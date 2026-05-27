from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .db import connect


def build_triage_queue(
    db_path: str | None,
    crate: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return tracks to triage as a list of dicts.

    Library-wide (crate=None): unrated active tracks (rating IS NULL OR rating=0).
    Crate-scoped: all active tracks in the named crate, including already-rated ones
    (re-triage is intentional).
    """
    with connect(db_path, readonly=True) as conn:
        if crate is not None:
            sql = """
                SELECT t.id, t.path, t.artist, t.title, t.bpm, t.key, t.energy
                FROM tracks t
                JOIN crate_tracks ct ON ct.track_id = t.id
                JOIN crates c ON c.id = ct.crate_id
                WHERE c.name = ? AND t.deleted = 0
                ORDER BY t.id
            """
            params: list[Any] = [crate]
        else:
            sql = """
                SELECT t.id, t.path, t.artist, t.title, t.bpm, t.key, t.energy
                FROM tracks t
                WHERE t.deleted = 0
                  AND (t.rating IS NULL OR t.rating = 0)
                ORDER BY t.id
            """
            params = []

        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        rows = conn.execute(sql, params).fetchall()

    return [dict(row) for row in rows]


def write_m3u(tracks: list[dict[str, Any]], path: str) -> None:
    """Write a minimal M3U playlist file with one path per line."""
    lines = ["#EXTM3U"] + [t["path"] for t in tracks]
    Path(path).write_text("\n".join(lines) + "\n")
