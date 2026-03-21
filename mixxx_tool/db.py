from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path("~/.mixxx/mixxxdb.sqlite").expanduser()


def resolve_db_path(db_path: str | None = None) -> Path:
    candidate = db_path or os.environ.get("MIXXX_DB_PATH")
    return Path(candidate).expanduser() if candidate else DEFAULT_DB_PATH


def ensure_db_exists(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Mixxx database not found: {db_path}")


@contextmanager
def connect(db_path: str | None = None, readonly: bool = True) -> Iterator[sqlite3.Connection]:
    path = resolve_db_path(db_path)
    ensure_db_exists(path)

    if readonly:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(path)

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
