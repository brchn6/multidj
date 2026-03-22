from __future__ import annotations

from typing import Any

from .db import connect

_ACTIVE = "mixxx_deleted = 0"

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
        rows = conn.execute(f"""
            SELECT id, artist, title
            FROM library
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
