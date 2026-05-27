from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..adapters.base import SyncAdapter
from ..audit import detect_title_artist_swap_mismatch
from ..backup import create_backup
from ..constants import KNOWN_ADAPTERS
from ..db import connect

try:
    from mutagen import File as MutagenFile  # type: ignore
except ImportError:
    MutagenFile = None  # type: ignore

SUPPORTED_EXTENSIONS = frozenset(
    {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".mp4", ".ogg", ".opus"}
)


def _read_tags(filepath: str) -> dict[str, Any]:
    """Read embedded tags from an audio file using mutagen Easy tags."""
    if MutagenFile is None:
        raise RuntimeError(
            "Missing optional dependency 'analysis'. Install with:\n\n    uv sync --extra analysis\n"
        )

    audio = MutagenFile(filepath, easy=True)
    if audio is None:
        return {}

    def _first(key: str) -> str | None:
        vals = audio.get(key)
        return str(vals[0]).strip() if vals else None

    bpm_raw = _first("bpm")
    bpm: float | None = None
    if bpm_raw:
        try:
            bpm = float(bpm_raw)
        except (ValueError, TypeError):
            bpm = None

    return {
        "artist":   _first("artist"),
        "title":    _first("title"),
        "album":    _first("album"),
        "genre":    _first("genre"),
        "bpm":      bpm,
        "duration": getattr(audio.info, "length", None),
        "filesize": os.path.getsize(filepath),
    }


def _walk_audio_files(paths: list[str]) -> list[str]:
    """Recursively collect all supported audio file paths."""
    found: list[str] = []
    for root_path in paths:
        for dirpath, _dirs, files in os.walk(root_path):
            for fname in sorted(files):
                if Path(fname).suffix.lower() in SUPPORTED_EXTENSIONS:
                    found.append(os.path.join(dirpath, fname))
    return found


class DirectoryAdapter(SyncAdapter):
    """Import tracks from raw filesystem directories into the MultiDJ DB."""

    def import_all(
        self,
        multidj_db_path: Path,
        apply: bool = False,
        paths: list[str] | None = None,
        backup_dir: str | None = None,
    ) -> dict[str, Any]:
        paths = paths or []
        audio_files = _walk_audio_files(paths)

        if not apply:
            return {
                "mode": "dry_run",
                "total_found": len(audio_files),
                "sample": audio_files[:5],
            }

        if backup_dir is not False and Path(str(multidj_db_path)).exists():
            create_backup(str(multidj_db_path), backup_dir=backup_dir)

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        new_tracks = 0
        updated_tracks = 0
        unchanged_tracks = 0
        auto_swapped_artist_title = 0
        errors: list[dict] = []

        with connect(str(multidj_db_path), readonly=False) as conn:
            for filepath in audio_files:
                try:
                    tags = _read_tags(filepath)
                    mismatch = detect_title_artist_swap_mismatch(
                        filepath,
                        tags.get("artist"),
                        tags.get("title"),
                    )
                    if mismatch is not None:
                        tags["artist"] = mismatch["suggested_artist"]
                        tags["title"] = mismatch["suggested_title"]
                        auto_swapped_artist_title += 1

                    existing = conn.execute(
                        "SELECT id, artist, title, genre, bpm FROM tracks WHERE path = ?",
                        (filepath,),
                    ).fetchone()

                    if existing is None:
                        cur = conn.execute(
                            """
                            INSERT INTO tracks
                                (path, artist, title, album, genre, bpm, duration, filesize, deleted)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                            """,
                            (
                                filepath,
                                tags.get("artist"),
                                tags.get("title"),
                                tags.get("album"),
                                tags.get("genre"),
                                tags.get("bpm"),
                                tags.get("duration"),
                                tags.get("filesize"),
                            ),
                        )
                        track_id = cur.lastrowid
                        new_tracks += 1
                    else:
                        changed = (
                            existing["artist"] != tags.get("artist")
                            or existing["title"] != tags.get("title")
                            or existing["genre"] != tags.get("genre")
                            or existing["bpm"] != tags.get("bpm")
                        )
                        if changed:
                            conn.execute(
                                """
                                UPDATE tracks SET
                                    artist=?, title=?, album=?, genre=?, bpm=?,
                                    duration=?, filesize=?, updated_at=?
                                WHERE path=?
                                """,
                                (
                                    tags.get("artist"),
                                    tags.get("title"),
                                    tags.get("album"),
                                    tags.get("genre"),
                                    tags.get("bpm"),
                                    tags.get("duration"),
                                    tags.get("filesize"),
                                    now_iso,
                                    filepath,
                                ),
                            )
                            track_id = existing["id"]
                            updated_tracks += 1
                        else:
                            track_id = existing["id"]
                            unchanged_tracks += 1

                    # Ensure sync_state rows exist for all known adapters
                    for adapter_name in KNOWN_ADAPTERS:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO sync_state (track_id, adapter, dirty, last_synced_at)
                            VALUES (?, ?, 1, ?)
                            """,
                            (track_id, adapter_name, now_iso),
                        )

                except Exception as exc:  # noqa: BLE001
                    errors.append({"path": filepath, "error": str(exc)})

            # Soft-delete tracks whose files no longer exist on disk
            removed_tracks = 0
            scanned_set = set(audio_files)
            existing_active = conn.execute(
                "SELECT id, path FROM tracks WHERE deleted = 0 AND path IS NOT NULL"
            ).fetchall()
            for erow in existing_active:
                epath = erow["path"]
                if epath not in scanned_set and not os.path.exists(epath):
                    conn.execute(
                        "UPDATE tracks SET deleted = 1 WHERE id = ?", (erow["id"],)
                    )
                    removed_tracks += 1

            # Single commit for all successful writes
            conn.commit()

        return {
            "mode":             "apply",
            "total_found":      len(audio_files),
            "new_tracks":       new_tracks,
            "updated_tracks":   updated_tracks,
            "unchanged_tracks": unchanged_tracks,
            "auto_swapped_artist_title": auto_swapped_artist_title,
            "removed_tracks":   removed_tracks,
            "errors":           errors,
        }

    def push_track(self, track: dict, conn: Any) -> bool:  # type: ignore[override]
        raise NotImplementedError("DirectoryAdapter is import-only")

    def full_sync(self, multidj_db_path: Path, apply: bool = False) -> dict:
        raise NotImplementedError("DirectoryAdapter is import-only")
