import pytest

from sozvon.storage.repository import Repository


def test_edit_creates_revision_without_changing_original(tmp_path):
    from sozvon.storage.transcripts import read_revision, save_edits
    from sozvon.transcripts.schema import TranscriptEdit
    repo = Repository(tmp_path)
    mid = repo.create_text("Встреча", "Срок в пятницу\nДругая реплика")
    original = repo.detail(mid)["segments"]
    request = TranscriptEdit(base_revision=1, edits=[{
        "id": original[0]["id"], "text": "Срок не определён", "speaker": "Анна"}])
    assert save_edits(repo, mid, request) == 2
    assert read_revision(repo, mid, 1) == original
    current = repo.detail(mid)["segments"]
    assert current[0]["text"] == "Срок не определён"
    assert current[0]["speaker"] == "Анна"
    assert current[1]["text"] == original[1]["text"]
    assert all(row["start_ms"] is None and row["end_ms"] is None for row in current)
    assert not ({row["id"] for row in current} & {row["id"] for row in original})


def test_second_editor_conflicts_and_preserves_first(tmp_path):
    from sozvon.core.errors import RevisionConflict
    from sozvon.storage.transcripts import save_edits
    from sozvon.transcripts.schema import TranscriptEdit
    repo = Repository(tmp_path)
    mid = repo.create_text("Встреча", "Исходник")
    sid = repo.detail(mid)["segments"][0]["id"]
    save_edits(repo, mid, TranscriptEdit(base_revision=1, edits=[{"id": sid, "text": "Первый"}]))
    with pytest.raises(RevisionConflict) as caught:
        save_edits(repo, mid, TranscriptEdit(base_revision=1, edits=[{"id": sid, "text": "Второй"}]))
    assert caught.value.current_revision == 2
    assert repo.detail(mid)["segments"][0]["text"] == "Первый"


def test_foreign_segment_rejected(tmp_path):
    from sozvon.storage.transcripts import save_edits
    from sozvon.transcripts.schema import TranscriptEdit
    repo = Repository(tmp_path)
    mid = repo.create_text("Первая", "Исходник")
    other = repo.create_text("Другая", "Чужой")
    sid = repo.detail(other)["segments"][0]["id"]
    with pytest.raises(ValueError, match="принадлежит"):
        save_edits(repo, mid, TranscriptEdit(base_revision=1, edits=[{"id": sid, "text": "Подмена"}]))
    assert repo.detail(mid)["transcript_revision"] == 1


def test_noop_does_not_increment_revision(tmp_path):
    from sozvon.storage.transcripts import save_edits
    from sozvon.transcripts.schema import TranscriptEdit
    repo = Repository(tmp_path)
    mid = repo.create_text("Встреча", "Исходник")
    sid = repo.detail(mid)["segments"][0]["id"]
    assert save_edits(repo, mid, TranscriptEdit(base_revision=1, edits=[{"id": sid, "text": "Исходник"}])) == 1
    assert repo.detail(mid)["segments"][0]["id"] == sid


@pytest.mark.parametrize("change", ["blank", "control", "surrogate", "long", "speaker", "duplicate", "empty", "zero"])
def test_edit_schema_rejects_invalid_changes(change):
    from sozvon.transcripts.schema import TranscriptEdit
    data = {"base_revision": 1, "edits": [{"id": "s1", "text": "Текст"}]}
    if change in {"blank", "control", "surrogate", "long"}:
        data["edits"][0]["text"] = {"blank": "  ", "control": "a\x00", "surrogate": "\ud800", "long": "x" * 50001}[change]
    elif change == "speaker":
        data["edits"][0]["speaker"] = "A\nB"
    elif change == "duplicate":
        data["edits"].append(data["edits"][0].copy())
    elif change == "empty":
        data["edits"] = []
    elif change == "zero":
        data["base_revision"] = 0
    with pytest.raises(ValueError):
        TranscriptEdit.model_validate(data)


def test_restore_creates_new_revision(tmp_path):
    from sozvon.storage.transcripts import read_revision, restore_revision
    repo = Repository(tmp_path)
    mid = repo.create("Аудио", "audio")
    repo.save_segments(mid, [{"text": "Первый", "start_ms": 10, "end_ms": 1200, "speaker": "Анна"}])
    original = read_revision(repo, mid, 1)
    repo.save_segments(mid, [{"text": "Второй", "start_ms": 0, "end_ms": 900}])
    assert restore_revision(repo, mid, 2, 1) == 3
    row = repo.detail(mid)["segments"][0]
    assert row["text"] == "Первый" and row["speaker"] == "Анна"
    assert (row["start_ms"], row["end_ms"]) == (10, 1200)
    assert row["id"] != original[0]["id"]
    assert read_revision(repo, mid, 1) == original


def test_stt_cas_cannot_overwrite_manual_edit(tmp_path):
    from sozvon.core.errors import RevisionConflict
    from sozvon.storage.transcripts import save_edits, save_recognition
    from sozvon.transcripts.schema import TranscriptEdit
    repo = Repository(tmp_path)
    mid = repo.create("Запись", "audio")
    assert save_recognition(repo, mid, 0, [{"text": "Речь"}], "cloud_stt", {"timed": False}) == 1
    sid = repo.detail(mid)["segments"][0]["id"]
    save_edits(repo, mid, TranscriptEdit(base_revision=1, edits=[{"id": sid, "text": "Правка"}]))
    with pytest.raises(RevisionConflict) as caught:
        save_recognition(repo, mid, 1, [{"text": "Поздний STT"}], "local_stt", {})
    assert caught.value.current_revision == 2
    assert repo.detail(mid)["segments"][0]["text"] == "Правка"


def test_all_entrypoints_publish_revision_history(tmp_path):
    from sozvon.storage.transcripts import list_revisions, restore_revision
    repo = Repository(tmp_path)
    mid = repo.create_text("Встреча", "Исходник")
    repo.save_segments(mid, [{"text": "Распознано"}])
    restore_revision(repo, mid, 2, 1)
    revisions = list_revisions(repo, mid)
    assert [r["revision"] for r in revisions] == [3, 2, 1]
    assert [r["origin"] for r in revisions] == ["restore", "local_stt", "import"]
    assert [r["parent_revision"] for r in revisions] == [2, 1, None]
    assert revisions[0]["metadata"] == {"restored_revision": 1}
    assert all(r["created_at"] for r in revisions)
    assert list_revisions(repo, repo.create("Пустая", "audio")) == []
    with pytest.raises(KeyError):
        list_revisions(repo, "missing")


def test_edit_rejects_total_over_limit_without_partial_write(tmp_path):
    from sozvon.storage.transcripts import save_edits
    from sozvon.transcripts.schema import TranscriptEdit
    repo = Repository(tmp_path)
    mid = repo.create_text("Большая", "x" * 499_980 + "\nx")
    rows = repo.detail(mid)["segments"]
    with pytest.raises(ValueError, match="500 000"):
        save_edits(repo, mid, TranscriptEdit(base_revision=1, edits=[{"id": rows[1]["id"], "text": "y" * 30}]))
    assert repo.detail(mid)["segments"] == rows


def test_manual_audio_edit_keeps_times_and_explicit_null_clears_speaker(tmp_path):
    from sozvon.storage.transcripts import read_revision, save_edits
    from sozvon.transcripts.schema import TranscriptEdit
    repo = Repository(tmp_path)
    mid = repo.create("Запись", "audio")
    repo.save_segments(mid, [{"text": "Речь", "start_ms": 123, "end_ms": 456, "speaker": "Анна"}])
    before = read_revision(repo, mid, 1)
    save_edits(repo, mid, TranscriptEdit(base_revision=1, edits=[{"id": before[0]["id"], "text": "Правка", "speaker": None}]))
    current = read_revision(repo, mid, 2)[0]
    assert (current["start_ms"], current["end_ms"], current["speaker"]) == (123, 456, None)
    assert read_revision(repo, mid, 1) == before


def test_parallel_editors_publish_exactly_one_revision(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sozvon.core.errors import RevisionConflict
    from sozvon.storage.transcripts import list_revisions, save_edits
    from sozvon.transcripts.schema import TranscriptEdit
    repo = Repository(tmp_path)
    mid = repo.create_text("Встреча", "Исходник")
    sid = repo.detail(mid)["segments"][0]["id"]
    barrier = Barrier(2)
    def edit(text):
        barrier.wait(timeout=3)
        try:
            return save_edits(repo, mid, TranscriptEdit(base_revision=1, edits=[{"id": sid, "text": text}]))
        except RevisionConflict as exc:
            return f"conflict:{exc.current_revision}"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ["Первый", "Второй"]))
    assert 2 in results and "conflict:2" in results
    assert len(list_revisions(repo, mid)) == 2


def test_revision_queries_never_read_other_meeting(tmp_path):
    from sozvon.core.errors import RevisionConflict
    from sozvon.storage.transcripts import read_revision, restore_revision
    repo = Repository(tmp_path)
    mid = repo.create_text("Встреча", "Свой текст")
    other = repo.create_text("Другая", "Чужой текст")
    repo.save_segments(other, [{"text": "Вторая чужая версия"}])
    with pytest.raises(KeyError):
        read_revision(repo, mid, 2)
    with pytest.raises(KeyError):
        restore_revision(repo, mid, 1, 2)
    with pytest.raises(RevisionConflict):
        restore_revision(repo, mid, 0, 1)
    assert repo.detail(mid)["transcript_revision"] == 1
