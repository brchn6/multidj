from __future__ import annotations

from typing import Any

from .backup import create_backup
from .constants import AUTO_CRATE_PREFIXES, BPM_RANGES, CATCH_ALL_CRATE_NAMES, REBUILD_CRATE_RE
from .enrich import is_hebrew
from .db import connect, table_exists, ensure_not_empty

MIN_TRACKS_DEFAULT = 5


def _classify(name: str) -> str:
    """Return 'catch-all', 'auto', or 'hand-curated'."""
    if name in CATCH_ALL_CRATE_NAMES:
        return "catch-all"
    if AUTO_CRATE_PREFIXES.match(name or ""):
        return "auto"
    return "hand-curated"


def _fetch_crates(conn) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT c.id, c.name, c.show, c.type,
               COUNT(ct.track_id) AS track_count
        FROM crates c
        LEFT JOIN crate_tracks ct ON c.id = ct.crate_id
        GROUP BY c.id
        ORDER BY track_count DESC, c.name
    """).fetchall()
    return [
        {
            "crate_id": r["id"],
            "name": r["name"],
            "visible": bool(r["show"]),
            "track_count": r["track_count"],
            "type": r["type"],
        }
        for r in rows
    ]


def audit_crates(
    db_path: str | None = None,
    min_tracks: int = MIN_TRACKS_DEFAULT,
    summary_only: bool = False,
) -> dict[str, Any]:
    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)
        crates = _fetch_crates(conn)

    # Exclude catch-alls from threshold analysis (they're not real collections).
    real_crates = [c for c in crates if c["type"] != "catch-all"]
    catch_alls = [c for c in crates if c["type"] == "catch-all"]

    below = [c for c in real_crates if c["track_count"] < min_tracks]
    above = [c for c in real_crates if c["track_count"] >= min_tracks]

    hand_curated_below = [c for c in below if c["type"] == "hand-curated"]
    auto_below = [c for c in below if c["type"] == "auto"]

    result: dict[str, Any] = {
        "min_tracks_threshold": min_tracks,
        "total_crates": len(crates),
        "catch_all_crates": len(catch_alls),
        "above_threshold": len(above),
        "below_threshold": len(below),
        "below_hand_curated": len(hand_curated_below),
        "below_auto": len(auto_below),
    }

    if not summary_only:
        result["crates_above"] = above
        result["crates_below_hand_curated"] = hand_curated_below
        result["crates_below_auto"] = auto_below

    return result


def hide_crates(
    db_path: str | None = None,
    min_tracks: int = MIN_TRACKS_DEFAULT,
    apply: bool = False,
    backup: bool = True,
    include_hand_curated: bool = False,
) -> dict[str, Any]:
    """Hide (show=0) crates below min_tracks. Hand-curated and catch-all crates are protected."""
    mode = "apply" if apply else "dry_run"

    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)
        crates = _fetch_crates(conn)

    candidates = [
        c for c in crates
        if c["track_count"] < min_tracks
        and c["visible"]
        and c["type"] != "catch-all"
        and (include_hand_curated or c["type"] == "auto")
    ]

    protected = [
        c for c in crates
        if c["track_count"] < min_tracks
        and c["type"] == "hand-curated"
        and not include_hand_curated
    ]

    if apply and candidates:
        if backup:
            create_backup(db_path)
        ids = [c["crate_id"] for c in candidates]
        with connect(db_path, readonly=False) as conn:
            conn.execute(
                f"UPDATE crates SET show = 0 WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            conn.commit()

    return {
        "mode": mode,
        "min_tracks_threshold": min_tracks,
        "total_hidden": len(candidates),
        "protected_hand_curated": len(protected),
        "hidden": candidates,
        "protected": protected,
    }


def show_crates(
    db_path: str | None = None,
    min_tracks: int | None = None,
    apply: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    """Restore hidden crates (optionally only those now meeting min_tracks)."""
    mode = "apply" if apply else "dry_run"

    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)
        crates = _fetch_crates(conn)

    candidates = [
        c for c in crates
        if not c["visible"]
        and (min_tracks is None or c["track_count"] >= min_tracks)
    ]

    if apply and candidates:
        if backup:
            create_backup(db_path)
        ids = [c["crate_id"] for c in candidates]
        with connect(db_path, readonly=False) as conn:
            conn.execute(
                f"UPDATE crates SET show = 1 WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            conn.commit()

    return {
        "mode": mode,
        "total_restored": len(candidates),
        "restored": candidates,
    }


def delete_crates(
    db_path: str | None = None,
    min_tracks: int = MIN_TRACKS_DEFAULT,
    apply: bool = False,
    backup: bool = True,
    include_hand_curated: bool = False,
) -> dict[str, Any]:
    """Permanently delete auto-generated crates below min_tracks."""
    mode = "apply" if apply else "dry_run"

    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)
        crates = _fetch_crates(conn)

    candidates = [
        c for c in crates
        if c["track_count"] < min_tracks
        and c["type"] != "catch-all"
        and (include_hand_curated or c["type"] == "auto")
    ]

    protected = [
        c for c in crates
        if c["track_count"] < min_tracks
        and c["type"] == "hand-curated"
        and not include_hand_curated
    ]

    if apply and candidates:
        if backup:
            create_backup(db_path)
        ids = [c["crate_id"] for c in candidates]
        ph = ",".join("?" * len(ids))
        with connect(db_path, readonly=False) as conn:
            conn.execute(f"DELETE FROM crate_tracks WHERE crate_id IN ({ph})", ids)
            conn.execute(f"DELETE FROM crates WHERE id IN ({ph})", ids)
            conn.commit()

    return {
        "mode": mode,
        "min_tracks_threshold": min_tracks,
        "total_deleted": len(candidates),
        "protected_hand_curated": len(protected),
        "deleted": candidates,
        "protected": protected,
    }


def rebuild_crates(
    db_path: str | None = None,
    min_tracks: int = MIN_TRACKS_DEFAULT,
    apply: bool = False,
    backup: bool = True,
    backup_dir: str | None = None,
) -> dict[str, Any]:
    """
    Rebuild auto-crates from scratch:
      1. Delete all existing Genre:, BPM:, and Lang: crates (and their track assignments).
      2. Create Genre: <canonical> crates for genres with >= min_tracks active tracks.
      3. Create Lang: Hebrew crate if >= min_tracks Hebrew tracks exist.
    Hand-curated and catch-all crates are never touched.
    """
    mode = "apply" if apply else "dry_run"

    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)

        # --- existing auto-crates to remove ---
        old_auto = [
            {"crate_id": r["id"], "name": r["name"]}
            for r in conn.execute("SELECT id, name FROM crates").fetchall()
            if REBUILD_CRATE_RE.match(r["name"] or "")
        ]

        # --- genre groups above threshold ---
        genre_rows = conn.execute("""
            SELECT genre, COUNT(*) AS cnt
            FROM tracks
            WHERE deleted = 0
              AND genre IS NOT NULL AND TRIM(genre) != ''
            GROUP BY genre
            HAVING cnt >= ?
            ORDER BY cnt DESC, genre
        """, (min_tracks,)).fetchall()
        genre_groups = [{"name": f"Genre: {r['genre']}", "genre": r["genre"], "count": r["cnt"]}
                        for r in genre_rows]

        # --- Hebrew tracks ---
        all_tracks = conn.execute("""
            SELECT id, artist, title FROM tracks WHERE deleted = 0
        """).fetchall()
        hebrew_ids = [r["id"] for r in all_tracks
                      if is_hebrew(r["title"]) or is_hebrew(r["artist"])]

        # --- track id lookup by genre ---
        genre_track_map: dict[str, list[int]] = {}
        for g in genre_groups:
            track_ids = [
                r["id"] for r in conn.execute("""
                    SELECT id FROM tracks
                    WHERE deleted = 0 AND genre = ?
                """, (g["genre"],)).fetchall()
            ]
            genre_track_map[g["genre"]] = track_ids

    # Build crates-to-create list
    crates_to_create: list[dict[str, Any]] = []
    for g in genre_groups:
        crates_to_create.append({
            "name": g["name"],
            "track_ids": genre_track_map[g["genre"]],
            "track_count": g["count"],
        })

    lang_crate: dict[str, Any] | None = None
    if len(hebrew_ids) >= min_tracks:
        lang_crate = {
            "name": "Lang: Hebrew",
            "track_ids": hebrew_ids,
            "track_count": len(hebrew_ids),
        }
        crates_to_create.append(lang_crate)

    # Re-query skipped genres for reporting (read-only conn above is already closed)
    with connect(db_path, readonly=True) as conn:
        skipped_rows = conn.execute("""
            SELECT genre, COUNT(*) AS cnt
            FROM tracks
            WHERE deleted = 0
              AND genre IS NOT NULL AND TRIM(genre) != ''
            GROUP BY genre
            HAVING cnt < ?
            ORDER BY cnt DESC
        """, (min_tracks,)).fetchall()
        skipped_below = [{"genre": r["genre"], "count": r["cnt"]} for r in skipped_rows]

    # Compute total track assignments (useful for both dry-run preview and apply confirmation).
    total_assignments = sum(len(c["track_ids"]) for c in crates_to_create)

    bpm_crates_created = 0
    bpm_tracks_added = 0

    if apply:
        if backup:
            create_backup(db_path, backup_dir=backup_dir)
        with connect(db_path, readonly=False) as conn:
            # 1. Delete old auto-crates (entire operation is one implicit transaction).
            if old_auto:
                old_ids = [c["crate_id"] for c in old_auto]
                ph = ",".join("?" * len(old_ids))
                conn.execute(f"DELETE FROM crate_tracks WHERE crate_id IN ({ph})", old_ids)
                conn.execute(f"DELETE FROM crates WHERE id IN ({ph})", old_ids)

            # 2. Create new Genre/Lang crates and populate.
            for crate in crates_to_create:
                cursor = conn.execute(
                    "INSERT INTO crates (name, type, show) VALUES (?, 'auto', 1)",
                    (crate["name"],),
                )
                crate_id = cursor.lastrowid
                pairs = [(crate_id, tid) for tid in crate["track_ids"]]
                conn.executemany(
                    "INSERT OR IGNORE INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
                    pairs,
                )

            # 3. Create BPM-range crates.
            for crate_name, bpm_low, bpm_high in BPM_RANGES:
                track_ids = [
                    r["id"] for r in conn.execute(
                        """
                        SELECT id FROM tracks
                        WHERE deleted = 0
                          AND bpm IS NOT NULL
                          AND bpm >= ? AND bpm < ?
                        """,
                        (bpm_low, bpm_high),
                    ).fetchall()
                ]
                if not track_ids:
                    continue  # don't create empty crates

                conn.execute(
                    "INSERT OR IGNORE INTO crates (name, type, show) VALUES (?, 'auto', 1)",
                    (crate_name,),
                )
                crate_id = conn.execute(
                    "SELECT id FROM crates WHERE name = ?", (crate_name,)
                ).fetchone()[0]

                conn.executemany(
                    "INSERT OR IGNORE INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
                    [(crate_id, tid) for tid in track_ids],
                )
                bpm_crates_created += 1
                bpm_tracks_added += len(track_ids)

            conn.commit()

    return {
        "mode": mode,
        "min_tracks_threshold": min_tracks,
        "old_auto_crates_deleted": len(old_auto),
        "crates_created": len(crates_to_create),
        "total_assignments": total_assignments,
        "genre_crates": len(genre_groups),
        "lang_hebrew_crate": lang_crate is not None,
        "lang_hebrew_tracks": len(hebrew_ids),
        "skipped_genres_below_threshold": len(skipped_below),
        "skipped_genres": skipped_below,
        "bpm_crates_created": bpm_crates_created,
        "bpm_tracks_added": bpm_tracks_added,
        "crates": [{"name": c["name"], "track_count": c["track_count"]}
                   for c in crates_to_create],
        "deleted": old_auto,
    }
