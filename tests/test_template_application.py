import threading

from test_web import open_client

from sozvon.storage.templates import create_template, save_template
from sozvon.templates.builtin import builtin_spec
from sozvon.templates.schema import TemplateSpec


def test_report_keeps_selected_snapshot_during_edit(tmp_path):
    started, release = threading.Event(), threading.Event()
    calls = []

    class Worker:
        def run(self, operation, payload, **kwargs):
            calls.append(payload)
            started.set()
            release.wait(5)
            return {'document': {'summary': []}, 'model': 'synthetic',
                    'template_snapshot': payload.get('template_snapshot')}

    app, client, headers = open_client(tmp_path, Worker)
    service = app.state.service
    try:
        with client:
            service.settings.save({'llm': {'model': 'synthetic'}})
            spec = builtin_spec('meeting')
            spec.name = 'Первый шаблон'
            template = create_template(service.repo, spec)
            mid = service.repo.create_text('Тест', 'Синтетический текст')
            response = client.post(f'/api/meetings/{mid}/report', headers=headers,
                                   json={'template_id': template['id'], 'template_revision': 1})
            assert response.status_code == 202
            assert started.wait(3)
            changed = {**template['spec'], 'name': 'Изменённый шаблон'}
            save_template(service.repo, template['id'], 1, TemplateSpec.model_validate(changed))
            release.set()
            assert service.wait(3)
            assert calls[0]['template'] == template['id']
            assert calls[0]['template_snapshot']['revision'] == 1
            stored = service.repo.detail(mid)['reports'][0]['meta']['template_snapshot']
            assert stored['spec']['name'] == 'Первый шаблон'
            assert stored['revision'] == 1
            stale = client.post(f'/api/meetings/{mid}/report', headers=headers,
                                json={'template_id': template['id'], 'template_revision': 1})
            assert stale.status_code == 409
            assert len(calls) == 1
    finally:
        release.set()
        service.close()
