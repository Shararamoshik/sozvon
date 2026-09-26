"""Синтетический сервер проверяет транспорт/IPC, не качество настоящей DeepSeek."""
import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_web import open_client

from sozvon.runtime.host import WorkerHost


@contextmanager
def report_api():
    state = {'reason': 'length', 'requests': []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append({'path': self.path, 'body': body})
            context = json.loads(body['messages'][1]['content'])
            segment = context['segments'][0]
            document = {key: [] for key in ('summary', 'decisions', 'proposals', 'tasks', 'questions', 'risks')}
            document['summary'] = [{'text': 'Синтетический результат.', 'evidence': [
                {'segment_id': segment['id'], 'quote': segment['text']}]}]
            reply = {'choices': [{'finish_reason': state['reason'], 'message': {
                'content': json.dumps(document, ensure_ascii=False),
                'reasoning_content': 'Синтетическое закрытое рассуждение: не сохранять.'}}],
                'usage': {'completion_tokens': 4096, 'completion_tokens_details': {'reasoning_tokens': 3072}}}
            raw = json.dumps(reply, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_budget_failure_manual_retry_and_preserved_report(tmp_path):
    exe = os.environ.get('SOZVON_TEST_EXE')
    factory = (lambda: WorkerHost(command=[exe, '--worker'])) if exe else None
    app, client, headers = open_client(tmp_path, factory)
    service = app.state.service
    with client, report_api() as (url, state):
        response = client.put('/api/settings', headers=headers, json={'llm': {
            'base_url': url, 'model': 'deepseek/deepseek-v4.1-flash',
            'max_output_tokens': 4096, 'timeout_s': 300}})
        assert response.status_code == 200
        mid = client.post('/api/import/text', headers=headers, json={
            'title': 'Синтетическая проверка', 'text': 'Обсудили план на неделю.'}).json()['id']

        def run_report():
            response = client.post(f'/api/meetings/{mid}/report', headers=headers, json={})
            assert response.status_code == 202
            assert service.wait(25)
            return service.repo.job(response.json()['job_id'])

        job = run_report()
        assert job['status'] == 'failed'
        assert 'length' in job['error'] and '4096' in job['error'] and '3072' in job['error']
        assert 'лимит' in job['error'].lower()
        assert len(state['requests']) == 1
        with service.repo.connection() as conn:
            snapshot = json.loads(conn.execute('SELECT snapshot FROM jobs WHERE id=?', (job['id'],)).fetchone()[0])
        assert snapshot['max_output_tokens'] == 4096 and snapshot['timeout'] == 300
        assert 'api_key' not in snapshot and 'segments' not in snapshot
        first = client.get(f'/api/meetings/{mid}').json()
        assert first['reports'] == [] and first['transcript_revision'] == 1
        segments = first['segments']
        response = client.put('/api/settings', headers=headers, json={'llm': {
            'max_output_tokens': 24576, 'timeout_s': 450}})
        assert response.status_code == 200
        current = client.get('/api/settings').json()['llm']
        assert current['max_output_tokens'] == 24576 and current['timeout_s'] == 450
        # Только явное второе нажатие; приложение не повторяет предыдущую ошибку.
        state['reason'] = 'stop'
        assert run_report()['status'] == 'succeeded'
        detail = client.get(f'/api/meetings/{mid}').json()
        assert len(detail['reports']) == 1
        assert detail['reports'][0]['meta']['usage']['reasoning_tokens'] == 3072
        assert detail['segments'] == segments
        for fmt in ('docx', 'pdf'):
            response = client.get(f'/api/meetings/{mid}/export?format={fmt}&content=report')
            assert response.status_code == 200
            assert response.content.startswith(b'PK' if fmt == 'docx' else b'%PDF')
        saved = detail['reports']
        state['reason'] = 'content_filter'
        assert run_report()['status'] == 'failed'
        after = client.get(f'/api/meetings/{mid}').json()
        assert after['reports'] == saved and after['segments'] == segments
        assert 'Синтетическое закрытое рассуждение' not in json.dumps(after, ensure_ascii=False)
        assert [r['path'] for r in state['requests']] == ['/v1/chat/completions'] * 3
        assert [r['body']['max_tokens'] for r in state['requests']] == [4096, 24576, 24576]
        assert all(r['body']['model'] == 'deepseek/deepseek-v4.1-flash' for r in state['requests'])
