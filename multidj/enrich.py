from __future__ import annotations

import sys
import time
from typing import Any

from .db import connect, table_exists, ensure_not_empty

_ACTIVE = "deleted = 0"

# Hebrew Unicode blocks:
#   U+0590–U+05FF  — Hebrew
#   U+FB1D–U+FB4F  — Hebrew Presentation Forms
_HEBREW_RANGES = (
    (0x0590, 0x05FF),
    (0xFB1D, 0xFB4F),
)


def is_hebrew(text: str | None) -> bool:
    """Return True if text contains at least one Hebrew character."""
    if not text:
        return False
    return any(
        lo <= ord(ch) <= hi
        for ch in text
        for lo, hi in _HEBREW_RANGES
    )


def enrich_language(db_path: str | None = None) -> dict[str, Any]:
    """
    Detect Hebrew tracks by scanning title and artist fields.
    Read-only — no DB writes. Use 'crates rebuild' to act on results.
    """
    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)

        rows = conn.execute(f"""
            SELECT id, artist, title
            FROM tracks
            WHERE {_ACTIVE}
        """).fetchall()

    total = len(rows)
    hebrew_tracks: list[dict[str, Any]] = []

    for row in rows:
        if is_hebrew(row["title"]) or is_hebrew(row["artist"]):
            hebrew_tracks.append({
                "track_id": row["id"],
                "artist": row["artist"],
                "title": row["title"],
            })

    hebrew_count = len(hebrew_tracks)
    pct = round(hebrew_count / total * 100, 1) if total else 0.0

    return {
        "total_active_tracks": total,
        "hebrew_tracks": hebrew_count,
        "hebrew_pct": pct,
        "tracks": hebrew_tracks,
    }


def _progress(msg: str, end: str = "\n") -> None:
    print(msg, file=sys.stderr, end=end, flush=True)


def read_file_tags(filepath: str) -> dict[str, Any]:
    """Read ID3/FLAC/AAC tags from an audio file. Returns dict of available fields."""
    try:
        import mutagen
    except ImportError:
        return {}

    try:
        f = mutagen.File(filepath)
    except Exception:
        return {}
    if f is None or f.tags is None:
        return {}

    result: dict[str, Any] = {}
    tags = f.tags

    if hasattr(tags, "getall"):
        # ID3 (MP3) — raw tag access
        tdrc = tags.get("TDRC")
        if tdrc and tdrc.text:
            try:
                result["release_year"] = int(str(tdrc.text[0])[:4])
            except (ValueError, AttributeError, IndexError):
                pass
        talb = tags.get("TALB")
        if talb and talb.text:
            val = str(talb.text[0]).strip()
            if val:
                result["album"] = val
        tpub = tags.get("TPUB")
        if tpub and tpub.text:
            val = str(tpub.text[0]).strip()
            if val:
                result["label"] = val
        tcon = tags.get("TCON")
        if tcon and tcon.text:
            val = str(tcon.text[0]).strip()
            if val:
                result["genre"] = val
    else:
        # FLAC / Vorbis Comments / M4A — list-of-strings interface
        def _first(key: str) -> str | None:
            val = tags.get(key) or tags.get(key.upper())
            if isinstance(val, list) and val:
                return str(val[0]).strip() or None
            if isinstance(val, str):
                return val.strip() or None
            return None

        year_str = _first("date") or _first("year")
        if year_str:
            try:
                result["release_year"] = int(year_str[:4])
            except ValueError:
                pass
        album = _first("album")
        if album:
            result["album"] = album
        label = _first("organization") or _first("label")
        if label:
            result["label"] = label
        genre = _first("genre")
        if genre:
            result["genre"] = genre

    return result
