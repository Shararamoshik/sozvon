"""Обрыв тела скачивания после HTTP 200 не выдаётся за готовый документ."""
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import test_ui_cloud_stt as ui
from test_ui_cloud_stt import open_meeting

browser = ui.browser
ui_server = ui.ui_server
feature_page = ui.feature_page


@pytest.mark.parametrize('fmt,mime,prefix', [
    ('docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', b'PK\x03\x04'),
    ('pdf', 'application/pdf', b'%PDF-'),
])
def test_export_body_disconnect_is_localized(feature_page, fmt, mime, prefix):
    page, _ = feature_page
    open_meeting(page)
    requested, failures, downloads = [], [], []

    class TruncatedBody(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            requested.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', '4096')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(prefix)
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
            self.close_connection = True

    server = ThreadingHTTPServer(('127.0.0.1', 0), TruncatedBody)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        target = f'http://127.0.0.1:{server.server_port}/broken'
        page.route('**/api/meetings/synthetic/export?*', lambda route: route.fulfill(
            status=307, headers={'Location': target}))
        page.on('requestfailed', lambda request: failures.append(request.failure))
        page.on('download', lambda download: downloads.append(download))
        page.locator('#export-button').click()
        page.locator('#export-format').select_option(fmt)
        page.locator('#download-export').click()
        page.locator('#export-error').wait_for(state='visible')
        assert requested == ['/broken']
        assert any('CONTENT_LENGTH_MISMATCH' in error for error in failures), failures
        assert page.locator('#export-error').inner_text() == (
            'Передача файла прервалась. Проверьте связь с приложением и повторите экспорт.')
        assert page.locator('#export-status').inner_text() == 'Файл не скачан'
        assert not downloads
        assert not page.locator('#download-export').is_disabled()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
