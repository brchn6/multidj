from __future__ import annotations

import sqlite3
from typing import Any

from .db import connect, table_exists
from .models import LibrarySummary


def _count(conn: sqlite3.Connection, query: str) -> int:
    row = conn.execute(query).fetchone()
    return int(row[0]) if row else 0


_ACTIVE = "mixxx_deleted = 0"


def scan_library(db_path: str | None = None, verbose: bool = False) -> dict[str, Any]:
    with connect(db_path, readonly=True) as conn:
        if not table_exists(conn, "library"):
            raise RuntimeError("Expected Mixxx table 'library' was not found.")

        total_tracks = _count(conn, f"SELECT COUNT(*) FROM library WHERE {_ACTIVE}")
        total_crates = 0
        if table_exists(conn, "crates"):
            total_crates = _count(conn, "SELECT COUNT(*) FROM crates")

        summary = LibrarySummary(
            total_tracks=total_tracks,
            total_crates=total_crates,
            tracks_with_genre=_count(
                conn,
                f"SELECT COUNT(*) FROM library WHERE {_ACTIVE} AND genre IS NOT NULL AND TRIM(genre) != ''"
            ),
            tracks_with_bpm=_count(
                conn,
                f"SELECT COUNT(*) FROM library WHERE {_ACTIVE} AND bpm IS NOT NULL"
            ),
            tracks_with_key=_count(
                conn,
                f"SELECT COUNT(*) FROM library WHERE {_ACTIVE} AND key IS NOT NULL AND TRIM(key) != ''"
            ),
            tracks_with_rating=_count(
                conn,
                f"SELECT COUNT(*) FROM library WHERE {_ACTIVE} AND rating IS NOT NULL AND rating != 0"
            ),
        )

        result: dict[str, Any] = {"summary": summary.to_dict()}

        if verbose:
            result["tables"] = [
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]

        return result


def format_scan(data: dict[str, Any]) -> str:
    s = data["summary"]
    total = s["total_tracks"]

    def pct(n: int) -> str:
        return f"{n / total * 100:.0f}%" if total else "—"

    def bar(n: int, width: int = 20) -> str:
        filled = round(n / total * width) if total else 0
        return "█" * filled + "░" * (width - filled)

    lines = [
        f"Library health — {total:,} active tracks  ({s['total_crates']} crates)",
        "",
        f"  BPM    {s['tracks_with_bpm']:>5,} / {total:,}  {pct(s['tracks_with_bpm']):>4}  {bar(s['tracks_with_bpm'])}",
        f"  Genre  {s['tracks_with_genre']:>5,} / {total:,}  {pct(s['tracks_with_genre']):>4}  {bar(s['tracks_with_genre'])}",
        f"  Key    {s['tracks_with_key']:>5,} / {total:,}  {pct(s['tracks_with_key']):>4}  {bar(s['tracks_with_key'])}",
        f"  Rating {s['tracks_with_rating']:>5,} / {total:,}  {pct(s['tracks_with_rating']):>4}  {bar(s['tracks_with_rating'])}",
    ]

    if "tables" in data:
        lines += ["", "Tables: " + ", ".join(data["tables"])]

    return "\n".join(lines)
