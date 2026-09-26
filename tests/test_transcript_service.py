import threading

from test_web import open_client


def test_same_meeting_edit_and_restore_blocked_while_other_meeting_allowed(tmp_path):
    started, release = threading.Event(), threading.Event()

    class Worker:
        def run(self, *args, **kwargs):
            started.set()
            release.wait(5)
            return {'document': {'summary': []}, 'model': 'synthetic'}

    app, client, headers = open_client(tmp_path, Worker)
    service = app.state.service
    try:
        with client:
            service.settings.save({'llm': {'model': 'synthetic'}})
            one = service.repo.create_text('Один', 'Первый')
            two = service.repo.create_text('Два', 'Второй')
            service.report(one)
            assert started.wait(3)
            for mid, status in [(one, 409), (two, 200)]:
                segment = service.repo.detail(mid)['segments'][0]
                response = client.put(f'/api/meetings/{mid}/transcript', headers=headers,
                                      json={'base_revision': 1, 'edits': [{'id': segment['id'], 'text': 'Правка'}]})
                assert response.status_code == status, response.text
            response = client.post(f'/api/meetings/{one}/transcript/restore', headers=headers,
                                   json={'base_revision': 1, 'target_revision': 1})
            assert response.status_code == 409
            assert service.repo.detail(one)['transcript_revision'] == 1
            release.set()
    finally:
        release.set()
        service.close()
