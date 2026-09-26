"""Родитель удаляет только производные конкретного STT после принудительной отмены."""
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sozvon.services.application import Application
from sozvon.services.requests import TranscribeInput


def test_forced_cloud_cancel_removes_job_staging_not_originals(tmp_path):
    received, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            received.set()
            release.wait(15)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service = Application(tmp_path)
    source = tmp_path / 'original.wav'
    with wave.open(str(source), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', ''))
        output.writeframes(b'\0\0' * 16000)
    original = source.read_bytes()
    unrelated = tmp_path / 'processing' / 'keep.txt'
    unrelated.parent.mkdir()
    unrelated.write_text('не удалять', encoding='utf-8')
    try:
        service.settings.save({'stt': {'engine': 'cloud', 'cloud': {
            'base_url': f'http://127.0.0.1:{server.server_port}/v1'}}})
        mid = service.repo.create('Синтетическая отмена', 'audio')
        service.repo.attach_source(mid, source, 'audio', 1000)
        endpoint = service.settings.public()['stt']['cloud']['endpoint']
        jid = service.transcribe(mid, TranscribeInput(base_revision=0, cloud_confirmed_url=endpoint))
        assert received.wait(10)
        service.stop(jid)
        assert service.wait(10)
        assert service.repo.job(jid)['status'] == 'cancelled'
        remaining = [p for p in (tmp_path / 'processing').rglob('*') if p.is_file() and p != unrelated]
        assert remaining == []
        assert source.read_bytes() == original
        assert unrelated.read_text(encoding='utf-8') == 'не удалять'
    finally:
        release.set()
        service.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
