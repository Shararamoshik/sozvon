"""SQLite и управляемые источники. Соединения не разделяются между потоками."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sozvon.storage.migrations import migrate
from sozvon.templates.rendering import sections_for_report


def now() -> str:
    return datetime.now(UTC).isoformat()


def uid() -> str:
    return uuid4().hex


SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings(
 id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,kind TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'ready',error TEXT,duration_ms INTEGER,notes TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS segments(
 id TEXT PRIMARY KEY,meeting_id TEXT NOT NULL REFERENCES meetings(id),revision INTEGER NOT NULL,
 ordinal INTEGER NOT NULL,start_ms INTEGER,end_ms INTEGER,speaker TEXT,text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reports(
 id TEXT PRIMARY KEY,meeting_id TEXT NOT NULL REFERENCES meetings(id),created_at TEXT NOT NULL,
 model TEXT NOT NULL,document TEXT NOT NULL,transcript_revision INTEGER NOT NULL,meta TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
 id TEXT PRIMARY KEY,meeting_id TEXT NOT NULL REFERENCES meetings(id),name TEXT NOT NULL,
 path TEXT NOT NULL,kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY,meeting_id TEXT NOT NULL REFERENCES meetings(id),operation TEXT NOT NULL,
 status TEXT NOT NULL,stage TEXT NOT NULL DEFAULT '',error TEXT,progress TEXT,snapshot TEXT NOT NULL,
 created_at TEXT NOT NULL,finished_at TEXT);
CREATE INDEX IF NOT EXISTS segments_meeting ON segments(meeting_id,revision,ordinal);
CREATE INDEX IF NOT EXISTS reports_meeting ON reports(meeting_id,created_at);
"""


class Repository:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "sozvon.db"
        with self.connection() as conn:
            if conn.execute("PRAGMA user_version").fetchone()[0] > 1:
                raise ValueError("База создана более новой версией приложения")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            migrate(conn, self.root)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create(self, title: str, kind: str) -> str:
        mid = uid()
        with self.connection() as conn:
            conn.execute("INSERT INTO meetings(id,title,created_at,kind) VALUES(?,?,?,?)",
                         (mid, title.strip()[:200] or "Новая запись", now(), kind))
        return mid

    def create_text(self, title: str, text: str) -> str:
        if not text.strip():
            raise ValueError("Введите текст созвона")
        if len(text) > 500_000:
            raise ValueError("Текст слишком большой: максимум 500 000 символов")
        mid = uid()
        parts = [part.strip() for part in text.splitlines() if part.strip()]
        with self.connection() as conn:
            conn.execute("INSERT INTO meetings(id,title,created_at,kind) VALUES(?,?,?,?)",
                         (mid, title.strip()[:200] or "Текст созвона", now(), "text"))
            from sozvon.storage.transcripts import insert_revision
            insert_revision(conn, mid, 0, [{"text": value} for value in parts], "import", {})
        return mid

    def list(self) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute("""SELECT m.*,
              (SELECT COUNT(*) FROM segments s WHERE s.meeting_id=m.id AND s.revision=
                (SELECT MAX(revision) FROM segments WHERE meeting_id=m.id)) AS segment_count,
              (SELECT COUNT(*) FROM reports r WHERE r.meeting_id=m.id) AS report_count
              FROM meetings m ORDER BY created_at DESC LIMIT 1000""").fetchall()
            return [dict(row) for row in rows]

    def detail(self, mid: str) -> dict:
        with self.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
            if row is None:
                raise KeyError(mid)
            result = dict(row)
            revision = conn.execute("SELECT COALESCE(MAX(revision),0) FROM segments WHERE meeting_id=?",
                                    (mid,)).fetchone()[0]
            result["transcript_revision"] = revision
            result["segments"] = [dict(s) for s in conn.execute(
                "SELECT * FROM segments WHERE meeting_id=? AND revision=? ORDER BY ordinal",
                (mid, revision))]
            result["transcript_history"] = self._history(conn, mid)
            result["reports"] = []
            for report in conn.execute("SELECT * FROM reports WHERE meeting_id=? ORDER BY created_at DESC",
                                       (mid,)):
                item = dict(report)
                item["document"] = json.loads(item["document"])
                item["meta"] = json.loads(item["meta"])
                item["stale"] = item["transcript_revision"] != revision
                item["sections"] = sections_for_report(item)
                result["reports"].append(item)
            result["sources"] = [dict(s) for s in conn.execute(
                "SELECT id,name,kind FROM sources WHERE meeting_id=?", (mid,))]
            for source in result["sources"]:
                source["url"] = f"/api/sources/{source['id']}"
            return result

    @staticmethod
    def _history(conn, mid: str) -> list[dict]:
        rows = conn.execute("SELECT * FROM segments WHERE meeting_id=? ORDER BY revision,ordinal", (mid,))
        history = {}
        for row in rows:
            item = dict(row)
            history.setdefault(item["revision"], []).append(item)
        return [{"revision": revision, "segments": segments}
                for revision, segments in history.items()]

    def transcript_history(self, mid: str) -> list[dict]:
        with self.connection() as conn:
            return self._history(conn, mid)

    def status(self, mid: str, status: str, error: str | None = None):
        with self.connection() as conn:
            conn.execute("UPDATE meetings SET status=?,error=? WHERE id=?", (status, error, mid))

    def save_notes(self, mid: str, text: str):
        with self.connection() as conn:
            changed = conn.execute("UPDATE meetings SET notes=? WHERE id=?", (text, mid)).rowcount
            if not changed:
                raise KeyError(mid)

    def save_segments(self, mid: str, segments: list[dict]):
        if not segments:
            raise ValueError("Речь не обнаружена")
        from sozvon.storage.transcripts import current_revision, insert_revision
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            base = current_revision(conn, mid)
            insert_revision(conn, mid, base, segments, "local_stt", {})

    def save_report(self, mid: str, result: dict, *, transcript_revision: int | None = None):
        with self.connection() as conn:
            revision = transcript_revision
            if revision is None:
                revision = conn.execute("SELECT COALESCE(MAX(revision),0) FROM segments WHERE meeting_id=?",
                                        (mid,)).fetchone()[0]
            conn.execute("INSERT INTO reports VALUES(?,?,?,?,?,?,?)", (
                uid(), mid, now(), result.get("model", ""),
                json.dumps(result["document"], ensure_ascii=False), revision,
                json.dumps({k: v for k, v in result.items() if k != "document"}, ensure_ascii=False)))
            conn.execute("UPDATE meetings SET status='ready',error=NULL WHERE id=?", (mid,))

    def attach_source(self, mid: str, path: Path, kind: str, duration_ms: int | None = None) -> str:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise ValueError("Источник должен быть управляемым файлом приложения")
        sid = uid()
        with self.connection() as conn:
            conn.execute("INSERT INTO sources VALUES(?,?,?,?,?)",
                         (sid, mid, resolved.name, str(resolved.relative_to(self.root)), kind))
            if duration_ms is not None:
                conn.execute("UPDATE meetings SET duration_ms=? WHERE id=?", (duration_ms, mid))
        return sid

    def set_duration(self, mid: str, duration_ms: int):
        with self.connection() as conn:
            conn.execute("UPDATE meetings SET duration_ms=? WHERE id=?", (duration_ms, mid))

    def source_path(self, sid: str) -> Path:
        if not sid.isalnum() or len(sid) != 32:
            raise ValueError("Недопустимый источник")
        with self.connection() as conn:
            row = conn.execute("SELECT path FROM sources WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise KeyError(sid)
        path = (self.root / row["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Путь источника выходит за папку приложения")
        if not path.is_file():
            raise FileNotFoundError("Исходный файл недоступен")
        return path

    def add_job(self, mid: str, operation: str, snapshot: dict) -> str:
        jid = uid()
        with self.connection() as conn:
            conn.execute("INSERT INTO jobs(id,meeting_id,operation,status,snapshot,created_at) "
                         "VALUES(?,?,?,'queued',?,?)",
                         (jid, mid, operation, json.dumps(snapshot, ensure_ascii=False), now()))
        return jid

    def job(self, jid: str) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if row is None:
            raise KeyError(jid)
        result = dict(row)
        result.pop("snapshot", None)
        result["progress"] = json.loads(result["progress"]) if result["progress"] else None
        return result

    def job_update(self, jid: str, status: str, *, stage: str = "", error: str | None = None,
                   progress: dict | None = None):
        finished = now() if status in {"succeeded", "failed", "cancelled", "interrupted"} else None
        with self.connection() as conn:
            conn.execute("UPDATE jobs SET status=?,stage=?,error=?,progress=?,finished_at=? WHERE id=?",
                         (status, stage, error, json.dumps(progress) if progress else None, finished, jid))

    def recover(self):
        with self.connection() as conn:
            conn.execute("UPDATE meetings SET status='interrupted',error=? WHERE id IN "
                         "(SELECT meeting_id FROM jobs WHERE status IN ('queued','running','cancelling'))",
                         ("Приложение завершилось во время обработки. Исходники сохранены.",))
            conn.execute("UPDATE jobs SET status='interrupted',error=?,finished_at=? "
                         "WHERE status IN ('queued','running','cancelling')",
                         ("Обработка прервана. Её можно запустить повторно.", now()))
