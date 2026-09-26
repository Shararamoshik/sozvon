"""Шаблоны отчёта: неизменяемые встроенные и версии пользовательских."""
from fastapi import APIRouter, HTTPException
from pydantic import Field, ValidationError

from sozvon.core.errors import ResourceConflict, RevisionConflict
from sozvon.core.limits import MAX_REVISION
from sozvon.services import templates as service_templates
from sozvon.services.requests import Command
from sozvon.storage import templates
from sozvon.templates.schema import TemplateSpec


class CopyInput(Command):
    name: str | None = Field(default=None, min_length=1, max_length=80)


class RevisionInput(Command):
    base_revision: int = Field(ge=1, le=MAX_REVISION)


class CreateInput(Command):
    spec: TemplateSpec


class SaveInput(RevisionInput):
    spec: TemplateSpec


def _valid_call(function, *args):
    try:
        return function(*args)
    except (ResourceConflict, RevisionConflict):
        raise
    except ValidationError:
        raise HTTPException(422, 'Проверьте название и разделы шаблона') from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


def create_router(service):
    router = APIRouter(prefix='/api/templates')

    @router.get('')
    def list_items():
        return {'items': templates.list_templates(service.repo)}

    @router.post('', status_code=201)
    def create_item(body: CreateInput):
        return _valid_call(service_templates.create, service, body.spec)

    @router.get('/{template_id}')
    def get_item(template_id: str):
        return templates.get_template(service.repo, template_id)

    @router.post('/{template_id}/copy', status_code=201)
    def copy_item(template_id: str, body: CopyInput | None = None):
        return _valid_call(service_templates.copy, service, template_id, body.name if body else None)

    @router.put('/{template_id}')
    def save_item(template_id: str, body: SaveInput):
        return _valid_call(service_templates.save, service, template_id, body.base_revision, body.spec)

    @router.post('/{template_id}/archive')
    def archive_item(template_id: str, body: RevisionInput):
        return service_templates.archive(service, template_id, body.base_revision)

    return router
