"""Import Mixxx analysis results (BPM, key) into MultiDJ tracks table.

Reads library.bpm, library.key, and library.bpm_lock directly from the
Mixxx SQLite database and populates the corresponding MultiDJ tracks
columns. This captures Mixxx's own analysis output — the ground truth
from the Queen Mary Vamp engine — without needing the GUI.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

from .db import connect as multidj_connect
from .db import ensure_not_empty
from .db import MIXXX_DB_PATH


def _progress(msg: str, end: str = "\n") -> None:
    print(msg, file=sys.stderr, end=end, flush=True)


def import_mixxx_analysis(
    multidj_db_path: str | None = None,
    mixxx_db_path: str | None = None,
    *,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    backup_dir: str | None = None,
) -> dict[str, Any]:
    """Import BPM and key from Mixxx DB into MultiDJ tracks table.

    Args:
        multidj_db_path: Path to MultiDJ SQLite DB.
        mixxx_db_path: Path to Mixxx SQLite DB.
        apply: If False (default), dry-run: report what would change.
        force: If True, overwrite existing BPM/key values in MultiDJ.
        limit: Cap number of tracks processed.
        backup_dir: Directory for MultiDJ DB backup, or None to use default.

    Returns:
        Dict with counts and per-track details.
    """
    if not mixxx_db_path:
        mixxx_path = MIXXX_DB_PATH
    else:
        mixxx_path = Path(mixxx_db_path).expanduser()

    if not mixxx_path.exists():
        return {
            "status": "error",
            "reason": f"Mixxx DB not found: {mixxx_path}",
        }

    # Read from Mixxx DB (read-only)
    try:
        mixxx_conn = sqlite3.connect(f"file:{mixxx_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {"status": "error", "reason": f"Cannot open Mixxx DB: {exc}"}

    try:
        query = """
            SELECT
                tl.location  AS path,
                l.artist,
                l.title,
                l.bpm,
                l.key,
                l.bpm_lock
            FROM library l
            JOIN track_locations tl ON l.location = tl.id
            WHERE l.mixxx_deleted = 0
              AND l.bpm IS NOT NULL
              AND l.bpm > 0
            ORDER BY l.artist, l.title
        """
        if limit is not None:
            query += f" LIMIT {int(limit)}"

        mixxx_rows = mixxx_conn.execute(query).fetchall()
    finally:
        mixxx_conn.close()

    total_candidates = len(mixxx_rows)

    if total_candidates == 0:
        return {
            "status": "ok",
            "applied": False,
            "total_candidates": 0,
            "matched": 0,
            "written_bpm": 0,
            "written_key": 0,
            "skipped_has_bpm": 0,
            "skipped_has_key": 0,
            "skipped_no_match": 0,
            "errors": [],
            "details": [],
        }

    mode = "apply" if apply else "dry_run"

    # Guard: ensure MultiDJ DB is not empty
    try:
        with multidj_connect(multidj_db_path, readonly=True) as guard:
            ensure_not_empty(guard)
    except (FileNotFoundError, RuntimeError) as exc:
        return {"status": "error", "reason": str(exc)}

    if not apply:
        _progress(
            f"Dry-run: {total_candidates:,} Mixxx tracks have analysis "
            f"(run with --apply to import)"
        )

        if limit:
            _progress(f"  (limited to first {limit} tracks)")

        # Show sample
        matched_sample = 0
        skipped_sample = 0
        with multidj_connect(multidj_db_path, readonly=True) as mdj_conn:
            for row in mixxx_rows[:5]:
                path = row[0]
                exists = mdj_conn.execute(
                    "SELECT bpm, key FROM tracks WHERE path = ? AND deleted = 0",
                    (path,),
                ).fetchone()
                if exists:
                    matched_sample += 1
                else:
                    skipped_sample += 1

        return {
            "status": "ok",
            "mode": mode,
            "applied": False,
            "total_candidates": total_candidates,
            "matched_sample": matched_sample,
            "skipped_sample": skipped_sample,
            "matched": 0,
            "written_bpm": 0,
            "written_key": 0,
            "skipped_has_bpm": 0,
            "skipped_has_key": 0,
            "skipped_no_match": 0,
            "errors": [],
            "details": [],
        }

    # ── apply mode ─────────────────────────────────────────────────────────
    from .backup import create_backup

    if backup_dir is not False:
        try:
            create_backup(multidj_db_path, backup_dir)
        except Exception as exc:
            return {"status": "error", "reason": f"Backup failed: {exc}"}

    written_bpm = 0
    written_key = 0
    skipped_has_bpm = 0
    skipped_has_key = 0
    skipped_no_match = 0
    errors: list[dict] = []
    details: list[dict] = []

    total = total_candidates
    _progress(f"Importing analysis for up to {total:,} tracks...")

    with multidj_connect(multidj_db_path, readonly=False) as mdj_conn:
        for i, row in enumerate(mixxx_rows, 1):
            path = row[0]
            artist = row[1] or ""
            title = row[2] or ""
            mixxx_bpm = row[3]
            mixxx_key = row[4]
            # row[5] = bpm_lock (informational, not used)

            label = f"{artist} - {title}".strip(" -") or path
            _progress(f"[{i:{len(str(total))}}/{total}] {label[:60]}", end="")

            try:
                existing = mdj_conn.execute(
                    "SELECT id, bpm, key FROM tracks WHERE path = ? AND deleted = 0",
                    (path,),
                ).fetchone()

                if not existing:
                    skipped_no_match += 1
                    _progress("  → not in MultiDJ")
                    continue

                track_id = existing[0]
                existing_bpm = existing[1]
                existing_key = existing[2]

                detail: dict[str, Any] = {
                    "track_id": track_id,
                    "path": path,
                    "artist": artist,
                    "title": title,
                    "bpm": mixxx_bpm,
                    "key": mixxx_key,
                }
                wrote_bpm = False
                wrote_key = False

                # Write BPM if missing or forced
                bpm_needs_write = (
                    existing_bpm is None or existing_bpm == 0 or force
                )
                if bpm_needs_write and mixxx_bpm is not None and mixxx_bpm > 0:
                    mdj_conn.execute(
                        "UPDATE tracks SET bpm = ? WHERE id = ?",
                        (mixxx_bpm, track_id),
                    )
                    written_bpm += 1
                    wrote_bpm = True
                elif not bpm_needs_write and mixxx_bpm is not None and mixxx_bpm > 0:
                    skipped_has_bpm += 1

                # Write key if missing or forced
                key_needs_write = (
                    existing_key is None
                    or str(existing_key).strip() == ""
                    or force
                )
                if key_needs_write and mixxx_key and str(mixxx_key).strip():
                    mdj_conn.execute(
                        "UPDATE tracks SET key = ? WHERE id = ?",
                        (mixxx_key, track_id),
                    )
                    written_key += 1
                    wrote_key = True
                elif not key_needs_write and mixxx_key and str(mixxx_key).strip():
                    skipped_has_key += 1

                detail["wrote_bpm"] = wrote_bpm
                detail["wrote_key"] = wrote_key
                details.append(detail)

                parts = []
                if wrote_bpm:
                    parts.append(f"BPM={mixxx_bpm}")
                if wrote_key:
                    parts.append(f"key={mixxx_key}")
                if parts:
                    _progress(f"  → {', '.join(parts)}")
                else:
                    _progress("  → skipped (already set)")
            except Exception as exc:
                _progress(f"  ERROR: {exc}")
                errors.append({"path": path, "error": str(exc)})

        mdj_conn.commit()

    return {
        "status": "ok",
        "mode": mode,
        "applied": True,
        "total_candidates": total_candidates,
        "matched": len(details),
        "written_bpm": written_bpm,
        "written_key": written_key,
        "skipped_has_bpm": skipped_has_bpm,
        "skipped_has_key": skipped_has_key,
        "skipped_no_match": skipped_no_match,
        "errors": errors,
        "details": details,
    }
