"""Неизменяемые расшифровки: все записи новой версии в одной транзакции."""
import json

from sozvon.core.errors import RevisionConflict
from sozvon.storage.repository import now, uid


def current_revision(conn, meeting_id: str) -> int:
    if conn.execute("SELECT 1 FROM meetings WHERE id=?", (meeting_id,)).fetchone() is None:
        raise KeyError(meeting_id)
    return conn.execute(
        "SELECT COALESCE(MAX(revision),0) FROM segments WHERE meeting_id=?", (meeting_id,)
    ).fetchone()[0]


def read_revision(repo, meeting_id: str, revision: int) -> list[dict]:
    with repo.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM segments WHERE meeting_id=? AND revision=? ORDER BY ordinal",
            (meeting_id, revision),
        ).fetchall()
        if not rows:
            raise KeyError(meeting_id)
        return [dict(row) for row in rows]


def insert_revision(conn, meeting_id, base_revision, rows, origin, metadata):
    revision = base_revision + 1
    conn.execute(
        "INSERT INTO transcript_revisions VALUES(?,?,?,?,?,?)",
        (meeting_id, revision, base_revision or None, origin, now(),
         json.dumps(metadata, ensure_ascii=False)),
    )
    conn.executemany(
        "INSERT INTO segments VALUES(?,?,?,?,?,?,?,?)",
        [(uid(), meeting_id, revision, i, row.get("start_ms"), row.get("end_ms"),
          row.get("speaker"), row["text"]) for i, row in enumerate(rows)],
    )
    conn.execute("UPDATE meetings SET status='ready',error=NULL WHERE id=?", (meeting_id,))
    return revision


def save_edits(repo, meeting_id, request):
    with repo.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actual = current_revision(conn, meeting_id)
        if actual != request.base_revision:
            raise RevisionConflict(actual)
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM segments WHERE meeting_id=? AND revision=? ORDER BY ordinal",
            (meeting_id, actual),
        )]
        by_id = {row["id"]: row for row in rows}
        for edit in request.edits:
            if edit.id not in by_id:
                raise ValueError("Реплика не принадлежит редактируемой версии")
        changed = False
        for edit in request.edits:
            row = by_id[edit.id]
            speaker = edit.speaker if "speaker" in edit.model_fields_set else row["speaker"]
            changed |= row["text"] != edit.text or row["speaker"] != speaker
            row["text"], row["speaker"] = edit.text, speaker
        if sum(len(row["text"]) for row in rows) > 500_000:
            raise ValueError("Расшифровка превышает 500 000 символов")
        if not changed:
            return actual
        return insert_revision(conn, meeting_id, actual, rows, "manual", {})


def restore_revision(repo, meeting_id, base_revision, target_revision):
    with repo.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actual = current_revision(conn, meeting_id)
        if actual != base_revision:
            raise RevisionConflict(actual)
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM segments WHERE meeting_id=? AND revision=? ORDER BY ordinal",
            (meeting_id, target_revision),
        )]
        if not rows:
            raise KeyError(meeting_id)
        return insert_revision(conn, meeting_id, actual, rows, "restore",
                               {"restored_revision": target_revision})


def save_recognition(repo, meeting_id, base_revision, segments, origin, metadata):
    if origin not in {"local_stt", "cloud_stt"} or not segments:
        raise ValueError("Нет корректного результата распознавания")
    with repo.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actual = current_revision(conn, meeting_id)
        if actual != base_revision:
            raise RevisionConflict(actual)
        return insert_revision(conn, meeting_id, actual, segments, origin, metadata)


def list_revisions(repo, meeting_id) -> list[dict]:
    with repo.connection() as conn:
        conn.execute("BEGIN")
        current_revision(conn, meeting_id)
        rows = conn.execute(
            "SELECT revision,parent_revision,origin,created_at,metadata FROM transcript_revisions "
            "WHERE meeting_id=? ORDER BY revision DESC", (meeting_id,),
        )
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]
