from test_web import open_client


def test_template_copy_edit_archive_api(tmp_path):
    _app, client, headers = open_client(tmp_path)
    with client:
        response = client.get('/api/templates')
        assert response.status_code == 200
        assert {t['id'] for t in response.json()['items']} == {'meeting', 'client', 'technical'}
        response = client.post('/api/templates/meeting/copy', json={'name': 'Мой шаблон'}, headers=headers)
        assert response.status_code == 201, response.text
        created = response.json()
        assert not created['builtin'] and created['id'] != 'meeting'
        assert created['spec']['name'] == 'Мой шаблон'
        spec = created['spec']
        spec['instructions'] = 'Только подтверждённые решения'
        response = client.put('/api/templates/' + created['id'], headers=headers,
                              json={'base_revision': 1, 'spec': spec})
        assert response.status_code == 200
        assert response.json()['revision'] == 2
        assert client.get('/api/templates/' + created['id']).json()['spec'] == spec
        assert client.put('/api/templates/' + created['id'], headers=headers,
                          json={'base_revision': 1, 'spec': spec}).status_code == 409
        assert client.put('/api/templates/meeting', headers=headers,
                          json={'base_revision': 1, 'spec': spec}).status_code == 409
        url = '/api/templates/' + created['id'] + '/archive'
        assert client.post(url, json={'base_revision': 2}).status_code == 403
        assert client.post(url, headers=headers, json={'base_revision': 2}).status_code == 200
        assert client.get('/api/templates/' + created['id']).json()['archived']
        assert created['id'] not in {t['id'] for t in client.get('/api/templates').json()['items']}


def test_create_default_and_archive_protection(tmp_path):
    _app, client, headers = open_client(tmp_path)
    with client:
        spec = client.get('/api/templates/meeting').json()['spec']
        spec['name'] = 'Собственный'
        response = client.post('/api/templates', json={'spec': spec}, headers=headers)
        assert response.status_code == 201, response.text
        tid = response.json()['id']
        selected = client.put('/api/settings', json={'template': tid}, headers=headers)
        assert selected.status_code == 200
        assert client.get('/api/settings').json()['template'] == tid
        assert client.post(f'/api/templates/{tid}/archive', json={'base_revision': 1}, headers=headers).status_code == 409
        assert client.put('/api/settings', json={'template': 'does-not-exist'}, headers=headers).status_code == 404
        assert client.get('/api/settings').json()['template'] == tid
        client.put('/api/settings', json={'template': 'meeting'}, headers=headers)
        assert client.post(f'/api/templates/{tid}/archive', json={'base_revision': 1}, headers=headers).status_code == 200
        assert client.put('/api/settings', json={'template': tid}, headers=headers).status_code == 409
        assert client.get('/api/settings').json()['template'] == 'meeting'


def test_invalid_default_template_is_validation_not_sql_error(tmp_path):
    _, client, headers = open_client(tmp_path)
    with client:
        for value in ([], {}, True, '', 'x' * 65):
            response = client.put('/api/settings', json={'template': value}, headers=headers)
            assert response.status_code == 422, response.text
        assert client.get('/api/settings').json()['template'] == 'meeting'
