from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .backup import create_backup
from .constants import UNINFORMATIVE_GENRES
from .db import connect

_ACTIVE = "mixxx_deleted = 0"
_COLLAPSE_SPACES = re.compile(r"  +")


def clean_genres(
    db_path: str | None = None,
    apply: bool = False,
    limit: int | None = None,
    backup: bool = True,
) -> dict[str, Any]:
    mode = "apply" if apply else "dry_run"

    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(
            f"SELECT id, genre FROM library"
            f" WHERE {_ACTIVE} AND genre IS NOT NULL AND TRIM(genre) != ''"
        ).fetchall()

    # Group by normalized (stripped + lowercased) key.
    groups: dict[str, list[tuple[int, str]]] = {}
    for row in rows:
        key = row["genre"].strip().lower()
        groups.setdefault(key, []).append((row["id"], row["genre"]))

    planned: list[dict[str, Any]] = []

    for norm_key, entries in groups.items():
        # Null uninformative genres.
        if norm_key in UNINFORMATIVE_GENRES:
            for track_id, genre in entries:
                planned.append({
                    "track_id": track_id,
                    "old_genre": genre,
                    "new_genre": None,
                    "reason": "uninformative",
                })
            continue

        # Fix whitespace on each entry.
        for track_id, genre in entries:
            stripped = genre.strip()
            if stripped != genre:
                planned.append({
                    "track_id": track_id,
                    "old_genre": genre,
                    "new_genre": stripped,
                    "reason": "whitespace",
                })

        # Collapse case variants: pick most-common as canonical.
        variant_counts: Counter[str] = Counter(genre.strip() for _, genre in entries)
        if len(variant_counts) > 1:
            canonical = variant_counts.most_common(1)[0][0]
            for track_id, genre in entries:
                if genre.strip() != canonical:
                    planned.append({
                        "track_id": track_id,
                        "old_genre": genre,
                        "new_genre": canonical,
                        "reason": "case_variant",
                    })

    if limit is not None:
        planned = planned[:limit]

    if apply and planned:
        if backup:
            create_backup(db_path)
        with connect(db_path, readonly=False) as conn:
            for change in planned:
                conn.execute(
                    "UPDATE library SET genre = ? WHERE id = ?",
                    (change["new_genre"], change["track_id"]),
                )
            conn.commit()

    return {
        "mode": mode,
        "total_changes": len(planned),
        "changes": planned,
    }


def clean_text(
    db_path: str | None = None,
    apply: bool = False,
    limit: int | None = None,
    backup: bool = True,
) -> dict[str, Any]:
    mode = "apply" if apply else "dry_run"

    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(f"""
            SELECT id, artist, title, album FROM library
            WHERE {_ACTIVE}
              AND (
                (artist IS NOT NULL AND artist != TRIM(artist))
                OR (title  IS NOT NULL AND title  != TRIM(title))
                OR (album  IS NOT NULL AND album  != TRIM(album))
                OR (artist LIKE '%  %')
                OR (title  LIKE '%  %')
                OR (album  LIKE '%  %')
              )
        """).fetchall()

    planned: list[dict[str, Any]] = []

    for row in rows:
        change: dict[str, Any] = {"track_id": row["id"]}
        has_change = False
        for field in ("artist", "title", "album"):
            val = row[field]
            if val is None:
                continue
            cleaned = _COLLAPSE_SPACES.sub(" ", val.strip())
            if cleaned != val:
                change[f"old_{field}"] = val
                change[f"new_{field}"] = cleaned
                has_change = True
        if has_change:
            planned.append(change)

    if limit is not None:
        planned = planned[:limit]

    if apply and planned:
        if backup:
            create_backup(db_path)
        with connect(db_path, readonly=False) as conn:
            for change in planned:
                track_id = change["track_id"]
                for field in ("artist", "title", "album"):
                    if f"new_{field}" in change:
                        conn.execute(
                            f"UPDATE library SET {field} = ? WHERE id = ?",
                            (change[f"new_{field}"], track_id),
                        )
            conn.commit()

    return {
        "mode": mode,
        "total_changes": len(planned),
        "changes": planned,
    }
