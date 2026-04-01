"""
MultiDJ-schema SQLite factory for tests.

Creates a SQLite file in "post-import" state — the state the DB is in after
`import mixxx` has run and all fixture tracks are present.

Preferred path: uses `multidj.db.connect()` so that the migration runner
applies the canonical schema. Falls back to an inline schema builder when the
`multidj` package is not yet available (i.e., before Sub-agent B lands).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tests.fixtures.data import CRATE_TRACKS, CRATES, KEYS, TRACK_KEY_IDS, TRACKS

# ---------------------------------------------------------------------------
# Inline schema — kept in sync with multidj migration v1.
# Used as fallback when `multidj` package is not installed yet.
# ---------------------------------------------------------------------------
_MULTIDJ_DDL = """
CREATE TABLE IF NOT EXISTS tracks (
    id          INTEGER PRIMARY KEY,
    path        TEXT UNIQUE NOT NULL,
    artist      TEXT,
    title       TEXT,
    album       TEXT,
    genre       TEXT,
    bpm         REAL,
    key         TEXT,
    rating      INTEGER,
    play_count  INTEGER DEFAULT 0,
    duration    REAL,
    filesize    INTEGER,
    deleted     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS crates (
    id    INTEGER PRIMARY KEY,
    name  TEXT UNIQUE NOT NULL,
    type  TEXT NOT NULL DEFAULT 'hand-curated',
    show  INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS crate_tracks (
    crate_id  INTEGER NOT NULL,
    track_id  INTEGER NOT NULL,
    PRIMARY KEY (crate_id, track_id)
);

CREATE TABLE IF NOT EXISTS sync_state (
    track_id        INTEGER NOT NULL,
    adapter         TEXT NOT NULL,
    dirty           INTEGER DEFAULT 0,
    last_synced_at  TEXT,
    PRIMARY KEY (track_id, adapter)
);
"""


def _build_key_lookup() -> dict[int, str | None]:
    """Map track_id -> Camelot key string (or None)."""
    key_by_id: dict[int, str] = {k_id: k_text for (k_id, k_text) in KEYS}
    return {
        track_id: key_by_id.get(key_id)
        for track_id, key_id in TRACK_KEY_IDS.items()
    }


def _insert_data(conn: sqlite3.Connection) -> None:
    """Insert all fixture data into an already-schema'd MultiDJ connection."""
    key_lookup = _build_key_lookup()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # --- tracks (active only; skip id=10) ---
    track_rows = []
    for row in TRACKS:
        (track_id, artist, title, genre, bpm, _key_text,
         rating, timesplayed, path_str, filesize, duration) = row
        if track_id == 10:
            continue  # soft-deleted in Mixxx; not imported into MultiDJ
        key = key_lookup.get(track_id)
        track_rows.append((
            track_id,
            path_str,
            artist,
            title,
            None,           # album
            genre,
            bpm,
            key,
            rating,
            timesplayed,    # play_count <- timesplayed
            duration,
            filesize,
            0,              # deleted=0
        ))
    conn.executemany(
        """
        INSERT INTO tracks
            (id, path, artist, title, album, genre, bpm, key, rating,
             play_count, duration, filesize, deleted)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        track_rows,
    )

    # --- crates ---
    conn.executemany(
        "INSERT INTO crates (id, name, type, show) VALUES (?,?,?,?)",
        CRATES,
    )

    # --- crate_tracks ---
    conn.executemany(
        "INSERT INTO crate_tracks (crate_id, track_id) VALUES (?,?)",
        CRATE_TRACKS,
    )

    # --- sync_state (one row per active track, adapter='mixxx') ---
    sync_rows = [
        (track_id, "mixxx", 0, now)
        for (track_id, *_rest) in TRACKS
        if track_id != 10
    ]
    conn.executemany(
        "INSERT INTO sync_state (track_id, adapter, dirty, last_synced_at) VALUES (?,?,?,?)",
        sync_rows,
    )

    conn.commit()


def make_multidj_db(path: Path) -> Path:
    """Create a MultiDJ-schema SQLite file at *path* in post-import state.

    Tries to use ``multidj.db.connect()`` (which runs the migration runner) so
    the schema is always canonical. Falls back to an inline DDL when the
    ``multidj`` package is not yet installed — this allows the test scaffold to
    load before Sub-agent B has landed.

    Returns *path*.
    """
    try:
        from multidj.db import connect as multidj_connect  # type: ignore[import]

        # connect() in read/write mode will run migrations and create all tables.
        with multidj_connect(str(path), readonly=False) as conn:
            _insert_data(conn)
    except ImportError:
        # multidj package not yet available — use inline schema.
        conn = sqlite3.connect(str(path))
        try:
            conn.executescript(_MULTIDJ_DDL)
            _insert_data(conn)
        finally:
            conn.close()

    return path
