"""Сохранение черновика под блокировкой приложения и SQL CAS."""
from sozvon.core.errors import ResourceConflict
from sozvon.storage import transcripts


def _ensure_editable(service, mid):
    job = service.active(mid)
    if service._closed or (job and job["status"] in {"queued", "running", "cancelling"}):
        raise ResourceConflict("Дождитесь завершения обработки этой записи")


def read(service, mid, revision=None):
    if revision is None:
        item = service.repo.detail(mid)
        return {"revision": item["transcript_revision"], "segments": item["segments"]}
    return {"revision": revision, "segments": transcripts.read_revision(service.repo, mid, revision)}


def save(service, mid, request):
    with service._lock:
        _ensure_editable(service, mid)
        revision = transcripts.save_edits(service.repo, mid, request)
        return read(service, mid, revision)


def restore(service, mid, base_revision, target_revision):
    with service._lock:
        _ensure_editable(service, mid)
        revision = transcripts.restore_revision(service.repo, mid, base_revision, target_revision)
        return read(service, mid, revision)


def history(service, mid):
    return {"items": transcripts.list_revisions(service.repo, mid)}
