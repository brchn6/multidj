"""
Mixxx-schema SQLite factory for tests.

Creates a SQLite file with the Mixxx schema (simplified to the columns
MultiDJ uses) and populates it with the canonical fixture data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.fixtures.data import CRATE_TRACKS, CRATES, KEYS, TRACK_KEY_IDS, TRACKS

_DDL = """
CREATE TABLE IF NOT EXISTS track_locations (
    id       INTEGER PRIMARY KEY,
    location TEXT UNIQUE NOT NULL,
    filesize INTEGER,
    fs_deleted INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS keys (
    id       INTEGER PRIMARY KEY,
    key_text TEXT
);

CREATE TABLE IF NOT EXISTS library (
    id             INTEGER PRIMARY KEY,
    artist         TEXT,
    title          TEXT,
    album          TEXT,
    genre          TEXT,
    bpm            REAL,
    key_id         INTEGER,
    rating         INTEGER,
    timesplayed    INTEGER DEFAULT 0,
    duration       REAL,
    remixer        TEXT,
    mixxx_deleted  INTEGER DEFAULT 0,
    location       INTEGER  -- FK to track_locations.id
);

CREATE TABLE IF NOT EXISTS crates (
    id              INTEGER PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    show            INTEGER DEFAULT 1,
    locked          INTEGER DEFAULT 0,
    autodj_source   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS crate_tracks (
    crate_id  INTEGER NOT NULL,
    track_id  INTEGER NOT NULL,
    PRIMARY KEY (crate_id, track_id)
);
"""


def make_mixxx_db(path: Path) -> Path:
    """Create a Mixxx-schema SQLite file at *path*. Returns *path*."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_DDL)

        # --- keys ---
        conn.executemany(
            "INSERT INTO keys (id, key_text) VALUES (?, ?)",
            KEYS,
        )

        # --- track_locations ---
        # One location row per track; id matches track id for simplicity.
        locations = [
            (
                track_id,       # id
                path_str,       # location
                filesize,       # filesize
                0,              # fs_deleted=0 (file exists)
            )
            for (track_id, _artist, _title, _genre, _bpm, _key_text,
                 _rating, _timesplayed, path_str, filesize, _duration) in TRACKS
        ]
        conn.executemany(
            "INSERT INTO track_locations (id, location, filesize, fs_deleted) VALUES (?,?,?,?)",
            locations,
        )

        # --- library ---
        library_rows = []
        for row in TRACKS:
            (track_id, artist, title, genre, bpm, _key_text,
             rating, timesplayed, path_str, filesize, duration) = row
            key_id = TRACK_KEY_IDS.get(track_id)
            mixxx_deleted = 1 if track_id == 10 else 0
            library_rows.append((
                track_id,
                artist,
                title,
                None,           # album
                genre,
                bpm,
                key_id,
                rating,
                timesplayed,
                duration,
                None,           # remixer
                mixxx_deleted,
                track_id,       # location (same id as track_locations row)
            ))
        conn.executemany(
            """
            INSERT INTO library
                (id, artist, title, album, genre, bpm, key_id, rating,
                 timesplayed, duration, remixer, mixxx_deleted, location)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            library_rows,
        )

        # --- crates ---
        # Mixxx crates table doesn't have a `type` column; we store only the
        # Mixxx-native columns here.
        conn.executemany(
            "INSERT INTO crates (id, name, show) VALUES (?,?,?)",
            [(c_id, c_name, c_show) for (c_id, c_name, _c_type, c_show) in CRATES],
        )

        # --- crate_tracks ---
        conn.executemany(
            "INSERT INTO crate_tracks (crate_id, track_id) VALUES (?,?)",
            CRATE_TRACKS,
        )

        conn.commit()
    finally:
        conn.close()

    return path
