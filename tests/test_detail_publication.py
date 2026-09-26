"""HTTP detail не смешивает старые данные с новым состоянием задания."""
import threading

from test_cloud_stt_application import source
from test_web import open_client


def test_detail_never_announces_success_with_prepublication_snapshot(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()

    class Worker:
        def run(self, *args, **kwargs):
            started.set()
            release.wait(5)
            return {'segments': [{'text': 'Готовый результат'}], 'warnings': []}

    app, client, headers = open_client(tmp_path, Worker)
    service = app.state.service
    try:
        with client:
            service.settings.save({'stt': {'engine': 'cloud', 'cloud': {'base_url': 'http://127.0.0.1:9999/v1'}}})
            mid = source(service)
            endpoint = service.settings.public()['stt']['cloud']['endpoint']
            assert client.post(f'/api/meetings/{mid}/transcribe', headers=headers,
                               json={'base_revision': 0, 'cloud_confirmed_url': endpoint}).status_code == 202
            assert started.wait(2)
            real_detail = service.repo.detail

            def racing_detail(value):
                snapshot = real_detail(value)
                release.set()
                service.wait(0.5)  # До исправления публикация завершается между detail и active.
                return snapshot

            monkeypatch.setattr(service.repo, 'detail', racing_detail)
            value = client.get(f'/api/meetings/{mid}').json()
            if value['job']['status'] == 'succeeded':
                assert value['segments'], 'Успех задания пришёл со снимком до публикации'
            monkeypatch.setattr(service.repo, 'detail', real_detail)
            assert service.wait(3)
            assert client.get(f'/api/meetings/{mid}').json()['segments'][0]['text'] == 'Готовый результат'
    finally:
        release.set()
        service.close()
