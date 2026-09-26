from contextlib import contextmanager

from sozvon.storage.repository import Repository


def test_detail_reads_one_snapshot_during_concurrent_write(tmp_path):
    repo = Repository(tmp_path)
    mid = repo.create_text("Встреча", "Исходник")
    original = repo.detail(mid)
    connection = repo.connection
    changed = False

    @contextmanager
    def interleaved():
        nonlocal changed
        with connection() as conn:
            def trace(sql):
                nonlocal changed
                if "COALESCE(MAX(revision)" in sql and not changed:
                    changed = True
                    with connection() as writer:
                        writer.execute("INSERT INTO segments VALUES('new',?,2,0,NULL,NULL,NULL,'Другой')", (mid,))
                        writer.execute("UPDATE meetings SET title='Новое имя' WHERE id=?", (mid,))
            conn.set_trace_callback(trace)
            yield conn
    repo.connection = interleaved
    during = repo.detail(mid)
    assert changed
    assert during == original
    repo.connection = connection
    assert repo.detail(mid)["transcript_revision"] == 2


def test_detail_contains_history_in_same_snapshot(tmp_path):
    repo = Repository(tmp_path)
    mid = repo.create_text("Встреча", "Исходник")
    repo.save_segments(mid, [{"text": "Другая версия"}])
    expected = repo.transcript_history(mid)
    assert repo.detail(mid)["transcript_history"] == expected
