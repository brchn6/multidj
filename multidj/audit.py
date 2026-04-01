from __future__ import annotations

import sqlite3
from typing import Any

from .constants import EMOJI_OR_SYMBOL_RE, UNINFORMATIVE_GENRES
from .db import connect, table_exists, ensure_not_empty

_ACTIVE = "deleted = 0"


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
