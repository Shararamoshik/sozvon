"""Версионированная схема SQLite: backup через SQLite, DDL без executescript."""
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sozvon.templates.builtin import builtin_ids, builtin_spec

DDL = (
    ("CREATE TABLE transcript_revisions (meeting_id TEXT NOT NULL REFERENCES meetings(id), "
     "revision INTEGER NOT NULL CHECK(revision>0), parent_revision INTEGER, "
     "origin TEXT NOT NULL CHECK(origin IN ('legacy','import','local_stt','cloud_stt','manual','restore')), "
     "created_at TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(meeting_id,revision))"),
    "CREATE UNIQUE INDEX segments_revision_ordinal ON segments(meeting_id,revision,ordinal)",
    ("CREATE TABLE templates (id TEXT PRIMARY KEY, builtin INTEGER NOT NULL DEFAULT 0 "
     "CHECK(builtin IN (0,1)), archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)), "
     "created_at TEXT NOT NULL)"),
    ("CREATE TABLE template_revisions (template_id TEXT NOT NULL REFERENCES templates(id), "
     "revision INTEGER NOT NULL CHECK(revision>0), spec_json TEXT NOT NULL, "
     "created_at TEXT NOT NULL, PRIMARY KEY(template_id,revision))"),
)


def ensure_builtins(conn):
    """Идемпотентно добавляет отсутствующие встроенные шаблоны.

    Только INSERT OR IGNORE с фиксированными идентификаторами: пользовательские
    шаблоны и их ревизии не затрагиваются, поэтому версия схемы не повышается и
    база остаётся открываемой предыдущей сборкой.
    """
    created_at = datetime.now(UTC).isoformat()
    for template_id in builtin_ids():
        conn.execute("INSERT OR IGNORE INTO templates VALUES(?,1,0,?)", (template_id, created_at))
        conn.execute("INSERT OR IGNORE INTO template_revisions VALUES(?,1,?,?)",
                     (template_id, builtin_spec(template_id).model_dump_json(), created_at))


def migrate(conn, root: Path, *, statements=DDL):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > 1:
        raise ValueError("База создана более новой версией приложения")
    if version == 1:
        ensure_builtins(conn)
        return
    if conn.in_transaction:
        raise ValueError("Перед миграцией завершите текущую транзакцию")
    if conn.execute("SELECT EXISTS(SELECT 1 FROM meetings)").fetchone()[0]:
        folder = Path(root) / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        backup = sqlite3.connect(folder / f"pre-v1-{uuid4().hex}.db")
        try:
            conn.backup(backup)
        finally:
            backup.close()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in statements:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO transcript_revisions(meeting_id,revision,parent_revision,origin,created_at,metadata) "
            "SELECT s.meeting_id,s.revision,NULL,'legacy',m.created_at,? "
            "FROM segments s JOIN meetings m ON m.id=s.meeting_id GROUP BY s.meeting_id,s.revision",
            (json.dumps({"timestamp_inferred": True}),),
        )
        created_at = datetime.now(UTC).isoformat()
        for template_id in builtin_ids():
            conn.execute("INSERT INTO templates VALUES(?,1,0,?)", (template_id, created_at))
            conn.execute("INSERT INTO template_revisions VALUES(?,1,?,?)",
                         (template_id, builtin_spec(template_id).model_dump_json(), created_at))
        conn.execute("PRAGMA user_version=1")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
