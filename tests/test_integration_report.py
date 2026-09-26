"""Проверить IPC -> HTTP -> валидация -> SQLite на явно синтетическом API."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sozvon.services.application import Application


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        context = json.loads(body['messages'][1]['content'])
        segment = context['segments'][0]
        document = {'summary': [{'text': 'Синтетический проверочный ответ.',
                                'evidence': [{'segment_id':segment['id'],'quote':segment['text']}]}],
                    'decisions':[], 'proposals':[], 'tasks':[], 'questions':[], 'risks':[]}
        response = json.dumps({'choices':[{'finish_reason':'stop','message':{
            'content':json.dumps(document,ensure_ascii=False)}}],
            'usage':{'prompt_tokens':12,'completion_tokens':14}}).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def test_actual_report_worker_http_and_persistence(tmp_path):
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever)
    thread.start()
    app=Application(tmp_path)
    try:
        app.settings.save({'llm':{'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'synthetic-test'}})
        mid=app.repo.create_text('Тест интеграции','Только синтетический текст для проверки.')
        jid=app.report(mid)
        assert app.wait(10)
        assert app.repo.job(jid)['status']=='succeeded',app.repo.job(jid)
        report=app.repo.detail(mid)['reports'][0]
        assert report['document']['summary'][0]['evidence'][0]['segment_id'] == app.repo.detail(mid)['segments'][0]['id']
        assert report['meta']['usage']['prompt_tokens']==12
    finally:
        app.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
