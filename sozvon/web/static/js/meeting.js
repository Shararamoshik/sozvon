import { request } from './api.js';
import { $, node, message, date, time, emptyState, activateTabs } from './dom.js';

const running = job => job && !['done', 'completed', 'succeeded', 'success', 'failed', 'error', 'cancelled', 'canceled', 'stopped', 'interrupted'].includes(job.status);
const sections = [['summary', 'Кратко'], ['decisions', 'Решения'], ['proposals', 'Предложения'], ['tasks', 'Задачи'], ['questions', 'Открытые вопросы'], ['risks', 'Риски']];

export function createMeeting({ getSettings, getJob, reportError, refresh, isReady }) {
  let current = null;
  let notesDirty = false;
  let saving = false;
  let launching = false;
  let notesRevision = 0;
  let reportSignature = '';
  let transcriptSignature = '';
  let sourcesSignature = '';

  function tab(value) { activateTabs('data-detail-tab', value); }
  function refreshActions() {
    if (!current) return;
    const settings = getSettings();
    const blocked = !isReady() || running(getJob()) || launching;
    const hasText = Boolean(current.segments?.length);
    const hasAudio = current.kind !== 'text';
    $('transcribe-button').hidden = !hasAudio;
    $('transcribe-button').textContent = hasText ? 'Распознать заново' : 'Распознать аудио';
    $('transcribe-button').disabled = blocked || !settings?.stt?.model_path;
    $('report-button').textContent = current.reports?.length ? 'Создать новый отчёт' : 'Создать отчёт';
    const configured = Boolean(settings?.llm?.base_url && settings?.llm?.model);
    $('report-button').disabled = blocked || !hasText || !configured;
    let reason = '';
    if (!isReady()) reason = 'Откройте приложение через ярлык.';
    else if (running(getJob()) || launching) reason = 'Дождитесь завершения текущей обработки.';
    else if (!settings) reason = 'Загрузка настроек… Если ожидание затянулось, откройте «Настройки».';
    else if (!hasText && hasAudio && !settings.stt?.model_path) reason = 'Укажите локальную модель в настройках распознавания.';
    else if (!hasText) reason = 'Для отчёта сначала нужна расшифровка.';
    else if (!configured) reason = 'Укажите адрес API и модель в настройках отчёта.';
    else if (hasAudio && !settings.stt?.model_path) reason = 'Повторное распознавание недоступно: не указан каталог модели.';
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
    if (report.stale) container.append(node('p', 'notice', 'Отчёт устарел: расшифровка изменена. Создайте новую версию, чтобы проверить источники и экспортировать Markdown.'));
    container.append(node('div', 'report-meta', `${date(report.created_at, true)} · ${report.model || 'Модель не указана'}${reports.length > 1 ? ` · Последний из ${reports.length} отчётов` : ''}`));
    let nonempty = false;
    sections.forEach(([key, title]) => {
      const items = report.document?.[key];
      if (!Array.isArray(items) || !items.length) return;
      nonempty = true;
      const section = node('section', 'report-section');
      section.append(node('h2', '', title));
      items.forEach(item => {
        const article = node('article', 'report-item');
        article.append(node('p', '', item.text));
        if (key === 'tasks') {
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
            jump.disabled = Boolean(report.stale);
            if (report.stale) jump.title = 'Источник относится к предыдущей версии расшифровки';
            jump.addEventListener('click', () => jumpToSegment(source.segment_id));
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
  function renderTranscript() {
    const segments = current.segments || [];
    const signature = JSON.stringify(segments);
    if (signature === transcriptSignature) return;
    transcriptSignature = signature;
    const container = $('transcript-content');
    container.replaceChildren();
    if (!segments.length) {
      container.append(emptyState('Пока без расшифровки', current.kind === 'text' ? 'В этой записи нет текстовых фрагментов.' : 'Запустите распознавание аудио. Для этого потребуется локальная модель.'));
      return;
    }
    [...segments].sort((a, b) => (a.ordinal ?? 0) - (b.ordinal ?? 0)).forEach((segment, index) => {
      const article = node('article', 'segment');
      article.tabIndex = -1;
      article.dataset.segmentId = String(segment.id);
      const meta = node('div', 'segment-meta');
      meta.append(node('div', '', current.kind === 'text' ? `§ ${index + 1}` : time(segment.start_ms)));
      if (segment.speaker) meta.append(node('div', '', segment.speaker));
      article.append(meta, node('p', '', segment.text));
      container.append(article);
    });
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
    const base = `/api/meetings/${encodeURIComponent(current.id)}/export`;
    const latest = [...(current.reports || [])].sort((a,b) => new Date(b.created_at) - new Date(a.created_at))[0];
    if (latest?.stale) {
      $('export-md').removeAttribute('href');
      $('export-md').setAttribute('aria-disabled', 'true');
      $('export-md').title = 'Сначала создайте актуальную версию отчёта';
    } else {
      $('export-md').href = `${base}?format=md`;
      $('export-md').removeAttribute('aria-disabled');
      $('export-md').title = '';
    }
    $('export-json').href = `${base}?format=json`;
    renderReport(); renderTranscript(); renderSources(); refreshActions();
  }
  function show(data) {
    current = data;
    notesDirty = false;
    notesRevision += 1;
    $('meeting-notes').value = current.notes || '';
    $('notes-status').textContent = 'Заметки сохраняются отдельно от отчёта.';
    reportSignature = transcriptSignature = sourcesSignature = '';
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
  async function launch(operation) {
    if (!current || launching || running(getJob())) return;
    launching = true;
    message('global-error');
    refreshActions();
    try {
      const suffix = operation === 'report' ? '/report' : '/transcribe';
      await request(`/api/meetings/${encodeURIComponent(current.id)}${suffix}`, { method: 'POST', body: {} });
      await refresh();
    } catch (error) { reportError(error); }
    finally { launching = false; refreshActions(); }
  }
  $('transcribe-button').addEventListener('click', () => launch('transcribe'));
  $('report-button').addEventListener('click', () => launch('report'));
  return {
    show, update, tab, refreshActions, id: () => current?.id, dirty: () => notesDirty,
    discard() { notesDirty = false; notesRevision += 1; if (current) $('meeting-notes').value = current.notes || ''; refreshActions(); },
  };
}
