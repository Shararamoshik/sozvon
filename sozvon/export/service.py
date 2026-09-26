"""Bounded export from one detached repository snapshot."""

import json
import os
import re
import stat
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from sozvon.core.errors import ResourceConflict
from sozvon.export.document import build_document
from sozvon.export.json_export import render_json
from sozvon.export.markdown import render_markdown
from sozvon.runtime.protocol import WorkerError

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024


class ExportTooLarge(ValueError):
    """An export crosses the documented input or output limit (HTTP 413)."""


def _bounded_json(value, limit):
    output = bytearray()
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(value):
        encoded = chunk.encode("utf-8")
        if len(output) + len(encoded) > limit:
            raise ExportTooLarge("Документ слишком большой для экспорта")
        output.extend(encoded)
    return bytes(output)


DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class _StopSignal:
    """Read-only union: HTTP cancellation never sets the application shutdown event."""

    def __init__(self, shutdown, request):
        self.shutdown, self.request = shutdown, request

    def is_set(self):
        return self.shutdown.is_set() or (self.request is not None and self.request.is_set())

    def check(self):
        if self.is_set():
            raise WorkerError("cancelled", "Экспорт отменён")


def export_meeting(service, mid, options, *, cancel_event=None) -> tuple[bytes, str, str]:
    """Return a bounded file; optional request cancellation is ORed with shutdown."""
    _validate_options(mid, options)
    if not service._export_lock.acquire(blocking=False):
        raise ResourceConflict("Другой экспорт уже выполняется")
    try:
        stop = _StopSignal(service._export_stop, cancel_event)
        stop.check()
        result = _export_locked(service, mid, options, stop)
        stop.check()
        if len(result[0]) > MAX_OUTPUT_BYTES:
            raise ExportTooLarge("Готовый документ слишком большой")
        return result
    finally:
        service._export_lock.release()


def _export_locked(service, mid, options, stop):
    fmt = options.get("format", "md")
    item = service.repo.detail(mid)
    _bounded_json(item, MAX_SNAPSHOT_BYTES)
    if fmt == "json":
        return render_json(item), "application/json", f"sozvon-{mid}-archive.json"
    content = options.get("content", "auto")
    if content == "auto":
        content = "report" if item["reports"] or options.get("report_id") is not None else "transcript"
    document = build_document(item, content=content,
                              include_transcript=options.get("include_transcript", False),
                              include_quotes=options.get("include_quotes", True),
                              report_id=options.get("report_id"))
    if fmt == "md":
        return render_markdown(document), "text/markdown", f"sozvon-{mid}-{content}.md"
    folder = service.root / "exports" / "tmp"
    if any(p.is_symlink() or p.is_junction() for p in (folder, *folder.parents)):
        raise ValueError("Нужна локальная папка экспорта без ссылок")
    folder.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=folder) as job:
        job = Path(job)
        (job / "input.json").write_bytes(_bounded_json(asdict(document), MAX_SNAPSHOT_BYTES))
        try:
            result = service.worker().run("render_export", {"job_dir": str(job), "format": fmt},
                                          timeout=60, stop_event=stop)
        except WorkerError as exc:
            # The existing fixed worker protocol has a generic operation_error
            # code. Only its exact, known size errors acquire HTTP 413 semantics.
            if exc.code == "operation_error" and str(exc) in {
                "Готовый документ слишком большой", "Документ слишком большой для экспорта",
            }:
                raise ExportTooLarge(str(exc)) from None
            raise
        data = _read_output(job, fmt, result)
    media = DOCX_MEDIA if fmt == "docx" else "application/pdf"
    return data, media, f"sozvon-{mid}-{content}.{fmt}"


def _read_output(job, fmt, result):
    name = "output." + fmt
    if (not isinstance(result, dict) or set(result) != {"filename", "size"}
            or result["filename"] != name or type(result["size"]) is not int):
        raise ValueError("Некорректный результат экспорта")
    target = job / name
    expected = target.lstat()
    if not stat.S_ISREG(expected.st_mode) or expected.st_nlink != 1 or target.is_junction():
        raise ValueError("Нужен обычный файл экспорта без ссылок")
    if expected.st_size > MAX_OUTPUT_BYTES:
        raise ExportTooLarge("Готовый документ слишком большой")
    if expected.st_size != result["size"] or expected.st_size < 5:
        raise ValueError("Некорректный размер результата экспорта")
    with target.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(expected, opened) or opened.st_nlink != 1:
            raise ValueError("Файл экспорта изменился")
        data = stream.read(MAX_OUTPUT_BYTES + 1)
    if len(data) > MAX_OUTPUT_BYTES:
        raise ExportTooLarge("Готовый документ слишком большой")
    if len(data) != result["size"]:
        raise ValueError("Размер файла экспорта изменился")
    if not data.startswith(b"PK\x03\x04" if fmt == "docx" else b"%PDF-"):
        raise ValueError("Некорректная сигнатура документа")
    return data


def _validate_options(mid, options):
    if not isinstance(mid, str) or not re.fullmatch(r"[a-f0-9]{32}", mid):
        raise ValueError("Некорректный идентификатор записи")
    allowed = {"format", "content", "include_transcript", "include_quotes", "report_id"}
    if not isinstance(options, dict) or set(options) - allowed:
        raise ValueError("Некорректные параметры экспорта")
    for key, default, choices in (
        ("format", "md", ("md", "json", "docx", "pdf")),
        ("content", "auto", ("auto", "report", "transcript")),
    ):
        if options.get(key, default) not in choices:
            raise ValueError("Неизвестный формат или состав экспорта")
    for key in ("include_transcript", "include_quotes"):
        if key in options and type(options[key]) is not bool:
            raise ValueError("Неверный переключатель экспорта")
    rid = options.get("report_id")
    if rid is not None and (not isinstance(rid, str) or not re.fullmatch(r"[a-f0-9]{32}", rid)):
        raise ValueError("Некорректный идентификатор отчёта")
