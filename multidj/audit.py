from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from .constants import EMOJI_OR_SYMBOL_RE, UNINFORMATIVE_GENRES
from .db import connect, table_exists, ensure_not_empty

_ACTIVE = "deleted = 0"
_TITLE_ARTIST_FILENAME_RE = re.compile(r"^\s*(?:\d+\s*-\s*)?(.*?)\s*-\s*(.*?)\s*$")


def _norm_text(value: str | None) -> str:
    return (value or "").strip().casefold()


def detect_title_artist_swap_mismatch(path: str, artist: str | None, title: str | None) -> dict[str, Any] | None:
    """Detect rows where filename follows Title - Artist but tags are Artist=Title and Title=Artist."""
    if not path:
        return None

    stem = Path(path).stem
    match = _TITLE_ARTIST_FILENAME_RE.match(stem)
    if not match:
        return None

    parsed_title = match.group(1).strip()
    parsed_artist = match.group(2).strip()
    if not parsed_title or not parsed_artist:
        return None

    artist_norm = _norm_text(artist)
    title_norm = _norm_text(title)
    parsed_title_norm = _norm_text(parsed_title)
    parsed_artist_norm = _norm_text(parsed_artist)

    is_swapped = artist_norm == parsed_title_norm and title_norm == parsed_artist_norm
    if not is_swapped:
        return None

    return {
        "parsed_title": parsed_title,
        "parsed_artist": parsed_artist,
        "suggested_artist": parsed_artist,
        "suggested_title": parsed_title,
    }


def _fetch_value_counts(conn: sqlite3.Connection, field: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT {field} AS value, COUNT(*) AS count
        FROM tracks
        WHERE {_ACTIVE} AND {field} IS NOT NULL AND TRIM({field}) != ''
        GROUP BY {field}
        ORDER BY count DESC, value ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [{"value": row["value"], "count": int(row["count"])} for row in rows]


def audit_genres(db_path: str | None = None, top_n: int = 100) -> dict[str, Any]:
    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)

        rows = conn.execute(
            f"""
            SELECT genre, COUNT(*) AS count
            FROM tracks
            WHERE {_ACTIVE} AND genre IS NOT NULL AND TRIM(genre) != ''
            GROUP BY genre
            ORDER BY count DESC, genre ASC
            """
        ).fetchall()

        top_values = [{"genre": row["genre"], "count": int(row["count"])} for row in rows[:top_n]]

        multi_value = []
        case_collisions: dict[str, list[str]] = {}
        uninformative = []
        suspicious = []

        for row in rows:
            genre = row["genre"]
            count = int(row["count"])
            genre_norm = genre.strip().lower()

            if "," in genre or "/" in genre:
                multi_value.append({"genre": genre, "count": count})

            case_collisions.setdefault(genre_norm, []).append(genre)

            if genre_norm in UNINFORMATIVE_GENRES:
                uninformative.append({"genre": genre, "count": count})

            if EMOJI_OR_SYMBOL_RE.match(genre.strip()):
                suspicious.append({"genre": genre, "count": count, "reason": "emoji_or_symbol_only"})
            elif len(genre.split(",")) >= 4:
                suspicious.append({"genre": genre, "count": count, "reason": "long_multi_value_string"})

        collision_list = [
            {"normalized": norm, "variants": sorted(set(originals))}
            for norm, originals in case_collisions.items()
            if len(set(originals)) > 1
        ]

        return {
            "top_genres": top_values,
            "multi_value_genres": multi_value,
            "case_collisions": sorted(collision_list, key=lambda x: x["normalized"]),
            "uninformative_genres": uninformative,
            "suspicious_genres": suspicious,
        }


def audit_metadata(db_path: str | None = None) -> dict[str, Any]:
    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)

        total_tracks = conn.execute(
            f"SELECT COUNT(*) FROM tracks WHERE {_ACTIVE}"
        ).fetchone()[0]

        def coverage(field: str) -> dict[str, Any]:
            present = conn.execute(
                f"SELECT COUNT(*) FROM tracks"
                f" WHERE {_ACTIVE} AND {field} IS NOT NULL AND TRIM(CAST({field} AS TEXT)) != ''"
            ).fetchone()[0]
            return {
                "field": field,
                "present": int(present),
                "missing": int(total_tracks - present),
                "coverage_pct": round(present / total_tracks * 100.0, 2) if total_tracks else 0.0,
            }

        return {
            "total_tracks": int(total_tracks),
            "coverage": [coverage(f) for f in ("artist", "title", "genre", "bpm", "key", "album")],
            "top_genres": _fetch_value_counts(conn, "genre", limit=20),
        }


def audit_mismatches(db_path: str | None = None, limit: int = 100) -> dict[str, Any]:
    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)

        rows = conn.execute(
            f"""
            SELECT id, path, artist, title
            FROM tracks
            WHERE {_ACTIVE}
              AND path IS NOT NULL
              AND artist IS NOT NULL AND TRIM(artist) != ''
              AND title IS NOT NULL AND TRIM(title) != ''
            ORDER BY id ASC
            """
        ).fetchall()

    mismatches: list[dict[str, Any]] = []
    for row in rows:
        mismatch = detect_title_artist_swap_mismatch(row["path"], row["artist"], row["title"])
        if mismatch is None:
            continue
        mismatches.append({
            "track_id": int(row["id"]),
            "path": row["path"],
            "artist": row["artist"],
            "title": row["title"],
            **mismatch,
            "reason": "filename_title_artist_swap",
        })
        if len(mismatches) >= limit:
            break

    return {
        "total_candidates": len(rows),
        "total_mismatches": len(mismatches),
        "mismatches": mismatches,
    }


def fix_mismatches(
    db_path: str | None = None,
    apply: bool = False,
    backup: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Detect and optionally correct artist/title swap mismatches across all active tracks.

    This is the pipeline-safe version of audit_mismatches — it reads all rows,
    finds swaps, and writes the corrections in a single pass when apply=True.
    No backup by default (pipeline takes one at the start).
    """
    from .backup import create_backup

    if apply and backup:
        create_backup(db_path)

    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)
        rows = conn.execute(
            f"""
            SELECT id, path, artist, title
            FROM tracks
            WHERE {_ACTIVE}
              AND path IS NOT NULL
              AND artist IS NOT NULL AND TRIM(artist) != ''
              AND title IS NOT NULL AND TRIM(title) != ''
            ORDER BY id ASC
            """
        ).fetchall()

    fixes: list[dict[str, Any]] = []
    for row in rows:
        mismatch = detect_title_artist_swap_mismatch(row["path"], row["artist"], row["title"])
        if mismatch is None:
            continue
        fixes.append({
            "track_id": int(row["id"]),
            "old_artist": row["artist"],
            "old_title": row["title"],
            "new_artist": mismatch["suggested_artist"],
            "new_title": mismatch["suggested_title"],
        })

    if apply and fixes:
        if limit is not None:
            fixes = fixes[:limit]
        with connect(db_path, readonly=False) as conn:
            conn.executemany(
                "UPDATE tracks SET artist=?, title=? WHERE id=?",
                [(f["new_artist"], f["new_title"], f["track_id"]) for f in fixes],
            )
            conn.commit()

    if not apply and limit is not None:
        fixes = fixes[:limit]

    return {
        "mode": "apply" if apply else "dry_run",
        "total_candidates": len(rows),
        "total_fixed": len(fixes),
        "fixes": fixes,
    }
