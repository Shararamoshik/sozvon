import { isActiveJob } from './job-state.js';
import { $, node, message, time, emptyState, bindDialog, date } from './dom.js';

export const transcriptRevision = meeting => meeting.transcript_revision ?? meeting.segments?.[0]?.revision ?? (meeting.segments?.length ? 1 : 0);

export function createTranscriptEditor({ request, showError, onSaved }) {
  let current, original = [], draft = [], base = 0, editing = false, saving = false, unresolvedSave = false;
  let signature = '';
  let matches = [], matchIndex = -1, replacements = [];
  const replaceDialog = bindDialog($('replace-dialog'));
  let historyTarget = null, historyBase = 0, historySerial = 0;
  const historyDialog = bindDialog($('history-dialog'), () => !saving);
  $('history-dialog').addEventListener('close', () => {
    if ($('history-dialog').open) return;
    historySerial += 1; historyTarget = null;
    $('history-list').replaceChildren(); $('history-content').replaceChildren();
    $('history-label').textContent = ''; $('history-restore').hidden = true;
  });
  const blocked = () => isActiveJob(current?.job) && current.job.meeting_id === current.id;
  const dirty = () => editing && (unresolvedSave || draft.some((row, i) => row.text !== original[i].text || row.speaker !== original[i].speaker));
  const path = () => `/api/meetings/${encodeURIComponent(current.id)}/transcript`;
  function controls() {
    $('replace-controls').hidden = !editing;
    $('preview-replace').disabled = !editing || !$('transcript-search').value;
    $('edit-transcript').hidden = editing;
    $('edit-transcript').disabled = !current?.segments?.length || blocked();
    $('transcript-savebar').hidden = !editing;
    $('save-transcript').disabled = !dirty() || saving || blocked();
    $('cancel-transcript').disabled = saving;
    if (editing) $('transcript-status').textContent = blocked() ? 'Сохранение доступно после завершения задания этой встречи.' : dirty() ? 'Есть несохранённые правки' : 'Без изменений';
  }
  function render() {
    const rows = editing ? draft : current.segments || [];
    const next = JSON.stringify([rows, editing]);
    if (signature === next) { controls(); return; }
    signature = next;
    const container = $('transcript-content');
    container.replaceChildren();
    if (!rows.length) container.append(emptyState('Пока без расшифровки', 'Запустите распознавание аудио.'));
    rows.forEach((row, index) => {
      const article = node('article', 'segment');
      article.tabIndex = -1;
      article.dataset.segmentId = row.id;
      const meta = node('div', 'segment-meta', row.start_ms == null ? `§ ${index + 1}` : `${time(row.start_ms)}–${time(row.end_ms)}`);
      if (editing) {
        const fields = node('div', 'segment-fields');
        const whoLabel = node('label', '', 'Говорящий');
        const who = node('input');
        who.maxLength = 80; who.value = row.speaker || ''; who.dataset.editSpeaker = row.id;
        who.addEventListener('input', () => { row.speaker = who.value.trim() || null; controls(); });
        whoLabel.append(who);
        const textLabel = node('label', '', `Реплика ${index + 1}`);
        const text = node('textarea');
        text.rows = 3; text.maxLength = 50000; text.required = true; text.value = row.text; text.dataset.editText = row.id;
        text.addEventListener('input', () => { row.text = text.value; search(); });
        textLabel.append(text); fields.append(whoLabel, textLabel); article.append(meta, fields);
      } else {
        if (row.speaker) meta.append(node('div', '', row.speaker));
        article.append(meta, node('p', '', row.text));
      }
      container.append(article);
    });
    search();
  }
  function discard() { $('transcript-conflict').hidden = true; message('transcript-error'); editing = false; unresolvedSave = false; draft = []; signature = ''; render(); $('transcript-status').textContent = 'Правки отменены'; }
  async function save() {
    if (!dirty() || saving || blocked()) return;
    if (draft.some(row => !row.text.trim())) { message('transcript-error', 'Реплика не может быть пустой.'); return; }
    const edits = draft.filter((row, i) => row.text !== original[i].text || row.speaker !== original[i].speaker)
      .map(({ id, text, speaker }) => ({ id, text, speaker: speaker || null }));
    saving = true; unresolvedSave = true; controls(); message('transcript-error');
    // Keep textarea DOM and native undo while the request is in flight.
    const submitted = JSON.stringify(draft);
    try {
      const result = await request(path(), { method: 'PUT', body: { base_revision: base, edits } });
      const verified = await request(`/api/meetings/${encodeURIComponent(current.id)}`);
      if (transcriptRevision(verified) !== result.revision) { $('transcript-conflict').hidden = false; throw new Error('Версия изменилась после сохранения. Черновик оставлен для сверки.'); }
      if (JSON.stringify(draft) !== submitted) { $('transcript-conflict').hidden = false; throw new Error('Предыдущие правки сохранены; новый ввод остался в черновике. Откройте новую версию для сверки.'); }
      current = verified; editing = false; unresolvedSave = false; signature = ''; render();
      $('transcript-status').textContent = `Версия ${result.revision} сохранена`;
      onSaved(verified);
    } catch (error) {
      if (error.status === 409) $('transcript-conflict').hidden = false;
      message('transcript-error', error.message); showError(error);
    }
    finally { saving = false; controls(); }
  }
  $('edit-transcript').addEventListener('click', () => {
    original = structuredClone(current.segments); draft = structuredClone(original);
    base = transcriptRevision(current); editing = true; signature = ''; render();
    $('transcript-content').querySelector('textarea')?.focus();
    $('transcript-content').querySelector('textarea')?.scrollIntoView({ block: 'center' });
  });
  function search() {
    const needle = $('transcript-search').value;
    matches = []; matchIndex = -1;
    (editing ? draft : current?.segments || []).forEach(row => {
      let offset = 0, found;
      while (needle && (found = row.text.indexOf(needle, offset)) >= 0) {
        matches.push({ id: row.id, offset: found }); offset = found + needle.length;
      }
    });
    $('search-count').textContent = needle ? `Совпадений: ${matches.length}` : '';
    $('transcript-next').disabled = !matches.length; controls();
  }
  $('transcript-search').addEventListener('input', search);
  $('transcript-next').addEventListener('click', () => {
    if (!matches.length) return;
    matchIndex = (matchIndex + 1) % matches.length;
    const match = matches[matchIndex];
    const article = [...$('transcript-content').children].find(item => item.dataset.segmentId === match.id);
    $('transcript-content').querySelectorAll('.highlight').forEach(item => item.classList.remove('highlight'));
    article?.classList.add('highlight');
    const field = article?.querySelector('textarea');
    if (field) { field.focus(); field.setSelectionRange(match.offset, match.offset + $('transcript-search').value.length); }
    else article?.focus();
  });
  $('preview-replace').addEventListener('click', () => {
    const needle = $('transcript-search').value;
    if (!editing || !needle) return;
    replacements = draft.filter(row => row.text.includes(needle)).map(row => ({ id: row.id, before: row.text, after: row.text.split(needle).join($('transcript-replacement').value) }));
    const preview = $('replace-preview'); preview.replaceChildren();
    const count = draft.reduce((sum, row) => sum + row.text.split(needle).length - 1, 0);
    preview.append(node('p', '', `Совпадений: ${count}`));
    replacements.forEach(row => { preview.append(node('p', 'muted', row.before), node('p', '', row.after)); });
    $('apply-replace').disabled = !count;
    replaceDialog.open();
  });
  $('cancel-replace').addEventListener('click', () => replaceDialog.close());
  $('apply-replace').addEventListener('click', () => {
    replaceDialog.close();
    replacements.forEach(row => {
      const field = [...$('transcript-content').querySelectorAll('textarea')].find(item => item.dataset.editText === row.id);
      if (!field || field.value !== row.before) return;
      // Chromium's native editing command preserves the textarea undo stack.
      field.focus(); field.select(); document.execCommand('insertText', false, row.after);
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    search();
  });
  async function historyVersion(revision, segmentId) {
    const serial = ++historySerial;
    const meetingId = current.id;
    historyTarget = null;
    $('history-restore').hidden = true;
    $('history-label').textContent = 'Загрузка версии…';
    try {
      const result = await request(`${path()}?revision=${revision}`);
      if (serial !== historySerial || meetingId !== current.id || !$('history-dialog').open) return;
      historyTarget = result.revision;
      $('history-label').textContent = `Версия ${result.revision}, только чтение`;
      $('history-content').replaceChildren();
      result.segments.forEach((row, index) => {
        const article = node('article', 'segment' + (row.id === segmentId ? ' highlight' : ''));
        article.tabIndex = -1; article.dataset.segmentId = row.id;
        article.append(node('div', 'segment-meta', row.start_ms == null ? `§ ${index + 1}` : time(row.start_ms)), node('p', '', `${row.speaker ? row.speaker + ': ' : ''}${row.text}`));
        $('history-content').append(article);
      });
      $('restore-confirmed').checked = false;
      $('history-restore').hidden = false;
      $('restore-transcript').disabled = dirty() || blocked();
      $('history-content').querySelector('.highlight')?.focus();
    } catch (error) { if (serial === historySerial && meetingId === current.id && $('history-dialog').open) message('history-error', error.message); }
  }
  async function openHistory(revision, segmentId) {
    const serial = ++historySerial;
    const meetingId = current.id;
    historyBase = transcriptRevision(current);
    historyTarget = null;
    $('history-list').replaceChildren(); $('history-content').replaceChildren();
    $('history-restore').hidden = true; message('history-error');
    historyDialog.open();
    if (revision) { await historyVersion(revision, segmentId); return; }
    try {
      const data = await request(`${path()}/revisions`);
      if (serial !== historySerial || meetingId !== current.id || !$('history-dialog').open) return;
      const origins = { import: 'Импорт', manual: 'Правка', restore: 'Восстановление', local_stt: 'Локальное STT', cloud_stt: 'Облачное STT', legacy: 'Прежняя версия' };
      $('history-label').textContent = data.items.length ? 'Выберите версию' : 'История пуста';
      data.items.forEach(item => {
        const button = node('button', 'button', `Версия ${item.revision} · ${origins[item.origin] || item.origin} · ${date(item.created_at, true)}${item.metadata?.timestamp_inferred ? ' (дата приблизительная)' : ''}`);
        button.type = 'button'; button.dataset.historyRevision = item.revision;
        button.addEventListener('click', () => historyVersion(item.revision));
        $('history-list').append(button);
      });
    } catch (error) { if (serial === historySerial && meetingId === current.id && $('history-dialog').open) message('history-error', error.message); }
  }
  $('transcript-history').addEventListener('click', () => openHistory());
  $('close-history').addEventListener('click', () => historyDialog.close());
  $('restore-transcript').addEventListener('click', async () => {
    if (saving || dirty() || blocked() || !historyTarget) return;
    if (!$('restore-confirmed').checked) { message('history-error', 'Подтвердите восстановление как новой версии.'); return; }
    saving = true; $('restore-transcript').disabled = true;
    try {
      const result = await request(`${path()}/restore`, { method: 'POST', body: { base_revision: historyBase, target_revision: historyTarget } });
      const verified = await request(`/api/meetings/${encodeURIComponent(current.id)}`);
      if (transcriptRevision(verified) !== result.revision) throw new Error('Не удалось подтвердить восстановление. Обновите историю.');
      current = verified; editing = false; signature = ''; render(); onSaved(verified);
      $('transcript-status').textContent = `Версия ${result.revision} сохранена`;
      saving = false; historyDialog.close();
    } catch (error) { message('history-error', error.message); }
    finally { saving = false; $('restore-transcript').disabled = dirty() || blocked(); }
  });
  $('transcript-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(draft.map(row => `${row.speaker ? row.speaker + ': ' : ''}${row.text}`).join('\n\n')); $('transcript-status').textContent = 'Черновик скопирован'; }
    catch { message('transcript-error', 'Буфер обмена недоступен. Скопируйте текст из полей редактора.'); }
  });
  $('transcript-open-current').addEventListener('click', async () => {
    try {
      const result = await request(`/api/meetings/${encodeURIComponent(current.id)}`);
      const preview = $('transcript-current-preview');
      preview.textContent = `Версия ${transcriptRevision(result)}, только чтение\n\n` + result.segments.map(row => row.text).join('\n\n');
      preview.hidden = false;
    } catch (error) { message('transcript-error', error.message); }
  });
  $('save-transcript').addEventListener('click', save);
  $('cancel-transcript').addEventListener('click', discard);
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && editing && dirty() && !$('panel-transcript').hidden && !$('meeting-view').hidden && !document.querySelector('dialog[open]')) {
      event.preventDefault(); save();
    }
  });
  return {
    show(meeting) {
      current = meeting; editing = false; unresolvedSave = false; signature = ''; historySerial += 1;
      $('transcript-conflict').hidden = true; $('transcript-current-preview').hidden = true;
      $('transcript-search').value = ''; $('transcript-replacement').value = '';
      $('transcript-status').textContent = ''; message('transcript-error'); render();
    },
    update(meeting) { current = meeting; if (!editing) render(); else controls(); },
    openHistory, dirty, discard, busy: () => saving, editing: () => editing,
  };
}
