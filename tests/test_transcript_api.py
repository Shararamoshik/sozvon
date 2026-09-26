"""Редактор работает через защищённый API и сохраняет старую версию."""
from test_web import open_client


def test_transcript_edit_readback_and_conflict(tmp_path):
    app, client, headers = open_client(tmp_path)
    with client:
        mid = app.state.service.repo.create_text('План', 'Исходная фраза')
        old = client.get(f'/api/meetings/{mid}').json()
        body = {'base_revision': 1, 'edits': [{'id': old['segments'][0]['id'],
                                               'text': 'Исправленная фраза', 'speaker': 'Анна'}]}
        response = client.put(f'/api/meetings/{mid}/transcript', json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()['revision'] == 2
        current = client.get(f'/api/meetings/{mid}/transcript').json()
        assert current['segments'][0]['text'] == 'Исправленная фраза'
        assert current['segments'][0]['speaker'] == 'Анна'
        assert current['segments'][0]['start_ms'] is None
        original = client.get(f'/api/meetings/{mid}/transcript?revision=1').json()
        assert original['segments'][0]['text'] == 'Исходная фраза'
        conflict = client.put(f'/api/meetings/{mid}/transcript', json=body, headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()['current_revision'] == 2
        assert client.get(f'/api/meetings/{mid}/transcript').json() == current


def test_history_restore_validation_and_csrf(tmp_path):
    app, client, headers = open_client(tmp_path)
    with client:
        mid = app.state.service.repo.create_text('План', 'Первая версия')
        app.state.service.repo.save_segments(mid, [{'text': 'Вторая версия'}])
        url = f'/api/meetings/{mid}/transcript'
        history = client.get(url + '/revisions')
        assert history.status_code == 200
        assert [r['revision'] for r in history.json()['items']] == [2, 1]
        body = {'base_revision': 2, 'target_revision': 1}
        assert client.post(url + '/restore', json=body).status_code == 403
        restored = client.post(url + '/restore', json=body, headers=headers)
        assert restored.status_code == 200, restored.text
        assert restored.json()['revision'] == 3
        assert client.get(url).json()['segments'][0]['text'] == 'Первая версия'
        assert client.get(url + '?revision=2').json()['segments'][0]['text'] == 'Вторая версия'
        assert client.get(url + '?revision=999').status_code == 404
        invalid = client.post(url + '/restore', json={**body, 'base_revision': True}, headers=headers)
        assert invalid.status_code == 422
        foreign = client.put(url, json={'base_revision': 3, 'edits': [{'id': 'foreign', 'text': 'x'}]}, headers=headers)
        assert foreign.status_code == 422
