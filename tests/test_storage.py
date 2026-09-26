import pytest


def test_text_has_no_fictional_time_and_persists(tmp_path):
    from sozvon.storage.repository import Repository
    repo = Repository(tmp_path)
    meeting = repo.create_text("Обсуждение", "Анна: обновлю инструкцию.\n\nИлья: срока нет.")
    detail = Repository(tmp_path).detail(meeting)
    assert detail["kind"] == "text"
    assert len(detail["segments"]) == 2
    assert all(s["start_ms"] is None and s["end_ms"] is None for s in detail["segments"])
    assert detail["duration_ms"] is None
    assert detail["reports"] == []


def test_report_versions_and_notes_survive(tmp_path):
    from sozvon.storage.repository import Repository
    repo = Repository(tmp_path)
    mid = repo.create_text("test", "Обсудили запуск")
    repo.save_report(mid, {"document": {"summary": []}, "model": "one"})
    repo.save_report(mid, {"document": {"summary": []}, "model": "two"})
    repo.save_notes(mid, "личная заметка")
    detail = repo.detail(mid)
    assert [r["model"] for r in detail["reports"]] == ["two", "one"]
    assert detail["notes"] == "личная заметка"


def test_restart_marks_unfinished_job_not_success(tmp_path):
    from sozvon.storage.repository import Repository
    repo = Repository(tmp_path)
    mid = repo.create_text("test", "text")
    jid = repo.add_job(mid, "report", {})
    repo.job_update(jid, "running", stage="Отчёт")
    again = Repository(tmp_path)
    again.recover()
    assert again.job(jid)["status"] == "interrupted"
    assert again.detail(mid)["segments"]


def test_source_is_confined(tmp_path):
    from sozvon.storage.repository import Repository
    repo = Repository(tmp_path)
    with pytest.raises(ValueError):
        repo.source_path("../../outside")
