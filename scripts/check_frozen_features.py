"""Проверка исходного/упакованного процесса: только синтетические данные и loopback API."""
import argparse
import hashlib
import io
import json
import subprocess
import sys
import time
import wave
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
from docx import Document
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from features_http import synthetic_api


def wait_job(client, mid, jid):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        value = client.get(f'/api/meetings/{mid}').json()
        job = value['job']
        if job and job['id'] == jid and job['status'] not in {'queued', 'running', 'cancelling'}:
            assert job['status'] == 'succeeded', job
            return value
        time.sleep(0.1)
    raise AssertionError('Превышено время ожидания задания')


def checked(response, status=200):
    assert response.status_code == status, response.text[:400]
    return response.json()


def exercise(client, headers, api_url, folder):
    checked(client.put('/api/settings', headers=headers, json={
        'stt': {'engine': 'cloud', 'cloud': {'base_url': api_url, 'model': 'synthetic-stt'}},
        'llm': {'base_url': api_url, 'model': 'synthetic-report'}}))
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', ''))
        audio.writeframes(b'\0\0' * 16000)
    mid = checked(client.post('/api/import/audio', headers=headers,
                             files={'file': ('synthetic.wav', buffer.getvalue(), 'audio/wav')}))['id']
    endpoint = checked(client.get('/api/settings'))['stt']['cloud']['endpoint']
    jid = checked(client.post(f'/api/meetings/{mid}/transcribe', headers=headers,
                             json={'base_revision': 0, 'cloud_confirmed_url': endpoint}), 202)['job_id']
    item = wait_job(client, mid, jid)
    edit = {'base_revision': 1, 'edits': [{'id': item['segments'][0]['id'],
                                          'text': 'Синтетическая правка: проверка нового EXE.', 'speaker': 'Проверка'}]}
    checked(client.put(f'/api/meetings/{mid}/transcript', headers=headers, json=edit))
    assert client.put(f'/api/meetings/{mid}/transcript', headers=headers, json=edit).status_code == 409
    spec = checked(client.get('/api/templates/meeting'))['spec']
    spec['name'] = 'Синтетический шаблон EXE'
    key = 'custom_' + uuid4().hex
    spec['sections'].append({'key': key, 'title': 'Проверенный раздел', 'kind': 'tasks', 'enabled': True, 'instructions': ''})
    tid = checked(client.post('/api/templates', headers=headers, json={'spec': spec}), 201)['id']
    jid = checked(client.post(f'/api/meetings/{mid}/report', headers=headers,
                             json={'template_id': tid, 'template_revision': 1}), 202)['job_id']
    item = wait_job(client, mid, jid)
    assert item['reports'][0]['meta']['template_snapshot']['id'] == tid
    artifacts = []
    for fmt in ('docx', 'pdf'):
        result = client.get(f'/api/meetings/{mid}/export', params={
            'format': fmt, 'content': 'report', 'include_transcript': 1})
        assert result.status_code == 200, result.text[:300]
        path = folder / ('result.' + fmt)
        path.write_bytes(result.content)
        if fmt == 'docx':
            text = '\n'.join(p.text for p in Document(path).paragraphs)
        else:
            text = '\n'.join(p.extract_text() for p in PdfReader(path).pages)
        assert 'Проверенный раздел' in text and 'проверка нового EXE' in text
        artifacts.append({'file': str(path), 'bytes': path.stat().st_size})
    checked(client.post(f'/api/meetings/{mid}/transcript/restore', headers=headers,
                        json={'base_revision': 2, 'target_revision': 1}))
    assert client.get(f'/api/meetings/{mid}/export?format=pdf&content=report').status_code == 409
    assert client.get(f'/api/meetings/{mid}/export?format=pdf&content=transcript').status_code == 200
    archive = checked(client.get(f'/api/meetings/{mid}/export?format=json'))
    assert [v['revision'] for v in archive['transcript_history']] == [1, 2, 3]
    return {'meeting_id': mid, 'template_id': tid, 'artifacts': artifacts, 'revision': 3}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    folder = args.output or ROOT / 'artifacts' / ('features-process-' + str(time.time_ns()))
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    data = folder / 'data'
    command = [str(args.exe.resolve())] if args.exe else [sys.executable, '-m', 'sozvon']
    logfile = (folder / 'process.log').open('wb')
    process = subprocess.Popen(command + ['--no-browser', '--port', '0', '--data-dir', str(data)],
                               stdout=logfile, stderr=subprocess.STDOUT, cwd=ROOT)
    try:
        runtime = data / 'runtime.json'
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError('Процесс завершился; см. process.log')
            if runtime.exists():
                value = json.loads(runtime.read_text(encoding='utf-8'))
                if value['pid'] == process.pid:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError('Процесс не запустил HTTP')
        url = urlsplit(value['url'])
        base = f'{url.scheme}://{url.netloc}'
        with httpx.Client(base_url=base, trust_env=False, timeout=70) as client, synthetic_api() as (api_url, observed):
            health = checked(client.get('/api/health'))
            assert health['version'] == '0.2.0'
            assert client.get('/api/meetings').status_code == 401
            session = checked(client.post('/api/session', headers={'Origin': base},
                                          json={'key': parse_qs(url.fragment)['key'][0]}))
            headers = {'Origin': base, 'X-CSRF-Token': session['csrf']}
            result = exercise(client, headers, api_url, folder)
            assert observed == ['/v1/audio/transcriptions', '/v1/chat/completions']
            result.update(ok=True, synthetic=True, requests=observed, version=health['version'])
            if args.exe:
                result.update(exe=str(args.exe.resolve()), sha256=hashlib.sha256(args.exe.read_bytes()).hexdigest())
            report = folder / 'result.json'
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print('FEATURES_PROCESS_OK ' + str(report))
    finally:
        process.terminate()
        try:
            process.wait(15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)
        logfile.close()


if __name__ == '__main__':
    main()
