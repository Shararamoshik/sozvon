"""Настоящие SQLite v0; исходные строки и backup проверяются независимо."""
import json
import sqlite3

import pytest

from sozvon.storage.repository import SCHEMA, Repository


def legacy(root):
    conn = sqlite3.connect(root / "sozvon.db")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO meetings VALUES('m','Встреча','2020-01-02','text','ready',NULL,NULL,'заметки')")
    for version in (1, 2):
        conn.execute("INSERT INTO segments VALUES(?, 'm', ?, 0, NULL, NULL, NULL, ?)",
                     (f"s{version}", version, f"Текст {version}"))
    conn.execute("INSERT INTO reports VALUES('r','m','2020-01-02','model',?,1,'{}')",
                 (json.dumps({"summary": []}),))
    conn.commit()
    return conn


def test_legacy_database_keeps_every_row(tmp_path):
    with legacy(tmp_path) as conn:
        before = {table: conn.execute(f"SELECT * FROM {table}").fetchall()
                  for table in ("meetings", "segments", "reports")}
    Repository(tmp_path)
    Repository(tmp_path)
    with sqlite3.connect(tmp_path / "sozvon.db") as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == rows
        revisions = conn.execute("SELECT revision,parent_revision,origin,created_at,metadata "
                                 "FROM transcript_revisions ORDER BY revision").fetchall()
        assert len(revisions) == 2
        for revision, parent, origin, timestamp, metadata in revisions:
            assert parent is None and origin == "legacy" and timestamp == "2020-01-02"
            assert json.loads(metadata) == {"timestamp_inferred": True}
        assert conn.execute("SELECT id FROM templates ORDER BY id").fetchall() == [
            ("client",), ("meeting",), ("technical",)]
    backups = list((tmp_path / "backups").glob("pre-v1-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT * FROM segments").fetchall() == before["segments"]

def test_failed_migration_rolls_back_version_and_schema(tmp_path):
    from sozvon.storage.migrations import DDL, migrate
    conn = legacy(tmp_path)
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, tmp_path, statements=(DDL[0], "THIS IS INVALID SQL", DDL[1]))
    assert not conn.in_transaction
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='transcript_revisions'").fetchall() == []
    assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 2
    conn.close()


def test_newer_schema_refuses_writes(tmp_path):
    with sqlite3.connect(tmp_path / "sozvon.db") as conn:
        conn.execute("PRAGMA user_version=99")
    before = (tmp_path / "sozvon.db").read_bytes()
    with pytest.raises(ValueError, match="новой"):
        Repository(tmp_path)
    assert (tmp_path / "sozvon.db").read_bytes() == before


def test_backup_contains_uncheckpointed_wal_rows(tmp_path):
    from sozvon.storage.migrations import migrate
    conn = legacy(tmp_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("INSERT INTO segments VALUES('wal', 'm', 3, 0, NULL, NULL, NULL, 'Текст в WAL')")
    conn.commit()
    assert (tmp_path / "sozvon.db-wal").stat().st_size > 0
    migrate(conn, tmp_path)
    backups = list((tmp_path / "backups").glob("*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT text FROM segments WHERE id='wal'").fetchone() == ("Текст в WAL",)
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()


def test_migration_refuses_active_transaction_before_backup(tmp_path):
    from sozvon.storage.migrations import migrate
    conn = legacy(tmp_path)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE meetings SET notes='unsaved'")
    # No backup call may wait on the caller's own write transaction.
    class NoBackup:
        in_transaction = True
        def execute(self, *args):
            return conn.execute(*args)
        def backup(self, target):
            raise AssertionError("backup called inside transaction")
    with pytest.raises(ValueError, match="транзакц"):
        migrate(NoBackup(), tmp_path)
    assert conn.in_transaction
    conn.rollback()
    conn.close()
