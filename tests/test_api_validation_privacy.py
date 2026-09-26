"""422 не возвращает исходные поля: среди них могут быть текст и ключи."""
from test_web import open_client


def test_validation_response_does_not_echo_private_input(tmp_path):
    _, client, headers = open_client(tmp_path)
    with client:
        response = client.post('/api/import/text', headers=headers,
                               json={'text': {'private': 'synthetic-private-input'}})
        assert response.status_code == 422
        assert 'synthetic-private-input' not in response.text
        assert isinstance(response.json()['detail'], str)


def test_static_modules_require_revalidation_after_application_upgrade(tmp_path):
    _, client, _ = open_client(tmp_path)
    with client:
        response = client.get('/static/js/api.js')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-cache'
