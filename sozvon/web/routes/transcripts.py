"""Защита сессии применяется общим middleware; здесь — строгие тела команд."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from sozvon.core.errors import ResourceConflict, RevisionConflict
from sozvon.core.limits import MAX_REVISION
from sozvon.services import transcripts
from sozvon.services.requests import Command
from sozvon.transcripts.schema import TranscriptEdit


class RestoreInput(Command):
    base_revision: int = Field(ge=1, le=MAX_REVISION)
    target_revision: int = Field(ge=1, le=MAX_REVISION)


def create_router(service):
    router = APIRouter(prefix="/api/meetings/{mid}/transcript")

    @router.get("")
    def read(mid: str, revision: int | None = Query(default=None, ge=1, le=MAX_REVISION)):
        return transcripts.read(service, mid, revision)

    @router.put("")
    def save(mid: str, body: TranscriptEdit):
        try:
            return transcripts.save(service, mid, body)
        except (ResourceConflict, RevisionConflict):
            raise
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.get("/revisions")
    def history(mid: str):
        return transcripts.history(service, mid)

    @router.post("/restore")
    def restore(mid: str, body: RestoreInput):
        return transcripts.restore(service, mid, body.base_revision, body.target_revision)

    return router
