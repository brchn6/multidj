from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..adapters.base import SyncAdapter
from ..db import connect, MIXXX_DB_PATH

DEFAULT_MIXXX_PATH = MIXXX_DB_PATH

# Fields used for change detection between existing track and incoming row.
_COMPARE_FIELDS = (
    "artist", "title", "album", "genre", "bpm", "key",
    "rating", "play_count", "duration", "filesize", "remixer",
)


def _detect_key_column(mixxx_conn: sqlite3.Connection) -> Optional[str]:
    """Return the column name in the `keys` table that holds the Camelot string.

    Inspects PRAGMA table_info(keys). The column is usually named 'key_text'
    but we verify defensively. Returns None if the table doesn't exist.
    """
    try:
        rows = mixxx_conn.execute("PRAGMA table_info(keys)").fetchall()
    except sqlite3.OperationalError:
        return None
    if not rows:
        return None
    col_names = [r[1] for r in rows]  # index 1 is the column name
    # Prefer 'key_text', then any column containing 'text', then second column
    for candidate in ("key_text", "key_name", "text"):
        if candidate in col_names:
            return candidate
    # Fallback: pick the second column (id is first)
    if len(col_names) >= 2:
        return col_names[1]
    return None


def _read_mixxx_tracks(mixxx_conn: sqlite3.Connection) -> list[dict]:
    """Read all non-deleted tracks from the Mixxx library."""
    key_col = _detect_key_column(mixxx_conn)

    if key_col:
        query = f"""
            SELECT
                l.id          AS mixxx_id,
                l.artist,
                l.title,
                l.album,
                l.genre,
                l.bpm,
                l.rating,
                l.timesplayed,
                l.duration,
                l.remixer,
                tl.location   AS path,
                tl.filesize,
                k.{key_col}   AS key
            FROM library l
            JOIN track_locations tl ON l.location = tl.id
            LEFT JOIN keys k ON l.key_id = k.id
            WHERE l.mixxx_deleted = 0
        """
    else:
        query = """
            SELECT
                l.id          AS mixxx_id,
                l.artist,
                l.title,
                l.album,
                l.genre,
                l.bpm,
                l.rating,
                l.timesplayed,
                l.duration,
                l.remixer,
                tl.location   AS path,
                tl.filesize,
                NULL          AS key
            FROM library l
            JOIN track_locations tl ON l.location = tl.id
            WHERE l.mixxx_deleted = 0
        """

    mixxx_conn.row_factory = sqlite3.Row
    rows = mixxx_conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def _row_to_track_tuple(row: dict) -> tuple:
    """Convert a Mixxx row dict to the tuple for INSERT OR REPLACE into tracks."""
    return (
        row["path"],
        row.get("artist"),
        row.get("title"),
        row.get("album"),
        row.get("genre"),
        row.get("bpm"),
        row.get("key"),
        row.get("rating"),
        row.get("timesplayed"),  # -> play_count
        row.get("duration"),
        row.get("filesize"),
        row.get("remixer"),
        0,  # deleted
    )


def _tracks_differ(existing: sqlite3.Row, incoming: dict) -> bool:
    """Return True if any mapped field has changed."""
    mapping = {
        "artist":     incoming.get("artist"),
        "title":      incoming.get("title"),
        "album":      incoming.get("album"),
        "genre":      incoming.get("genre"),
        "bpm":        incoming.get("bpm"),
        "key":        incoming.get("key"),
        "rating":     incoming.get("rating"),
        "play_count": incoming.get("timesplayed"),
        "duration":   incoming.get("duration"),
        "filesize":   incoming.get("filesize"),
        "remixer":    incoming.get("remixer"),
    }
    for field, new_val in mapping.items():
        if existing[field] != new_val:
            return True
    return False


class MixxxAdapter(SyncAdapter):
    def __init__(self, mixxx_db_path: Path | None = None):
        self.mixxx_path = Path(mixxx_db_path) if mixxx_db_path else DEFAULT_MIXXX_PATH

    # ------------------------------------------------------------------
    # import_all
    # ------------------------------------------------------------------

    def import_all(self, multidj_db_path: Path, apply: bool = False) -> dict:
        """Import all non-deleted tracks from Mixxx into the MultiDJ DB.

        In dry-run mode (apply=False): reads Mixxx, returns a summary with
        a sample of the first 5 tracks. Does not open MultiDJ DB writable.

        In apply mode: upserts every track and writes sync_state rows.
        """
        # Open Mixxx DB read-only
        mixxx_uri = f"file:{self.mixxx_path}?mode=ro"
        mixxx_conn = sqlite3.connect(mixxx_uri, uri=True)
        try:
            tracks = _read_mixxx_tracks(mixxx_conn)
        finally:
            mixxx_conn.close()

        if not apply:
            sample = []
            for row in tracks[:5]:
                sample.append({
                    "path":       row.get("path"),
                    "artist":     row.get("artist"),
                    "title":      row.get("title"),
                    "bpm":        row.get("bpm"),
                    "key":        row.get("key"),
                    "play_count": row.get("timesplayed"),
                })
            return {
                "mode":         "dry_run",
                "total_tracks": len(tracks),
                "sample":       sample,
            }

        # ── apply mode ────────────────────────────────────────────────────
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        new_tracks     = 0
        updated_tracks = 0
        unchanged_tracks = 0
        errors: list[dict] = []

        with connect(str(multidj_db_path), readonly=False) as mdj_conn:
            for row in tracks:
                path = row.get("path")
                try:
                    # Check whether this path already exists
                    existing = mdj_conn.execute(
                        "SELECT * FROM tracks WHERE path = ?", (path,)
                    ).fetchone()

                    if existing is None:
                        # New track
                        cur = mdj_conn.execute(
                            """
                            INSERT OR REPLACE INTO tracks
                                (path, artist, title, album, genre, bpm, key,
                                 rating, play_count, duration, filesize, remixer, deleted)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            _row_to_track_tuple(row),
                        )
                        track_id = cur.lastrowid
                        new_tracks += 1
                    else:
                        if _tracks_differ(existing, row):
                            mdj_conn.execute(
                                """
                                INSERT OR REPLACE INTO tracks
                                    (path, artist, title, album, genre, bpm, key,
                                     rating, play_count, duration, filesize, remixer, deleted)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                _row_to_track_tuple(row),
                            )
                            # INSERT OR REPLACE resets id — re-fetch
                            track_id = mdj_conn.execute(
                                "SELECT id FROM tracks WHERE path = ?", (path,)
                            ).fetchone()[0]
                            updated_tracks += 1
                        else:
                            track_id = existing["id"]
                            unchanged_tracks += 1

                    # Upsert sync_state — dirty=0 since we just imported from Mixxx
                    mdj_conn.execute(
                        """
                        INSERT OR REPLACE INTO sync_state (track_id, adapter, dirty, last_synced_at)
                        VALUES (?, 'mixxx', 0, ?)
                        """,
                        (track_id, now_iso),
                    )
                    mdj_conn.commit()

                except Exception as exc:  # noqa: BLE001
                    mdj_conn.rollback()
                    errors.append({"path": path, "error": str(exc)})

        return {
            "mode":             "apply",
            "total_tracks":     len(tracks),
            "new_tracks":       new_tracks,
            "updated_tracks":   updated_tracks,
            "unchanged_tracks": unchanged_tracks,
            "errors":           errors,
        }

    # ------------------------------------------------------------------
    # push_track / full_sync — implemented in Layer 3 (sync mixxx)
    # ------------------------------------------------------------------

    def push_track(self, track: dict, multidj_db_path: Path) -> bool:
        raise NotImplementedError("push_track implemented in sync phase")

    def full_sync(self, multidj_db_path: Path, apply: bool = False) -> dict:
        raise NotImplementedError("full_sync implemented in sync phase")
