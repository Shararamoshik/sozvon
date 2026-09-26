"""Настоящие FastAPI → worker → loopback HTTP → БД → экспорт."""
import io
import wave
from uuid import uuid4

from docx import Document
from features_http import synthetic_api
from pypdf import PdfReader
from test_web import open_client


def test_audio_cloud_edit_template_report_and_documents(tmp_path):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', ''))
        audio.writeframes(b'\0\0' * 16000)
    app, client, headers = open_client(tmp_path)
    service = app.state.service
    with client, synthetic_api() as (url, observed):
        settings = client.put('/api/settings', headers=headers, json={
            'stt': {'engine': 'cloud', 'cloud': {'base_url': url, 'model': 'synthetic-stt'}},
            'llm': {'base_url': url, 'model': 'synthetic-report'}})
        assert settings.status_code == 200, settings.text
        response = client.post('/api/import/audio', headers=headers,
                               files={'file': ('synthetic.wav', buffer.getvalue(), 'audio/wav')})
        assert response.status_code == 200, response.text
        mid = response.json()['id']
        endpoint = client.get('/api/settings').json()['stt']['cloud']['endpoint']
        response = client.post(f'/api/meetings/{mid}/transcribe', headers=headers,
                               json={'base_revision': 0, 'cloud_confirmed_url': endpoint})
        assert response.status_code == 202, response.text
        assert service.wait(20)
        assert service.repo.job(response.json()['job_id'])['status'] == 'succeeded'
        transcript = client.get(f'/api/meetings/{mid}/transcript').json()
        assert transcript['revision'] == 1
        assert transcript['segments'][0]['start_ms'] is None
        edit = {'base_revision': 1, 'edits': [{'id': transcript['segments'][0]['id'],
                                              'text': 'Исправлено: Борис подготовит инструкцию.', 'speaker': 'Борис'}]}
        response = client.put(f'/api/meetings/{mid}/transcript', headers=headers, json=edit)
        assert response.status_code == 200
        assert response.json()['revision'] == 2
        assert client.put(f'/api/meetings/{mid}/transcript', headers=headers, json=edit).status_code == 409
        spec = client.get('/api/templates/meeting').json()['spec']
        spec['name'] = 'Синтетический шаблон'
        key = 'custom_' + uuid4().hex
        spec['sections'].append({'key': key, 'title': 'Особые договорённости',
                                 'kind': 'tasks', 'enabled': True, 'instructions': ''})
        response = client.post('/api/templates', headers=headers, json={'spec': spec})
        assert response.status_code == 201, response.text
        tid = response.json()['id']
        response = client.post(f'/api/meetings/{mid}/report', headers=headers,
                               json={'template_id': tid, 'template_revision': 1})
        assert response.status_code == 202, response.text
        assert service.wait(20)
        assert service.repo.job(response.json()['job_id'])['status'] == 'succeeded'
        detail = client.get(f'/api/meetings/{mid}').json()
        assert detail['reports'][0]['meta']['template_snapshot']['id'] == tid
        assert detail['reports'][0]['document']['custom_sections'][key]['items'][0]['owner'] is None
        for fmt in ('docx', 'pdf'):
            response = client.get(f'/api/meetings/{mid}/export?format={fmt}&content=report&include_transcript=1')
            assert response.status_code == 200, response.text[:300] if response.status_code != 200 else ''
            if fmt == 'docx':
                text = '\n'.join(p.text for p in Document(io.BytesIO(response.content)).paragraphs)
            else:
                text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(response.content)).pages)
            assert 'Особые договорённости' in text
            assert 'Борис подготовит' in text
            assert 'Не назначен' in text
        assert observed == ['/v1/audio/transcriptions', '/v1/chat/completions']
        # Правка шаблона не меняет уже сохранённый отчёт.
        spec['sections'][-1]['title'] = 'Новое название'
        changed = client.put('/api/templates/' + tid, headers=headers,
                             json={'base_revision': 1, 'spec': spec})
        assert changed.status_code == 200
        stable = client.get(f'/api/meetings/{mid}/export?format=pdf&content=report')
        assert stable.status_code == 200
        stable_text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(stable.content)).pages)
        assert 'Особые договорённости' in stable_text and 'Новое название' not in stable_text
        current = client.get(f'/api/meetings/{mid}/transcript').json()
        response = client.put(f'/api/meetings/{mid}/transcript', headers=headers,
                              json={'base_revision': 2, 'edits': [{'id': current['segments'][0]['id'],
                                'text': 'Третья версия для проверки устаревания', 'speaker': 'Борис'}]})
        assert response.status_code == 200
        for fmt in ('md', 'docx', 'pdf'):
            assert client.get(f'/api/meetings/{mid}/export?format={fmt}&content=report').status_code == 409
        transcript_pdf = client.get(f'/api/meetings/{mid}/export?format=pdf&content=transcript')
        assert transcript_pdf.status_code == 200
        text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(transcript_pdf.content)).pages)
        assert 'Третья версия' in text
        assert client.get(f'/api/meetings/{mid}/transcript?revision=1').json()['segments'][0]['text'].startswith('Синтетический')
    # Повторное открытие того же корня не теряет историю или шаблон.
    _again, next_client, _ = open_client(tmp_path)
    with next_client:
        stored = next_client.get(f'/api/meetings/{mid}').json()
        assert stored['transcript_revision'] == 3
        assert stored['reports'][0]['stale']
        assert next_client.get('/api/templates/' + tid).json()['revision'] == 2
        assert [r['revision'] for r in stored['transcript_history']] == [1, 2, 3]
