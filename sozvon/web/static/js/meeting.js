import { createExport } from './export.js';
import { request } from './api.js';
import { createTranscriptEditor } from './transcript-editor.js';
import { $, node, message, date, time, emptyState, activateTabs, bindDialog } from './dom.js';

import { isActiveJob as running } from './job-state.js';
const sections = [['summary', 'Кратко'], ['decisions', 'Решения'], ['proposals', 'Предложения'], ['tasks', 'Задачи'], ['questions', 'Открытые вопросы'], ['risks', 'Риски']];

export function createMeeting({ getSettings, getJob, reportError, refresh, isReady, guard }) {
  let current = null;
  createExport({ getMeeting: () => current });
  let sttCandidate = null;
  const sttDialog = bindDialog($('stt-confirm-dialog'), () => !launching);
  let notesDirty = false;
  let saving = false;
  let launching = false;
  let notesRevision = 0;
  let reportSignature = '';
  const editor = createTranscriptEditor({ request, showError: reportError, onSaved: data => update(data) });
  let sourcesSignature = '';

  function tab(value) { activateTabs('data-detail-tab', value); }
  function refreshActions() {
    if (!current) return;
    const settings = getSettings();
    const blocked = !isReady() || running(getJob()) || launching;
    const hasText = Boolean(current.segments?.length);
    const hasAudio = current.kind !== 'text';
    const cloud = settings?.stt?.engine === 'cloud';
    const sttReady = cloud ? Boolean(settings.stt.cloud?.endpoint && settings.stt.cloud?.model) : Boolean(settings?.stt?.model_path);
    $('transcribe-button').hidden = !hasAudio;
    $('transcribe-button').textContent = hasText ? 'Распознать заново' : 'Распознать аудио';
    $('transcribe-button').disabled = blocked || !sttReady;
    $('report-button').textContent = current.reports?.length ? 'Создать новый отчёт' : 'Создать отчёт';
    const configured = Boolean(settings?.llm?.base_url && settings?.llm?.model);
    $('report-button').disabled = blocked || !hasText || !configured;
    let reason = '';
    if (!isReady()) reason = 'Откройте приложение через ярлык.';
    else if (running(getJob()) || launching) reason = 'Дождитесь завершения текущей обработки.';
    else if (!settings) reason = 'Загрузка настроек… Если ожидание затянулось, откройте «Настройки».';
    else if (!hasText && hasAudio && !sttReady) reason = 'Укажите локальную модель в настройках распознавания.';
    else if (!hasText) reason = 'Для отчёта сначала нужна расшифровка.';
    else if (!configured) reason = 'Укажите адрес API и модель в настройках отчёта.';
    else if (hasAudio && !sttReady) reason = 'Повторное распознавание недоступно: не указан каталог модели.';
    $('action-reason').textContent = reason;
    $('report-button').title = $('report-button').disabled ? reason : '';
    $('transcribe-button').title = $('transcribe-button').disabled ? reason : '';
    $('save-notes').disabled = !notesDirty || saving || !isReady();
  }
  function jumpToSegment(id) {
    tab('transcript');
    const segment = [...$('transcript-content').children].find(element => element.dataset.segmentId === String(id));
    if (!segment) return;
    $('transcript-content').querySelectorAll('.highlight').forEach(element => element.classList.remove('highlight'));
    segment.classList.add('highlight');
    segment.focus();
  }
  function renderReport() {
    const reports = current.reports || [];
    const signature = JSON.stringify(reports);
    if (signature === reportSignature) return;
    reportSignature = signature;
    const container = $('report-content');
    container.replaceChildren();
    if (!reports.length) {
      container.append(emptyState('Отчёт ещё не создан', 'Когда расшифровка будет готова, нажмите «Создать отчёт». Решения и задачи появятся здесь вместе с цитатами из разговора.'));
      return;
    }
    const sorted = [...reports].sort((left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime());
    const report = sorted[sorted.length - 1];
    if (report.stale) container.append(node('p', 'notice', 'Отчёт устарел: расшифровка изменена. Создайте новую версию, для экспорта отчёта. Прежние источники доступны в истории.'));
    container.append(node('div', 'report-meta', `${date(report.created_at, true)} · ${report.model || 'Модель не указана'}${reports.length > 1 ? ` · Последний из ${reports.length} отчётов` : ''}`));
    let nonempty = false;
    const ordered = report.sections || sections.map(([key, title]) => ({ key, title, kind: key === 'tasks' ? 'tasks' : 'statements', items: report.document?.[key] }));
    ordered.forEach(({ title, kind, items }) => {
      if (!Array.isArray(items) || !items.length) return;
      nonempty = true;
      const section = node('section', 'report-section');
      section.append(node('h2', '', title));
      items.forEach(item => {
        const article = node('article', 'report-item');
        article.append(node('p', '', item.text));
        if (kind === 'tasks') {
          const meta = node('div', 'task-meta');
          meta.append(node('span', '', `Кто: ${item.owner || 'не указан'}`), node('span', '', `Срок: ${item.due || 'не указан'}`));
          article.append(meta);
        }
        if (Array.isArray(item.evidence) && item.evidence.length) {
          const toggle = node('button', 'evidence-toggle', `Основание · ${item.evidence.length}`);
          toggle.type = 'button';
          toggle.setAttribute('aria-expanded', 'false');
          const evidence = node('div', 'evidence-list');
          evidence.hidden = true;
          item.evidence.forEach(source => {
            const quote = node('blockquote', 'evidence-quote', source.quote);
            const jump = node('button', 'evidence-jump', 'Открыть в расшифровке ↗');
            jump.type = 'button';
            jump.disabled = Boolean(report.stale && !report.transcript_revision);
            if (report.stale) jump.title = 'Источник относится к предыдущей версии расшифровки';
            jump.addEventListener('click', () => {
              if (report.stale || !current.segments?.some(row => row.id === source.segment_id)) editor.openHistory(report.transcript_revision, source.segment_id);
              else jumpToSegment(source.segment_id);
            });
            quote.append(jump);
            evidence.append(quote);
          });
          toggle.addEventListener('click', () => {
            evidence.hidden = !evidence.hidden;
            toggle.setAttribute('aria-expanded', String(!evidence.hidden));
          });
          article.append(toggle, evidence);
        }
        section.append(article);
      });
      container.append(section);
    });
    if (!nonempty) container.append(emptyState('В отчёте нет пунктов', 'Модель не вернула содержательных пунктов. Исходный текст доступен во вкладке «Расшифровка».'));
  }

  function renderSources() {
    const sources = current.sources || [];
    const signature = JSON.stringify(sources);
    if (signature === sourcesSignature) return;
    sourcesSignature = signature;
    const container = $('sources-content');
    container.replaceChildren();
    if (!sources.length) {
      container.append(emptyState('Отдельных файлов нет', current.kind === 'text' ? 'Исходный текст сохранён во вкладке «Расшифровка».' : 'Файлы появятся здесь после сохранения источников.'));
      return;
    }
    sources.forEach(source => {
      const row = node('div', 'source-row');
      row.append(node('span', '', source.name));
      let url;
      try { url = new URL(source.url, location.origin); } catch { url = null; }
      if (source.url && url && url.origin === location.origin && ['http:', 'https:'].includes(url.protocol)) {
        const link = node('a', 'button quiet', 'Скачать ↓');
        link.href = url.href;
        link.download = '';
        row.append(link);
      } else row.append(node('span', 'muted', 'Ссылка на файл недоступна'));
      container.append(row);
    });
  }
  function render() {
    $('meeting-title').textContent = current.title || 'Без названия';
    const kind = { text: 'Текстовая запись', audio: 'Аудиофайл', recording: 'Запись разговора' }[current.kind] || 'Запись';
    $('meeting-meta').textContent = [kind, current.duration_ms ? time(current.duration_ms) : null].filter(Boolean).join(' · ');
    message('meeting-error', current.error || '');

    renderReport(); editor.update(current); renderSources(); refreshActions();
  }
  function show(data) {
    current = data;
    editor.show(data);
    notesDirty = false;
    notesRevision += 1;
    $('meeting-notes').value = current.notes || '';
    $('notes-status').textContent = 'Заметки сохраняются отдельно от отчёта.';
    reportSignature = sourcesSignature = '';
    render();
    tab(current.reports?.length ? 'report' : 'transcript');
  }
  function update(data) {
    if (!current || current.id !== data.id) return;
    current = data;
    if (!notesDirty && !saving && document.activeElement !== $('meeting-notes')) $('meeting-notes').value = data.notes || '';
    render();
  }
  $('meeting-notes').addEventListener('input', () => {
    notesRevision += 1;
    notesDirty = $('meeting-notes').value !== (current?.notes || '');
    $('notes-status').textContent = notesDirty ? 'Есть несохранённые изменения' : 'Без изменений';
    refreshActions();
  });
  $('notes-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!current || !notesDirty || saving) return;
    const id = current.id;
    const text = $('meeting-notes').value;
    const revision = notesRevision;
    saving = true;
    refreshActions();
    $('notes-status').textContent = 'Сохранение…';
    try {
      await request(`/api/meetings/${encodeURIComponent(id)}/notes`, { method: 'PUT', body: { text } });
      const verified = await request(`/api/meetings/${encodeURIComponent(id)}`);
      if (verified.notes !== text) throw new Error('Не удалось подтвердить сохранение заметок. Текст оставлен в редакторе.');
      if (current.id === id) {
        current.notes = verified.notes;
        if (notesRevision === revision) { notesDirty = false; $('notes-status').textContent = 'Заметки сохранены'; }
        else $('notes-status').textContent = 'Предыдущая версия сохранена. Есть новые изменения.';
      }
    } catch (error) {
      if (current.id === id) $('notes-status').textContent = error.message;
      reportError(error);
    } finally { saving = false; refreshActions(); }
  });
  async function launch(operation, body = {}) {
    if (!current || launching || running(getJob())) return;
    launching = true;
    message('global-error');
    refreshActions();
    try {
      const suffix = operation === 'report' ? '/report' : '/transcribe';
      await request(`/api/meetings/${encodeURIComponent(current.id)}${suffix}`, { method: 'POST', body });
      launching = false;
      sttDialog.close();
      await refresh();
    } catch (error) { if ($('stt-confirm-dialog').open) message('stt-confirm-error', error.message); else reportError(error); }
    finally { launching = false; refreshActions(); $('confirm-stt').disabled = false; }
  }
  $('transcribe-button').addEventListener('click', () => guard(async () => {
    launching = true; refreshActions();
    try {
      const settings = await request('/api/settings');
      const revision = current.transcript_revision ?? current.segments?.[0]?.revision ?? (current.segments?.length ? 1 : 0);
      sttCandidate = { base_revision: revision, replace_confirmed: false, cloud_confirmed_url: null };
      const cloud = settings.stt?.engine === 'cloud';
      if (!cloud && !revision) { launching = false; await launch('transcribe', sttCandidate); return; }
      sttCandidate.cloud_confirmed_url = cloud ? settings.stt.cloud.endpoint : null;
      $('stt-confirm-copy').textContent = cloud ? `Аудиозапись будет отправлена на ${sttCandidate.cloud_confirmed_url}. Сервис может списать средства. Локальные оригиналы сохранятся.` : 'Результат станет новой версией. Прежние правки сохранятся в истории.';
      $('stt-replace-label').hidden = !revision;
      $('stt-replace-confirmed').checked = false;
      $('confirm-stt').textContent = cloud ? 'Отправить и распознать' : 'Распознать заново';
      message('stt-confirm-error');
      launching = false; refreshActions(); $('transcribe-button').focus();
      sttDialog.open();
    } catch (error) { reportError(error); }
    finally { launching = false; refreshActions(); }
  }));
  $('cancel-stt').addEventListener('click', () => sttDialog.close());
  $('confirm-stt').addEventListener('click', () => {
    if (sttCandidate.base_revision && !$('stt-replace-confirmed').checked) {
      message('stt-confirm-error', 'Подтвердите создание новой версии расшифровки.'); return;
    }
    sttCandidate.replace_confirmed = $('stt-replace-confirmed').checked;
    $('confirm-stt').disabled = true;
    launch('transcribe', { ...sttCandidate });
  });
  $('report-button').addEventListener('click', () => guard(() => launch('report')));
  return {
    show, update, tab, refreshActions, busy: () => saving || launching || editor.busy(), id: () => current?.id, dirty: () => notesDirty || editor.dirty(),
    discard() { editor.discard(); notesDirty = false; notesRevision += 1; if (current) $('meeting-notes').value = current.notes || ''; refreshActions(); },
  };
}
