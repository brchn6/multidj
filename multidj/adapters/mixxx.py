from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..adapters.base import SyncAdapter
from ..backup import create_backup
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
                NULL          AS remixer,
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
                NULL          AS remixer,
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


_AUTO_CRATE_RE = re.compile(
    r"^(Genre:\s|BPM:\s|Lang:\s|Key:\s|Energy:\s)", re.IGNORECASE
)


def _push_crates_to_mixxx(
    mdj_conn: sqlite3.Connection,
    mixxx_conn: sqlite3.Connection,
) -> dict[str, int]:
    """Push all visible MultiDJ crates to Mixxx with full reconciliation.

    - Creates missing crates in Mixxx by name.
    - Reconciles crate_tracks: clears and repopulates each synced crate.
    - Deletes auto-crates from Mixxx (Genre:, BPM:, Lang:, Key:, Energy:)
      that no longer exist in MultiDJ.
    - Never deletes non-auto crates created directly in Mixxx.
    """
    mdj_crates = mdj_conn.execute(
        "SELECT id, name FROM crates WHERE show = 1"
    ).fetchall()
    mdj_crate_names = {c["name"] for c in mdj_crates}

    # 1. Remove auto-crates from Mixxx that no longer exist in MultiDJ
    mx_crates = mixxx_conn.execute("SELECT id, name FROM crates").fetchall()
    for mc in mx_crates:
        name = mc[1]
        mx_id = mc[0]
        if _AUTO_CRATE_RE.match(name or "") and name not in mdj_crate_names:
            mixxx_conn.execute("DELETE FROM crate_tracks WHERE crate_id = ?", (mx_id,))
            mixxx_conn.execute("DELETE FROM crates WHERE id = ?", (mx_id,))

    crates_created = 0
    tracks_pushed = 0

    # 2. For each MultiDJ crate: upsert in Mixxx and reconcile membership
    for crate in mdj_crates:
        mdj_crate_id = crate["id"]
        crate_name = crate["name"]

        existing = mixxx_conn.execute(
            "SELECT id FROM crates WHERE name = ?", (crate_name,)
        ).fetchone()

        if existing:
            mx_crate_id = existing[0]
        else:
            cur = mixxx_conn.execute(
                "INSERT INTO crates (name, show) VALUES (?, 1)", (crate_name,)
            )
            mx_crate_id = cur.lastrowid
            crates_created += 1

        # Clear existing membership and repopulate (simpler than diff)
        mixxx_conn.execute(
            "DELETE FROM crate_tracks WHERE crate_id = ?", (mx_crate_id,)
        )

        mdj_tracks = mdj_conn.execute(
            """
            SELECT t.path FROM tracks t
            JOIN crate_tracks ct ON t.id = ct.track_id
            WHERE ct.crate_id = ? AND t.deleted = 0
            """,
            (mdj_crate_id,),
        ).fetchall()

        for track_row in mdj_tracks:
            path = track_row[0]
            mx_track = mixxx_conn.execute(
                """
                SELECT l.id FROM library l
                JOIN track_locations tl ON l.location = tl.id
                WHERE tl.location = ?
                """,
                (path,),
            ).fetchone()
            if mx_track is None:
                continue
            mixxx_conn.execute(
                "INSERT OR IGNORE INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
                (mx_crate_id, mx_track[0]),
            )
            tracks_pushed += 1

    return {"crates_created": crates_created, "tracks_pushed": tracks_pushed}


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

    def push_track(self, track: dict, mixxx_conn: sqlite3.Connection) -> bool:
        """Push one track's metadata from MultiDJ to Mixxx.

        track: dict with keys: id, path, artist, title, album, genre, bpm,
               rating, play_count, key (Camelot string or None)
        mixxx_conn: already-open writable connection to Mixxx DB
        Returns True on success (at least 1 row updated), False otherwise.
        """
        path = track.get("path")

        # Find the Mixxx library id by path
        cur = mixxx_conn.execute(
            """
            UPDATE library SET
                artist=?, title=?, album=?, genre=?, bpm=?, rating=?, timesplayed=?
            WHERE id=(
                SELECT l.id FROM library l
                JOIN track_locations tl ON l.location = tl.id
                WHERE tl.location = ?
            )
            """,
            (
                track.get("artist"),
                track.get("title"),
                track.get("album"),
                track.get("genre"),
                track.get("bpm"),
                track.get("rating"),
                track.get("play_count"),
                path,
            ),
        )
        updated = cur.rowcount >= 1

        if not updated:
            return False

        # Handle key update — only if track has a key value
        key = track.get("key")
        if key is not None:
            row = mixxx_conn.execute(
                "SELECT id FROM keys WHERE key_text=?", (key,)
            ).fetchone()
            if row is not None:
                key_id = row[0]
                mixxx_conn.execute(
                    """
                    UPDATE library SET key_id=?
                    WHERE id=(
                        SELECT l.id FROM library l
                        JOIN track_locations tl ON l.location = tl.id
                        WHERE tl.location = ?
                    )
                    """,
                    (key_id, path),
                )

        return True

    def full_sync(self, multidj_db_path: Path, apply: bool = False) -> dict:
        """Push all dirty tracks to Mixxx.

        Dry-run mode: lists dirty tracks without writing.
        Apply mode: pushes each dirty track to Mixxx, marks dirty=0 on success.
        """
        from ..db import resolve_db_path
        multidj_db_path = resolve_db_path(str(multidj_db_path) if multidj_db_path else None)

        with connect(str(multidj_db_path), readonly=True) as mdj_conn:
            dirty_rows = mdj_conn.execute(
                """
                SELECT t.id, t.path, t.artist, t.title, t.album, t.genre, t.bpm,
                       t.rating, t.play_count, t.key
                FROM tracks t
                JOIN sync_state ss ON t.id = ss.track_id
                WHERE ss.adapter='mixxx' AND ss.dirty=1 AND t.deleted=0
                """
            ).fetchall()

        dirty_tracks = [dict(r) for r in dirty_rows]

        if not apply:
            sample = dirty_tracks[:5]
            return {
                "mode":         "dry_run",
                "dirty_tracks": len(dirty_tracks),
                "sample":       sample,
            }

        # ── apply mode ────────────────────────────────────────────────────
        # Backup Mixxx DB first
        create_backup(str(self.mixxx_path), backup_dir=None)

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        pushed = 0
        errors: list[dict] = []

        # Open Mixxx DB writable
        mixxx_conn = sqlite3.connect(str(self.mixxx_path))
        try:
            with connect(str(multidj_db_path), readonly=False) as mdj_conn:
                for track in dirty_tracks:
                    path = track.get("path")
                    try:
                        success = self.push_track(track, mixxx_conn)
                        if success:
                            mixxx_conn.commit()
                            # Mark clean in MultiDJ
                            mdj_conn.execute(
                                """
                                UPDATE sync_state SET dirty=0, last_synced_at=?
                                WHERE track_id=? AND adapter='mixxx'
                                """,
                                (now_iso, track["id"]),
                            )
                            mdj_conn.commit()
                            pushed += 1
                        else:
                            errors.append({"path": path, "reason": "path not found in Mixxx"})
                    except Exception as exc:  # noqa: BLE001
                        try:
                            mixxx_conn.rollback()
                        except Exception:
                            pass
                        errors.append({"path": path, "reason": str(exc)})
                crate_result = _push_crates_to_mixxx(mdj_conn, mixxx_conn)
                mixxx_conn.commit()
        finally:
            mixxx_conn.close()

        return {
            "mode":               "apply",
            "total_dirty":        len(dirty_tracks),
            "pushed":             pushed,
            "errors":             errors,
            "crates_created":      crate_result["crates_created"],
            "crate_tracks_pushed": crate_result["tracks_pushed"],
        }
