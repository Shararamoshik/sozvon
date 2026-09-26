import { request } from './api.js';
import { $, message, bindDialog, activateTabs } from './dom.js';

export function createImport({ isReady, getJob, openMeeting, refresh, openSettings, reportError }) {
  let submitting = false;
  const dialog = bindDialog($('new-dialog'), () => !submitting);
  const forms = ['new-text-form', 'new-audio-form', 'record-form'].map($);
  function activeJob() {
    const job = getJob();
    return job && !['done', 'completed', 'succeeded', 'success', 'failed', 'error', 'cancelled', 'canceled', 'stopped'].includes(job.status);
  }
  function refreshActions() {
    forms.forEach(form => form.querySelectorAll('button[type=submit]').forEach(button => {
      button.disabled = !isReady() || submitting || (form.id === 'record-form' && activeJob());
    }));
    $('close-new').disabled = submitting;
    $('open-record-settings').disabled = submitting;
    $('record-availability').textContent = activeJob() ? 'Сначала завершите текущую обработку или запись.' : 'Устройства выбираются в разделе «Настройки → Запись».';
  }
  async function submit(form, action) {
    if (submitting || !isReady() || !form.reportValidity()) return;
    submitting = true;
    refreshActions();
    message('new-error');
    form.setAttribute('aria-busy', 'true');
    try {
      const result = await action();
      if (!result.id) throw new Error('Приложение не вернуло идентификатор записи. Обновите библиотеку перед повторной попыткой.');
      await refresh();
      submitting = false;
      dialog.close();
      form.reset();
      await openMeeting(result.id);
    } catch (error) {
      message('new-error', error.message);
      if (!$('new-dialog').open) reportError(error);
    } finally {
      submitting = false;
      form.removeAttribute('aria-busy');
      $('upload-status').textContent = '';
      refreshActions();
    }
  }
  $('new-text-form').addEventListener('submit', event => {
    event.preventDefault();
    if (!$('new-title').value.trim() || !$('new-text').value.trim()) {
      message('new-error', 'Введите название и непустой текст встречи.');
      return;
    }
    submit(event.currentTarget, () => request('/api/import/text', {
      method: 'POST', body: { title: $('new-title').value.trim(), text: $('new-text').value.trim() },
    }));
  });
  $('new-audio-form').addEventListener('submit', event => {
    event.preventDefault();
    const file = $('audio-file').files[0];
    if (!file) { message('new-error', 'Сначала выберите аудиофайл.'); return; }
    submit(event.currentTarget, () => {
      const data = new FormData();
      data.append('file', file);
      $('upload-status').textContent = 'Загрузка и проверка файла…';
      return request('/api/import/audio', { method: 'POST', body: data });
    });
  });
  $('record-form').addEventListener('submit', event => {
    event.preventDefault();
    if (activeJob()) { message('new-error', 'Дождитесь завершения текущей обработки.'); return; }
    submit(event.currentTarget, () => request('/api/record/start', {
      method: 'POST', body: { allow_partial: $('allow-partial').checked, max_seconds: Number($('record-seconds').value) },
    }));
  });
  $('close-new').addEventListener('click', () => dialog.close());
  $('open-record-settings').addEventListener('click', () => { dialog.close(); openSettings(); });
  return {
    busy: () => submitting, refreshActions,
    open() {
      if (!isReady()) return;
      message('new-error');
      activateTabs('data-new-tab', 'text');
      refreshActions();
      dialog.open();
      $('new-title').focus();
    },
  };
}
