"""Builder checks against synthetic meetings persisted in real SQLite."""

import pytest

from sozvon.core.errors import ResourceConflict
from sozvon.export import document
from sozvon.storage.repository import Repository


def test_auto_without_report_builds_current_transcript(tmp_path):
    repo = Repository(tmp_path)
    mid = repo.create_text("Синтетический Ёж", "Первый абзац\nВторой абзац")
    result = document.build_document(repo.detail(mid))
    assert result.title == "Синтетический Ёж"
    assert [(b.kind, b.text) for b in result.blocks] == [
        ("h2", "Расшифровка"), ("meta", "[Абзац 1]"), ("p", "Первый абзац"),
        ("meta", "[Абзац 2]"), ("p", "Второй абзац"),
    ]


def saved_report(repo, mid, *, snapshot=None):
    sid = repo.detail(mid)["segments"][0]["id"]
    result = {"document": {"tasks": [{"text": "Обновить инструкцию", "owner": None,
               "due": None, "evidence": [{"segment_id": sid, "quote": "Первый абзац"}]}]}}
    if snapshot:
        result["template_snapshot"] = snapshot
    repo.save_report(mid, result)
    return repo.detail(mid)["reports"][0]["id"]


def test_report_contains_task_unknowns_and_source_without_invented_time(tmp_path):
    repo = Repository(tmp_path)
    mid = repo.create_text("Синтетический Ёж", "Первый абзац")
    saved_report(repo, mid)
    with repo.connection() as conn:
        conn.execute("UPDATE reports SET created_at=? WHERE meeting_id=?",
                     ("2026-09-26T12:34:00+00:00", mid))
    result = document.build_document(repo.detail(mid))
    assert document.Block("p", "Обновить инструкцию") in result.blocks
    assert document.Block("meta", "Исполнитель: Не назначен · Срок: Не указан") in result.blocks
    assert document.Block("quote", "[Абзац 1] Первый абзац") in result.blocks
    assert document.Block("meta", "Отчёт создан: 26.09.2026 12:34 UTC") in result.blocks
    assert document.Block("h2", "Расшифровка") not in result.blocks


def test_stale_report_conflicts_but_current_transcript_exports(tmp_path):
    repo = Repository(tmp_path)
    mid = repo.create_text("Ёж", "Первый абзац")
    saved_report(repo, mid)
    repo.save_segments(mid, [{"text": "Новая версия"}])
    with pytest.raises(ResourceConflict, match="устарел"):
        document.build_document(repo.detail(mid))
    current = document.build_document(repo.detail(mid), content="transcript")
    assert document.Block("p", "Новая версия") in current.blocks
    assert all("Первый абзац" not in b.text for b in current.blocks)


def test_quote_opt_out_retains_source_references(tmp_path):
    repo = Repository(tmp_path)
    mid = repo.create_text("Ёж", "Первый абзац")
    saved_report(repo, mid)
    result = document.build_document(repo.detail(mid), include_quotes=False)
    assert document.Block("meta", "[Абзац 1] Источник") in result.blocks
    assert all(b.kind != "quote" and "Первый абзац" not in b.text for b in result.blocks)


@pytest.mark.parametrize("case, error", [
    ("empty", ValueError), ("report_missing", KeyError), ("report_foreign", KeyError),
    ("source_missing", ValueError), ("content_invalid", ValueError),
])
def test_invalid_document_selection_fails_explicitly(tmp_path, case, error):
    repo = Repository(tmp_path)
    mid = repo.create("Пустая запись", "audio") if case == "empty" else repo.create_text("Ёж", "Первый абзац")
    options = {}
    if case == "report_missing":
        options["content"] = "report"
    if case in {"report_foreign", "source_missing"}:
        saved_report(repo, mid)
    if case == "report_foreign":
        options["report_id"] = "f" * 32
    if case == "content_invalid":
        options["content"] = "nonsense"
    item = repo.detail(mid)
    if case == "source_missing":
        item["reports"][0]["document"]["tasks"][0]["evidence"][0]["segment_id"] = "foreign"
    with pytest.raises(error):
        document.build_document(item, **options)


def test_custom_snapshot_order_and_selected_report_not_current_template(tmp_path):
    from copy import deepcopy

    from sozvon.storage.templates import create_template, save_template
    from sozvon.templates.builtin import builtin_spec
    from sozvon.templates.schema import TemplateSpec

    repo = Repository(tmp_path)
    spec = builtin_spec("meeting").model_dump()
    custom_key = "custom_" + "a" * 32
    spec["sections"] = [{"key": custom_key, "title": "Особые задачи", "kind": "tasks",
                         "enabled": True}, *reversed(spec["sections"])]
    template = create_template(repo, TemplateSpec.model_validate(spec))
    mid = repo.create_text("Ёж", "Первый абзац")
    sid = repo.detail(mid)["segments"][0]["id"]
    snapshot = {k: template[k] for k in ("id", "revision", "spec")}
    repo.save_report(mid, {"template_snapshot": snapshot, "document": {"custom_sections": {
        custom_key: {"kind": "tasks", "items": [{"text": "Собрать особый план", "owner": None,
            "due": None, "evidence": [{"segment_id": sid, "quote": "Первый абзац"}]}]}}}})
    selected = repo.detail(mid)["reports"][0]["id"]
    newer = deepcopy(spec)
    newer["sections"][0]["title"] = "Поздний заголовок"
    save_template(repo, template["id"], 1, TemplateSpec.model_validate(newer))
    saved_report(repo, mid)
    result = document.build_document(repo.detail(mid), report_id=selected, include_transcript=True)
    headings = [b.text for b in result.blocks if b.kind == "h2"]
    assert headings == [s["title"] for s in spec["sections"]] + ["Расшифровка"]
    assert document.Block("p", "Собрать особый план") in result.blocks
    assert document.Block("p", "Обновить инструкцию") not in result.blocks
    assert document.Block("p", "Первый абзац") in result.blocks


def test_audio_anchors_use_stored_time_and_speaker(tmp_path):
    repo = Repository(tmp_path)
    mid = repo.create("Аудио", "audio")
    repo.save_segments(mid, [{"text": "Первый абзац", "start_ms": 3_723_400,
                              "end_ms": 3_724_000, "speaker": "Анна"}])
    result = document.build_document(repo.detail(mid))
    assert document.Block("meta", "[01:02:03] · Анна") in result.blocks
