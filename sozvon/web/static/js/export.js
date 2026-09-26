import { $, message, bindDialog } from './dom.js';
import { ApiError } from './api.js';

export function createExport({ getMeeting }) {
  let meeting, report, busy = false;
  const dialog = bindDialog($('export-dialog'), () => !busy);
  function fields() {
    const archive = $('export-format').value === 'json';
    const isReport = $('export-content').value === 'report';
    $('export-archive-note').hidden = !archive;
    $('export-content').disabled = archive;
    $('export-include-transcript').disabled = archive || !isReport;
    $('export-include-quotes').disabled = archive || !isReport;
  }
  $('export-button').addEventListener('click', () => {
    meeting = getMeeting();
    report = [...(meeting.reports || [])].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))[0];
    $('export-content').value = report ? 'report' : 'transcript';
    $('export-content').querySelector('[value="report"]').disabled = !report;
    $('export-format').value = 'docx';
    $('export-include-transcript').checked = false;
    $('export-include-quotes').checked = true;
    $('export-status').textContent = '';
    message('export-error'); fields(); dialog.open();
  });
  $('export-format').addEventListener('change', fields);
  $('export-content').addEventListener('change', fields);
  $('close-export').addEventListener('click', () => dialog.close());
  $('download-export').addEventListener('click', async () => {
    if (busy) return;
    busy = true; $('download-export').disabled = true; message('export-error');
    $('export-status').textContent = 'Подготовка файла…';
    const format = $('export-format').value;
    const content = $('export-content').value;
    const params = new URLSearchParams({ format, content,
      include_transcript: content === 'report' && $('export-include-transcript').checked ? '1' : '0',
      include_quotes: $('export-include-quotes').checked ? '1' : '0' });
    if (content === 'report' && report) params.set('report_id', report.id);
    try {
      let response;
      try {
        response = await fetch(`/api/meetings/${encodeURIComponent(meeting.id)}/export?${params}`, {
          credentials: 'same-origin', cache: 'no-store', headers: { Accept: '*/*' },
        });
      } catch {
        throw new ApiError('Нет связи с приложением. Проверьте, что оно запущено, и повторите действие.');
      }
      if (!response.ok) {
        let payload; try { payload = await response.json(); } catch { payload = null; }
        throw new ApiError(typeof payload?.detail === 'string' ? payload.detail : `Ошибка экспорта ${response.status}`, response.status, payload);
      }
      const mime = response.headers.get('Content-Type') || '';
      const expected = { pdf: 'application/pdf', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', json: 'application/json', md: 'text/' }[format];
      if (!mime.startsWith(expected)) throw new Error('Сервер вернул неожиданный формат. Файл не скачан.');
      let blob;
      try { blob = await response.blob(); }
      catch { throw new ApiError('Передача файла прервалась. Проверьте связь с приложением и повторите экспорт.'); }
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = url;
      const filename = /filename="?([^";]+)"?/.exec(response.headers.get('Content-Disposition') || '')?.[1];
      link.download = filename && /^[a-zA-Z0-9_.-]+$/.test(filename) ? filename : `sozvon-${content}.${format}`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      $('export-status').textContent = 'Файл передан браузеру';
    } catch (error) {
      message('export-error', error.message || 'Не удалось скачать файл.');
      $('export-status').textContent = 'Файл не скачан';
    } finally { busy = false; $('download-export').disabled = false; }
  });
}
