"""Real SQLite → export service → real worker → independent readers."""

import importlib
import json
from io import BytesIO

import pytest
from docx import Document

from sozvon.services.application import Application


def test_real_docx_from_database_does_not_mutate_sources(tmp_path):
    export = importlib.import_module("sozvon.export.service")
    service = Application(tmp_path)
    mid = service.repo.create_text("Синтетическая встреча Ёж", "Анна & Илья: <условие>")
    source = tmp_path / "original.txt"
    source.write_text("Исходный файл", encoding="utf-8")
    service.repo.attach_source(mid, source, "text")
    before = service.repo.detail(mid)
    data, media, filename = export.export_meeting(service, mid, {"format": "docx"})
    text = "\n".join(p.text for p in Document(BytesIO(data)).paragraphs)
    assert "Синтетическая встреча Ёж" in text and "Анна & Илья: <условие>" in text
    assert media == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert filename == f"sozvon-{mid}-transcript.docx"
    assert service.repo.detail(mid) == before
    assert source.read_text(encoding="utf-8") == "Исходный файл"
    assert list((tmp_path / "exports" / "tmp").iterdir()) == []
    service.close()


def test_real_pdf_from_database_has_readable_cyrillic(tmp_path):
    from pypdf import PdfReader

    from sozvon.export.service import export_meeting

    service = Application(tmp_path)
    mid = service.repo.create_text("Синтетический PDF Ёж", "Литеральный <img src=x> & текст")
    data, media, filename = export_meeting(service, mid, {"format": "pdf"})
    assert data.startswith(b"%PDF-")
    assert media == "application/pdf"
    assert filename == f"sozvon-{mid}-transcript.pdf"
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)
    assert "Синтетический PDF Ёж" in text and "Литеральный <img src=x> & текст" in text
    assert list((tmp_path / "exports" / "tmp").iterdir()) == []
    service.close()


@pytest.mark.parametrize("fmt", ["md", "json"])
def test_text_formats_use_snapshot_without_worker(tmp_path, fmt):
    from sozvon.export.service import export_meeting

    def no_worker():
        pytest.fail("Text formats must not start a worker or LLM")

    service = Application(tmp_path, worker_factory=no_worker)
    mid = service.repo.create_text("Ёж", "Старая версия")
    old = service.repo.detail(mid)
    sid = old["segments"][0]["id"]
    service.repo.save_report(mid, {"document": {"summary": [
        {"text": "Сводка", "evidence": [{"segment_id": sid, "quote": "Старая версия"}]}]}})
    service.repo.save_segments(mid, [{"text": "Текущая версия"}])
    before = service.repo.detail(mid)
    data, media, name = export_meeting(service, mid, {"format": fmt, "content": "transcript",
                                                    "include_quotes": False})
    if fmt == "json":
        archive = json.loads(data)
        assert archive == before
        assert archive["transcript_history"][0]["segments"] == old["segments"]
        assert archive["reports"][0]["stale"]
        assert media == "application/json"
    else:
        text = data.decode("utf-8")
        assert text.startswith("# Ёж\n")
        assert "Текущая версия" in text and "Старая версия" not in text
        assert media == "text/markdown"
    assert name == f"sozvon-{mid}-{'archive' if fmt == 'json' else 'transcript'}.{fmt}"
    assert not (tmp_path / "exports").exists()
    service.close()


@pytest.mark.parametrize("fmt", ["md", "json", "docx", "pdf"])
def test_busy_export_fails_without_waiting_and_releases_after_success(tmp_path, fmt):
    from sozvon.core.errors import ResourceConflict
    from sozvon.export.service import export_meeting

    service = Application(tmp_path)
    mid = service.repo.create_text("Ёж", "Текст")
    assert service._export_lock.acquire(blocking=False)
    try:
        with pytest.raises(ResourceConflict, match="экспорт|Экспорт"):
            export_meeting(service, mid, {"format": fmt})
    finally:
        service._export_lock.release()
    assert export_meeting(service, mid, {"format": fmt})[0]
    assert service._export_lock.acquire(blocking=False)
    service._export_lock.release()
    service.close()


@pytest.mark.parametrize("fmt", ["md", "json", "docx", "pdf"])
def test_snapshot_over_eight_mib_rejected_before_worker(tmp_path, fmt):
    from sozvon.export import service as export

    service = Application(tmp_path, worker_factory=lambda: pytest.fail("No worker on large input"))
    mid = service.repo.create_text("Ёж", "Текст")
    with service.repo.connection() as conn:
        conn.execute("UPDATE meetings SET notes=? WHERE id=?", ("x" * (8 * 1024 * 1024), mid))
    with pytest.raises(export.ExportTooLarge):
        export.export_meeting(service, mid, {"format": fmt})
    assert service._export_lock.acquire(blocking=False)
    service._export_lock.release()
    assert not (tmp_path / "exports").exists()
    service.close()


@pytest.mark.parametrize("fault", ["name", "size", "signature", "oversize", "symlink"])
def test_worker_output_is_revalidated_and_private_job_removed(tmp_path, fault):
    from pathlib import Path

    from sozvon.export.service import ExportTooLarge, export_meeting

    outside = tmp_path / "untouched"
    outside.write_bytes(b"private outside file")

    class FaultyWorker:
        def run(self, operation, payload, **kwargs):
            # Deliberately invalid renderer boundary output, never claimed as a real document.
            assert operation == "render_export" and kwargs["timeout"] == 60
            assert set(payload) == {"job_dir", "format"}
            folder = Path(payload["job_dir"])
            assert set(folder.iterdir()) == {folder / "input.json"}
            target = folder / "output.docx"
            if fault == "symlink":
                target.symlink_to(outside)
            else:
                target.write_bytes(b"broken" if fault == "signature" else b"PK\x03\x04test")
                if fault == "oversize":
                    with target.open("r+b") as stream:
                        stream.truncate(32 * 1024 * 1024 + 1)
            return {"filename": "../untouched" if fault == "name" else target.name,
                    "size": -1 if fault == "size" else target.stat().st_size}

    service = Application(tmp_path, worker_factory=FaultyWorker)
    mid = service.repo.create_text("Ёж", "Текст")
    error = ExportTooLarge if fault == "oversize" else ValueError
    with pytest.raises(error):
        export_meeting(service, mid, {"format": "docx"})
    assert outside.read_bytes() == b"private outside file"
    assert list((tmp_path / "exports" / "tmp").iterdir()) == []
    assert service._export_lock.acquire(blocking=False)
    service._export_lock.release()
    service.close()


@pytest.mark.parametrize("options", [
    {"format": "html"}, {"format": []}, {"content": "all"}, {"include_quotes": "0"},
    {"include_transcript": 1}, {"report_id": "../../other"}, {"unknown": True},
])
def test_invalid_options_fail_before_database_or_worker(tmp_path, options):
    from sozvon.export.service import export_meeting

    service = Application(tmp_path)
    with pytest.raises(ValueError):
        export_meeting(service, "f" * 32, options)
    service.close()


@pytest.mark.parametrize("mid", ["../file", 'bad"\r\nX-Header: injection', "ёж", "A" * 32])
def test_identifier_is_validated_not_sanitized_before_lookup(tmp_path, mid):
    from sozvon.export.service import export_meeting

    service = Application(tmp_path)
    with pytest.raises(ValueError):
        export_meeting(service, mid, {"format": "md"})
    service.close()


@pytest.mark.parametrize("reason", ["request", "shutdown"])
def test_cancel_signal_reaps_real_worker_and_cleans_job(tmp_path, monkeypatch, reason):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from sozvon.export.service import export_meeting
    from sozvon.runtime import host
    from sozvon.runtime.protocol import WorkerError

    real_popen = host.subprocess.Popen
    children = []
    rendering = threading.Event()
    request_stop = threading.Event()

    def observe(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    class ObservedWorker(host.WorkerHost):
        def run(self, *args, **kwargs):
            def on_event(event):
                if event["type"] == "progress":
                    rendering.set()
            return super().run(*args, **kwargs, on_event=on_event)

    monkeypatch.setattr(host.subprocess, "Popen", observe)
    service = Application(tmp_path, worker_factory=lambda: ObservedWorker(stop_grace=.05))
    mid = service.repo.create_text("Отмена", "\n".join(["Длинная строка. " * 50] * 500))
    before = service.repo.detail(mid)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(export_meeting, service, mid, {"format": "pdf"},
                              cancel_event=request_stop)
        assert rendering.wait(5)
        if reason == "request":
            request_stop.set()
        else:
            service.close()
        with pytest.raises(WorkerError) as exc:
            pending.result(timeout=5)
        assert exc.value.code == "cancelled"
    assert len(children) == 1 and children[0].poll() is not None
    assert list((tmp_path / "exports" / "tmp").iterdir()) == []
    assert service.repo.detail(mid) == before
    assert service._export_lock.acquire(blocking=False)
    service._export_lock.release()
    service.close()


def test_json_uses_exactly_one_snapshot_even_if_database_changes_after_read(tmp_path, monkeypatch):
    from sozvon.export.service import export_meeting

    service = Application(tmp_path)
    mid = service.repo.create_text("Ёж", "До конкурентной правки")
    old = service.repo.detail(mid)
    original = service.repo.detail
    calls = []

    def read_then_edit(mid):
        calls.append(mid)
        snapshot = original(mid)
        service.repo.save_segments(mid, [{"text": "После конкурентной правки"}])
        return snapshot

    monkeypatch.setattr(service.repo, "detail", read_then_edit)
    data, _, _ = export_meeting(service, mid, {"format": "json"})
    assert json.loads(data) == old
    assert calls == [mid]
    assert original(mid)["transcript_revision"] == 2
    service.close()


@pytest.mark.parametrize("fmt", ["md", "json"])
def test_text_output_also_obeys_output_limit(tmp_path, monkeypatch, fmt):
    from sozvon.export import service as export

    service = Application(tmp_path)
    mid = service.repo.create_text("Ёж", "Непустой текст")
    monkeypatch.setattr(export, "MAX_OUTPUT_BYTES", 16)
    with pytest.raises(export.ExportTooLarge):
        export.export_meeting(service, mid, {"format": fmt})
    service.close()


def test_service_refuses_linked_temp_parent_before_writing_snapshot(tmp_path):
    from sozvon.export.service import export_meeting

    root, outside = tmp_path / "app", tmp_path / "outside"
    outside.mkdir()
    service = Application(root)
    (root / "exports").symlink_to(outside, target_is_directory=True)
    mid = service.repo.create_text("Ёж", "Приватный текст")
    with pytest.raises(ValueError, match="ссыл"):
        export_meeting(service, mid, {"format": "pdf"})
    assert list(outside.iterdir()) == []
    service.close()


def test_worker_output_limit_error_preserves_http_413_classification(tmp_path):
    import sys

    from sozvon.export.service import ExportTooLarge, export_meeting
    from sozvon.runtime.host import WorkerHost

    # Explicit fault injection in a real subprocess: no fake document is accepted.
    code = (
        "from sozvon.export import docx; "
        "docx.render_docx = lambda document, output: output.truncate(32 * 1024 * 1024 + 1); "
        "from sozvon.runtime.worker import main; raise SystemExit(main())"
    )
    service = Application(tmp_path, worker_factory=lambda: WorkerHost(command=[sys.executable, "-c", code]))
    mid = service.repo.create_text("Лимит", "Текст")
    with pytest.raises(ExportTooLarge):
        export_meeting(service, mid, {"format": "docx"})
    assert list((tmp_path / "exports" / "tmp").iterdir()) == []
    service.close()
