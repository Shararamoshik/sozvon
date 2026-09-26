"""Облачный STT запускается только по подтверждённому снимку и публикуется с CAS."""
from test_web import open_client


def source(service):
    path = service.root / 'fixture.wav'
    path.write_bytes(b'synthetic')  # Worker ниже подмена; декодирование проверяет отдельный process-тест.
    mid = service.repo.create('Синтетическая запись', 'audio')
    service.repo.attach_source(mid, path, 'audio', 1000)
    return mid


def test_cloud_uses_separate_snapshot_without_local_model_and_persists(tmp_path, monkeypatch):
    import keyring
    secrets = {}
    monkeypatch.setattr(keyring, 'get_password', lambda service, account: secrets.get((service, account)))
    monkeypatch.setattr(keyring, 'set_password', lambda service, account, value: secrets.__setitem__((service, account), value))
    calls = []

    class Worker:
        def run(self, operation, payload, **kwargs):
            calls.append((operation, payload))
            return {'segments': [{'text': 'Облачная речь', 'start_ms': None, 'end_ms': None}],
                    'model': 'cloud-test', 'warnings': ['Без временных меток'], 'timed': False}

    app, client, headers = open_client(tmp_path, Worker)
    service = app.state.service
    with client:
        service.settings.save({'llm': {'api_key': 'synthetic-llm'}})
        service.settings.save({'stt': {'engine': 'cloud', 'model_path': '', 'cloud': {
            'base_url': 'https://speech.example.invalid/v1', 'model': 'cloud-test',
            'allow_remote': True, 'api_key': 'synthetic-stt'}}})
        mid = source(service)
        endpoint = service.settings.public()['stt']['cloud']['endpoint']
        response = client.post(f'/api/meetings/{mid}/transcribe', headers=headers,
                               json={'base_revision': 0, 'cloud_confirmed_url': endpoint})
        assert response.status_code == 202, response.text
        assert service.wait(3)
        assert service.repo.job(response.json()['job_id'])['status'] == 'succeeded'
        assert calls[0][0] == 'transcribe_cloud'
        assert calls[0][1]['api_key'] == 'synthetic-stt'
        current = client.get(f'/api/meetings/{mid}').json()
        assert current['segments'][0]['text'] == 'Облачная речь'
        assert current['status'] == 'partial'
        revision = client.get(f'/api/meetings/{mid}/transcript/revisions').json()['items'][0]
        assert revision['origin'] == 'cloud_stt'
        with service.repo.connection() as conn:
            snapshot = conn.execute('SELECT snapshot FROM jobs').fetchone()[0]
        assert 'synthetic-stt' not in snapshot and 'synthetic-llm' not in snapshot


def test_missing_or_changed_consent_never_starts_worker(tmp_path, monkeypatch):
    import keyring
    monkeypatch.setattr(keyring, 'get_password', lambda *args: 'synthetic-key')

    def forbidden():
        raise AssertionError('Worker не должен запускаться')

    app, client, headers = open_client(tmp_path, forbidden)
    service = app.state.service
    with client:
        service.settings.save({'stt': {'engine': 'cloud', 'cloud': {
            'base_url': 'https://speech.example.invalid/v1', 'allow_remote': True}}})
        mid = source(service)
        url = f'/api/meetings/{mid}/transcribe'
        for body in ({}, {'base_revision': 0}, {'base_revision': 0, 'cloud_confirmed_url': 'https://old.invalid/v1/audio/transcriptions'}):
            assert client.post(url, json=body, headers=headers).status_code == 409
        service.settings.save({'stt': {'cloud': {'allow_remote': False}}})
        endpoint = service.settings.public()['stt']['cloud']['endpoint']
        assert client.post(url, json={'base_revision': 0, 'cloud_confirmed_url': endpoint}, headers=headers).status_code == 409
        assert service.active() is None


def test_late_stt_cannot_overwrite_new_revision(tmp_path):
    import threading
    started, release = threading.Event(), threading.Event()

    class Worker:
        def run(self, *args, **kwargs):
            started.set()
            release.wait(5)
            return {'segments': [{'text': 'Поздний ответ'}], 'warnings': []}

    app, client, headers = open_client(tmp_path, Worker)
    service = app.state.service
    try:
        with client:
            service.settings.save({'stt': {'engine': 'cloud', 'cloud': {'base_url': 'http://127.0.0.1:9999/v1'}}})
            mid = source(service)
            endpoint = service.settings.public()['stt']['cloud']['endpoint']
            response = client.post(f'/api/meetings/{mid}/transcribe', headers=headers,
                                   json={'base_revision': 0, 'cloud_confirmed_url': endpoint})
            assert response.status_code == 202
            assert started.wait(3)
            service.repo.save_segments(mid, [{'text': 'Независимо сохранённый текст'}])
            release.set()
            assert service.wait(3)
            assert service.repo.job(response.json()['job_id'])['status'] == 'failed'
            assert service.repo.detail(mid)['segments'][0]['text'] == 'Независимо сохранённый текст'
    finally:
        release.set()
        service.close()


def test_application_refuses_reflected_stt_key_before_storage(tmp_path, monkeypatch):
    import keyring
    monkeypatch.setattr(keyring, 'get_password', lambda *args: 'synthetic-current-stt-key')

    class Worker:
        def run(self, operation, payload, **kwargs):
            return {'segments': [{'text': 'Отражённый ключ: ' + payload['api_key']}], 'warnings': []}

    app, client, headers = open_client(tmp_path, Worker)
    service = app.state.service
    with client:
        service.settings.save({'stt': {'engine': 'cloud', 'cloud': {'base_url': 'http://127.0.0.1:9999/v1'}}})
        mid = source(service)
        endpoint = service.settings.public()['stt']['cloud']['endpoint']
        response = client.post(f'/api/meetings/{mid}/transcribe', headers=headers,
                               json={'base_revision': 0, 'cloud_confirmed_url': endpoint})
        assert response.status_code == 202
        assert service.wait(3)
        detail = client.get(f'/api/meetings/{mid}')
        assert detail.json()['segments'] == []
        assert detail.json()['job']['status'] == 'failed'
        assert 'synthetic-current-stt-key' not in detail.text
