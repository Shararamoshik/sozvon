import { request, startSession, onUnauthorized } from './api.js';
import { $, node, message, date, time, emptyState, activateTabs, wireTabs, bindDialog } from './dom.js';
import { createSettings } from './settings.js';
import { createMeeting } from './meeting.js';
import { createImport } from './import.js';

const state = { ready: false, view: 'library', items: [], job: null, refreshing: false, revision: 0 };
let listSignature = '';
let pendingNavigation = null;
let pollTimer;
const leaveDialog = bindDialog($('leave-dialog'));
const settings = createSettings({ changed: () => meeting.refreshActions(), reportError });
const meeting = createMeeting({
  getSettings: () => settings.value(), getJob: () => state.job,
  reportError, refresh: () => refresh(), isReady: () => state.ready,
});
const importer = createImport({
  isReady: () => state.ready, getJob: () => state.job,
  openMeeting: id => openMeeting(id), refresh: () => refresh(),
  openSettings: () => navigate('settings', 'recording'), reportError,
});

function reportError(error) { message('global-error', error.message || String(error)); }

function isActive(job) {
  return Boolean(job && !['done', 'completed', 'succeeded', 'success', 'failed', 'error', 'cancelled', 'canceled', 'stopped', 'interrupted'].includes(job.status));
}

function guard(action) {
  if ((state.view === 'meeting' && meeting.dirty()) || (state.view === 'settings' && settings.dirty())) {
    pendingNavigation = action;
    leaveDialog.open();
  } else action();
}

function changeView(view) {
  state.view = view;
  for (const name of ['library', 'meeting', 'settings', 'about']) $(name + '-view').hidden = name !== view;
  document.querySelectorAll('[data-view]').forEach(button => {
    const active = button.dataset.view === (view === 'meeting' ? 'library' : view);
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  $('breadcrumb').textContent = { library: 'Рабочая библиотека', meeting: 'Библиотека / Запись', settings: 'Настройки приложения', about: 'О приложении' }[view];
  $('main').focus({ preventScroll: true });
}

function navigate(view, category) {
  guard(() => {
    state.revision += 1;
    changeView(view);
    if (view === 'settings') {
      if (category) settings.tab(category);
      if (!settings.value() && state.ready) settings.load();
    }
    if (view === 'library' && state.ready) refresh(true);
  });
}

async function loadMeeting(id) {
  const revision = ++state.revision;
  message('global-error');
  try {
    const data = await request(`/api/meetings/${encodeURIComponent(id)}`);
    if (revision !== state.revision) return;
    meeting.show(data);
    changeView('meeting');
    showJob(data.job || state.job);
  } catch (error) { if (revision === state.revision) reportError(error); }
}
function openMeeting(id) { guard(() => loadMeeting(id)); }

function renderLibrary() {
  const query = $('meeting-search').value.toLocaleLowerCase('ru').trim();
  const items = state.items.filter(item => String(item.title).toLocaleLowerCase('ru').includes(query));
  $('library-count').textContent = String(state.items.length);
  $('list-caption').textContent = query ? `Найдено: ${items.length}` : `Всего: ${state.items.length}`;
  const signature = JSON.stringify([items, query, state.job?.id, state.job?.status, state.job?.stage]);
  if (signature === listSignature) return;
  listSignature = signature;
  const list = $('meeting-list');
  list.replaceChildren();
  if (!items.length) {
    if (query) list.append(emptyState('Ничего не найдено', 'Попробуйте другое название или очистите поиск.'));
    else {
      const empty = emptyState('Разговоры, к которым можно вернуться', 'Добавьте текст встречи, загрузите аудиофайл или начните запись.', { label: 'Добавить первую запись', run: () => importer.open() });
      list.append(empty);
    }
    return;
  }
  const kinds = { text: 'Текст', audio: 'Аудиофайл', recording: 'Запись' };
  const statuses = { imported: 'Добавлено', ready: 'Готово', pending: 'Ожидание', queued: 'В очереди', processing: 'Обработка', transcribing: 'Распознавание', reporting: 'Создание отчёта', recording: 'Идёт запись', running: 'Обработка', completed: 'Готово', done: 'Готово', failed: 'Ошибка', error: 'Ошибка', cancelled: 'Отменено', canceled: 'Отменено', transcribed: 'Распознано' };
  items.forEach(item => {
    const button = node('button', 'meeting-row');
    button.type = 'button';
    button.dataset.meetingId = item.id;
    const title = node('span');
    title.append(node('span', 'meeting-row-title', item.title || 'Без названия'));
    const meta = [kinds[item.kind] || 'Материал'];
    if (item.duration_ms !== null && item.duration_ms !== undefined) meta.push(time(item.duration_ms));
    if (item.segment_count) meta.push(`Фрагментов: ${item.segment_count}`);
    title.append(node('span', 'meeting-row-meta', meta.join(' · ')));
    const active = isActive(state.job) && state.job.meeting_id === item.id;
    const label = item.error ? 'Ошибка' : active ? (state.job.stage || 'Обработка')
      : item.report_count ? 'Есть отчёт' : item.segment_count ? 'Есть текст' : statuses[item.status] || item.status || 'Добавлено';
    const status = node('span', `status-badge${item.error ? ' error' : active ? ' working' : ''}`, label);
    button.append(title, status, node('span', 'row-date', date(item.created_at)));
    button.addEventListener('click', () => openMeeting(item.id));
    list.append(button);
  });
}

function showJob(job) {
  state.job = job;
  const active = isActive(job);
  $('job-banner').hidden = !active;
  if (job?.error) message('global-error', job.error);
  if (active) {
    const recording = job.operation === 'record';
    $('job-stage').textContent = job.stage || (recording ? 'Идёт запись' : 'Обработка');
    $('stop-job').textContent = recording ? 'Остановить и сохранить' : 'Отменить обработку';
    $('stop-job').disabled = !state.ready || ['stopping', 'cancelling'].includes(job.status);
    const progress = job.progress;
    const parts = [];
    if (progress?.completed !== undefined && progress?.total !== undefined) parts.push(`${progress.completed} / ${progress.total} ${progress.unit || ''}`.trim());
    if (progress?.duration_ms !== undefined) parts.push(time(progress.duration_ms));
    if (progress?.duration !== undefined) parts.push(`${progress.duration} с`);
    $('job-progress').textContent = parts.join(' · ') || (recording ? 'Сохранение после остановки' : 'Можно продолжать работу с библиотекой');
    const meter = $('job-meter');
    if (Number(progress?.total) > 0 && Number.isFinite(Number(progress?.completed))) {
      meter.max = Number(progress.total);
      meter.value = Math.max(0, Math.min(Number(progress.completed), Number(progress.total)));
    } else meter.removeAttribute('value');
  }
  meeting.refreshActions();
  importer.refreshActions();
}

async function refresh(explicit = false) {
  if (!state.ready || state.refreshing) return;
  state.refreshing = true;
  if (explicit) message('global-error');
  const revision = state.revision;
  try {
    const data = await request('/api/meetings');
    if (!Array.isArray(data.items)) throw new Error('Не удалось прочитать список записей.');
    state.items = data.items;
    showJob(data.job);
    renderLibrary();
    if (state.view === 'meeting' && meeting.id()) {
      const currentId = meeting.id();
      const detail = await request(`/api/meetings/${encodeURIComponent(currentId)}`);
      if (state.view === 'meeting' && revision === state.revision && currentId === meeting.id()) meeting.update(detail);
    }
  } catch (error) { reportError(error); }
  finally { state.refreshing = false; }
}

$('stay-editing').addEventListener('click', () => { pendingNavigation = null; leaveDialog.close(); });
$('discard-editing').addEventListener('click', () => {
  if (state.view === 'settings') settings.discard();
  if (state.view === 'meeting') meeting.discard();
  const action = pendingNavigation;
  pendingNavigation = null;
  leaveDialog.close();
  action?.();
});
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => navigate(button.dataset.view)));
document.querySelector('.brand').addEventListener('click', event => { event.preventDefault(); navigate('library'); });
$('back-to-library').addEventListener('click', () => navigate('library'));
$('refresh-library').addEventListener('click', () => refresh(true));
$('meeting-search').addEventListener('input', renderLibrary);
$('new-recording').addEventListener('click', () => guard(() => importer.open()));
$('stop-job').addEventListener('click', async () => {
  const id = state.job?.id;
  if (!id) return;
  $('stop-job').disabled = true;
  message('global-error');
  try {
    await request(`/api/jobs/${encodeURIComponent(id)}/stop`, { method: 'POST', body: {} });
    await refresh();
  } catch (error) { reportError(error); }
  finally { if (state.job?.id === id) showJob(state.job); }
});
wireTabs('data-detail-tab', value => meeting.tab(value));
wireTabs('data-settings-tab', value => settings.tab(value));
wireTabs('data-new-tab', value => activateTabs('data-new-tab', value));
window.addEventListener('beforeunload', event => {
  if (meeting.dirty() || settings.dirty() || importer.busy() || isActive(state.job)) { event.preventDefault(); event.returnValue = ''; }
});
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
onUnauthorized(() => {
  state.ready = false;
  clearTimeout(pollTimer);
  $('new-recording').disabled = true;
  $('refresh-library').disabled = true;
  $('settings-fields').disabled = true;
  $('save-settings').disabled = true;
  message('session-status', 'Откройте приложение через ярлык. Эта сессия недоступна.');
  meeting.refreshActions();
});

async function poll() {
  if (!state.ready) return;
  if (!document.hidden) await refresh();
  if (state.ready) pollTimer = setTimeout(poll, isActive(state.job) ? 1800 : 6000);
}
async function bootstrap() {
  try {
    await startSession();
    state.ready = true;
    message('session-status');
    $('new-recording').disabled = false;
    $('refresh-library').disabled = false;
    await Promise.all([refresh(), settings.load()]);
    if (state.ready) pollTimer = setTimeout(poll, 3000);
  } catch (error) {
    message('session-status', error.message);
    $('meeting-list').replaceChildren(emptyState('Библиотека пока недоступна', 'Запустите «Созвон» через ярлык, чтобы открыть защищённую локальную сессию.'));
  }
}
bootstrap();
