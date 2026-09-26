"""Только синтетические ответы для сквозной проверки транспорта, не качества модели."""
import io
import json
import threading
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import av


@contextmanager
def synthetic_api():
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            observed.append(self.path)
            if self.path == '/v1/audio/transcriptions':
                prefix = ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode()
                message = BytesParser(policy=policy.default).parsebytes(prefix + raw)
                parts = {p.get_param('name', header='content-disposition'): p for p in message.iter_parts()}
                audio = parts['file'].get_payload(decode=True)
                with av.open(io.BytesIO(audio)) as source:
                    assert source.streams.audio[0].codec_context.sample_rate == 16000
                    assert sum(frame.samples for frame in source.decode(audio=0)) > 0
                result = {'text': 'Синтетический текст: Анна подготовит инструкцию.'}
            elif self.path == '/v1/chat/completions':
                request = json.loads(raw)
                context = json.loads(request['messages'][1]['content'])
                segment = context['segments'][0]
                document = {key: [] for key in ('summary', 'decisions', 'proposals', 'tasks', 'questions', 'risks')}
                point = {'text': 'Синтетический пункт отчёта',
                         'evidence': [{'segment_id': segment['id'], 'quote': segment['text']}]}
                document['custom_sections'] = {}
                for section in context['template_snapshot']['spec']['sections']:
                    if not section['enabled']:
                        continue
                    item = dict(point)
                    if section['kind'] == 'tasks':
                        item.update(owner=None, due=None)
                    if section['key'].startswith('custom_'):
                        document['custom_sections'][section['key']] = {'kind': section['kind'], 'items': [item]}
                    else:
                        document[section['key']] = [item]
                result = {'choices': [{'finish_reason': 'stop', 'message': {
                    'content': json.dumps(document, ensure_ascii=False)}}]}
            else:
                self.send_error(404)
                return
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
