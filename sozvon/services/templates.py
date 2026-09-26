"""Операции шаблонов согласованы с изменением default под общим lock."""
from sozvon.core.errors import ResourceConflict
from sozvon.storage import templates
from sozvon.templates.schema import TemplateSpec


def _open(service):
    if service._closed:
        raise ResourceConflict("Приложение завершается")


def create(service, spec):
    with service._lock:
        _open(service)
        return templates.create_template(service.repo, spec)


def copy(service, template_id, name=None):
    with service._lock:
        _open(service)
        original = templates.get_template(service.repo, template_id)
        value = original['spec']
        value['name'] = name if name is not None else value['name'][:72] + ' — копия'
        return templates.create_template(service.repo, TemplateSpec.model_validate(value))


def save(service, template_id, base_revision, spec):
    with service._lock:
        _open(service)
        return templates.save_template(service.repo, template_id, base_revision, spec)


def archive(service, template_id, base_revision):
    with service._lock:
        _open(service)
        default_id = service.settings.snapshot()['template']
        templates.archive_template(service.repo, template_id, base_revision, default_id)
        return {'archived': True}
