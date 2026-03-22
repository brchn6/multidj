from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path("~/.multidj/library.sqlite").expanduser()
MIXXX_DB_PATH   = Path("~/.mixxx/mixxxdb.sqlite").expanduser()


def resolve_db_path(db_path: str | None = None) -> Path:
    candidate = db_path or os.environ.get("MULTIDJ_DB_PATH")
    return Path(candidate).expanduser() if candidate else DEFAULT_DB_PATH


def ensure_db_exists(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")


def ensure_not_empty(conn: sqlite3.Connection) -> None:
    """Raise a clear error if MultiDJ tracks table is empty (import not yet run)."""
    if not table_exists(conn, "tracks"):
        raise RuntimeError("MultiDJ DB is empty. Run 'multidj import mixxx' first.")
    row = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()
    if int(row[0]) == 0:
        raise RuntimeError("MultiDJ DB is empty. Run 'multidj import mixxx' first.")


def _apply_migrations(conn: sqlite3.Connection) -> None:
    migrations_dir = Path(__file__).parent / "migrations"

    # Bootstrap schema_version table
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version VALUES (0)")
        conn.commit()
        current = 0
    else:
        current = int(row[0])

    for script in sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.sql")):
        n = int(script.name[:3])
        if n <= current:
            continue
        sql = script.read_text()
        try:
            # executescript() handles multi-statement SQL including triggers.
            # It auto-commits (SQLite DDL can't be rolled back anyway).
            # Protection: schema_version only updates if the script succeeds.
            conn.executescript(sql)
            conn.execute("UPDATE schema_version SET version = ?", (n,))
            conn.commit()
        except Exception as exc:
            raise RuntimeError(f"Migration {script.name} failed: {exc}") from exc


@contextmanager
def connect(db_path: str | None = None, readonly: bool = True) -> Iterator[sqlite3.Connection]:
    path = resolve_db_path(db_path)

    if readonly:
        ensure_db_exists(path)
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        _apply_migrations(conn)

    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None
