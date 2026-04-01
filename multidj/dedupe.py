from __future__ import annotations

from typing import Any

from .backup import create_backup
from .db import connect, table_exists, ensure_not_empty


def _keeper_sort_key(track: dict) -> tuple:
    """Prefer most-played, then highest-rated, then largest file."""
    return (
        -(track["play_count"] or 0),
        -(track["rating"] or 0),
        -(track["filesize"] or 0),
    )


def _find_groups(db_path: str | None, by: str) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []

    with connect(db_path, readonly=True) as conn:
        if by in ("artist-title", "both"):
            rows = conn.execute("""
                SELECT
                    LOWER(TRIM(COALESCE(artist, ''))) AS norm_artist,
                    LOWER(TRIM(COALESCE(title,  ''))) AS norm_title,
                    id, artist, title, rating, play_count, duration,
                    path AS filepath, filesize
                FROM tracks
                WHERE deleted = 0
                ORDER BY norm_artist, norm_title
            """).fetchall()

            seen: dict[tuple[str, str], list[dict]] = {}
            for row in rows:
                na, nt = row["norm_artist"], row["norm_title"]
                if not na and not nt:
                    continue
                seen.setdefault((na, nt), []).append({
                    "track_id": row["id"],
                    "artist": row["artist"],
                    "title": row["title"],
                    "rating": row["rating"],
                    "play_count": row["play_count"],
                    "duration": row["duration"],
                    "filepath": row["filepath"],
                    "filesize": row["filesize"],
                })

            for (na, nt), tracks in seen.items():
                if len(tracks) > 1:
                    groups.append({
                        "match_key": f"{na} — {nt}",
                        "match_type": "artist_title",
                        "tracks": tracks,
                    })

        if by in ("filesize", "both"):
            rows = conn.execute("""
                SELECT
                    id, artist, title, rating, play_count, duration,
                    path AS filepath, filesize
                FROM tracks
                WHERE deleted = 0
                  AND filesize IS NOT NULL AND filesize > 0
                ORDER BY filesize
            """).fetchall()

            seen_fs: dict[tuple[int, Any], list[dict]] = {}
            for row in rows:
                key = (row["filesize"], row["duration"])
                seen_fs.setdefault(key, []).append({
                    "track_id": row["id"],
                    "artist": row["artist"],
                    "title": row["title"],
                    "rating": row["rating"],
                    "play_count": row["play_count"],
                    "duration": row["duration"],
                    "filepath": row["filepath"],
                    "filesize": row["filesize"],
                })

            for (filesize, duration), tracks in seen_fs.items():
                if len(tracks) > 1:
                    groups.append({
                        "match_key": f"size={filesize} duration={duration}",
                        "match_type": "filesize_duration",
                        "tracks": tracks,
                    })

    return groups


def dedupe(
    db_path: str | None = None,
    by: str = "both",
    apply: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)

    mode = "apply" if apply else "dry_run"
    all_groups = _find_groups(db_path, by)

    groups_output: list[dict[str, Any]] = []
    removed_ids: list[int] = []
    seen_removed: set[int] = set()

    for group in all_groups:
        sorted_tracks = sorted(group["tracks"], key=_keeper_sort_key)
        keeper = sorted_tracks[0]
        duplicates = sorted_tracks[1:]

        # Only schedule tracks not already marked for removal by a prior group.
        new_dups = [d for d in duplicates if d["track_id"] not in seen_removed]
        for dup in new_dups:
            seen_removed.add(dup["track_id"])
            removed_ids.append(dup["track_id"])

        groups_output.append({
            "match_key": group["match_key"],
            "match_type": group["match_type"],
            "total_tracks": len(sorted_tracks),
            "keeper": {
                "track_id": keeper["track_id"],
                "artist": keeper["artist"],
                "title": keeper["title"],
                "play_count": keeper["play_count"],
                "rating": keeper["rating"],
                "filesize": keeper["filesize"],
                "filepath": keeper["filepath"],
            },
            "duplicates": [
                {
                    "track_id": d["track_id"],
                    "artist": d["artist"],
                    "title": d["title"],
                    "play_count": d["play_count"],
                    "rating": d["rating"],
                    "filesize": d["filesize"],
                    "filepath": d["filepath"],
                }
                for d in new_dups
            ],
        })

    if apply and removed_ids:
        if backup:
            create_backup(db_path)
        with connect(db_path, readonly=False) as conn:
            conn.executemany(
                "UPDATE tracks SET deleted = 1 WHERE id = ?",
                [(tid,) for tid in removed_ids],
            )
            conn.commit()

    return {
        "mode": mode,
        "by": by,
        "total_groups": len(groups_output),
        "total_removed": len(removed_ids),
        "groups": groups_output,
    }
