from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .backup import create_backup
from .constants import CAMELOT_SUFFIX_RE, DUPLICATE_SUFFIX_RE, NOISE_PREFIX_RE
from .db import connect

_ACTIVE = "mixxx_deleted = 0"

# Remix/edit/bootleg/flip inside parens or brackets.
_REMIX_RE = re.compile(
    r"[\(\[]\s*(.+?)\s+"
    r"(remix|edit|bootleg|flip|rework|re-?work|mashup|mash-?up|version|mix|dub)\s*[\)\]]",
    re.IGNORECASE,
)

# feat. / ft. / featuring — may appear in artist or title position.
_FEAT_RE = re.compile(
    r"\b(?:feat(?:uring|\.?)?|ft\.?)\s+(.+?)(?=\s*[\(\[–\-]|$)",
    re.IGNORECASE,
)

# Separator used to split filename into parts.
_SEP = re.compile(r"\s+-\s+")


def _strip_extension(name: str) -> str:
    """Remove file extension."""
    return Path(name).stem


def _strip_noise(stem: str) -> tuple[str, bool]:
    """Strip leading noise prefixes. Returns (cleaned, had_noise)."""
    cleaned = NOISE_PREFIX_RE.sub("", stem).strip()
    return cleaned, cleaned != stem


def _strip_camelot(stem: str) -> str:
    """Remove trailing Camelot/analysis tags."""
    return CAMELOT_SUFFIX_RE.sub("", stem).strip()


def _strip_duplicate_suffix(stem: str) -> str:
    """Remove trailing .N duplicate counters (applied before extension strip)."""
    return DUPLICATE_SUFFIX_RE.sub("", stem).strip()


def _extract_remix(text: str) -> tuple[str, str | None]:
    """Extract remixer from title. Returns (title_without_remix_tag, remixer_or_None)."""
    m = _REMIX_RE.search(text)
    if not m:
        return text, None
    remixer = m.group(1).strip()
    # Keep the parens in the title but extract the name separately.
    return text, remixer


def _extract_feat(text: str) -> tuple[str, str | None]:
    """Extract featuring artist from text. Returns (cleaned_text, featuring_or_None)."""
    m = _FEAT_RE.search(text)
    if not m:
        return text, None
    featuring = m.group(1).strip()
    cleaned = _FEAT_RE.sub("", text).strip().rstrip("(").strip()
    return cleaned, featuring


def _confidence(parts: list[str], had_noise: bool, is_underscore: bool) -> str:
    """
    Assign confidence:
      high   — clean 2-part split, no noise
      medium — 3-part split (third part stripped as uploader noise), or had light noise
      low    — 1-part (no separator found), 4+ parts, or underscore format
    """
    if is_underscore:
        return "low"
    if len(parts) <= 1 or len(parts) >= 4:
        return "low"
    if len(parts) == 2 and not had_noise:
        return "high"
    return "medium"


def parse_filename(filename: str) -> dict[str, Any]:
    """
    Parse a single filename into structured metadata.
    Returns a dict with keys:
      stem, artist, title, remixer, featuring, confidence, notes
    """
    notes: list[str] = []

    # 1. Remove extension and duplicate counter (.1, .2 etc.)
    stem = _strip_extension(filename)
    stem = _strip_duplicate_suffix(stem)

    # 2. Handle underscore-delimited names (ABBA_Dancing_Queen_101bpm_...).
    is_underscore = "_" in stem and " - " not in stem
    if is_underscore:
        stem = stem.replace("_", " ")
        notes.append("underscore_format")

    # 3. Strip Camelot/analysis suffix before splitting.
    stem = _strip_camelot(stem)

    # 4. Strip noise prefixes.
    stem, had_noise = _strip_noise(stem)
    if had_noise:
        notes.append("noise_prefix_stripped")

    # 5. Split on " - ".
    parts = [p.strip() for p in _SEP.split(stem) if p.strip()]

    if not parts:
        return {
            "stem": Path(filename).stem,
            "artist": None, "title": None,
            "remixer": None, "featuring": None,
            "confidence": "low", "notes": ["empty_after_cleaning"],
        }

    # 6. Assign artist / title from parts.
    # Part 0 → artist.  Part 1 → title.  Part 2+ → uploader noise (dropped).
    if len(parts) == 1:
        # No separator at all — treat whole thing as title, no artist.
        artist_raw, title_raw = None, parts[0]
    else:
        artist_raw, title_raw = parts[0], parts[1]
        if len(parts) >= 3:
            notes.append(f"dropped_suffix: {' - '.join(parts[2:])}")

    # 7. Extract featuring from artist or title.
    featuring: str | None = None
    if artist_raw:
        artist_raw, feat_a = _extract_feat(artist_raw)
        if feat_a:
            featuring = feat_a
    if title_raw and not featuring:
        title_raw, feat_t = _extract_feat(title_raw)
        if feat_t:
            featuring = feat_t

    # 8. Extract remixer from title.
    remixer: str | None = None
    if title_raw:
        title_raw, remixer = _extract_remix(title_raw)

    confidence = _confidence(parts, had_noise, is_underscore)

    return {
        "stem": Path(filename).stem,
        "artist": artist_raw.strip() if artist_raw else None,
        "title": title_raw.strip() if title_raw else None,
        "remixer": remixer,
        "featuring": featuring,
        "confidence": confidence,
        "notes": notes,
    }


def parse_library(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    min_confidence: str = "medium",
    backup: bool = True,
) -> dict[str, Any]:
    """
    Propose or apply filename-derived metadata to the library table.

    Skips tracks that already have both artist AND title set (unless --force).
    Only writes fields that are currently empty (or all fields if --force).
    """
    mode = "apply" if apply else "dry_run"
    confidence_rank = {"high": 2, "medium": 1, "low": 0}
    min_rank = confidence_rank.get(min_confidence, 1)

    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(f"""
            SELECT l.id, l.artist, l.title, tl.location AS filepath
            FROM library l
            JOIN track_locations tl ON l.location = tl.id
            WHERE l.{_ACTIVE}
        """).fetchall()

    planned: list[dict[str, Any]] = []
    skipped_confident: int = 0
    skipped_low_confidence: int = 0

    for row in rows:
        track_id = row["id"]
        current_artist = row["artist"] or ""
        current_title = row["title"] or ""
        filename = Path(row["filepath"]).name

        # Skip if both fields are already populated and not forcing.
        if current_artist and current_title and not force:
            skipped_confident += 1
            continue

        parsed = parse_filename(filename)

        # Skip below min_confidence.
        if confidence_rank.get(parsed["confidence"], 0) < min_rank:
            skipped_low_confidence += 1
            continue

        # Build the change dict — only fields that are empty (or force).
        change: dict[str, Any] = {"track_id": track_id, "filepath": row["filepath"]}
        has_change = False

        for field in ("artist", "title"):
            new_val = parsed[field]
            current_val = row[field] or ""
            if new_val and (not current_val or force) and new_val != current_val:
                change[f"old_{field}"] = current_val or None
                change[f"new_{field}"] = new_val
                has_change = True

        if not has_change:
            continue

        change["confidence"] = parsed["confidence"]
        change["remixer"] = parsed["remixer"]
        change["featuring"] = parsed["featuring"]
        change["notes"] = parsed["notes"]
        planned.append(change)

    if limit is not None:
        planned = planned[:limit]

    if apply and planned:
        if backup:
            create_backup(db_path)
        artist_updates = [(c["new_artist"], c["track_id"]) for c in planned if "new_artist" in c]
        title_updates  = [(c["new_title"],  c["track_id"]) for c in planned if "new_title"  in c]
        with connect(db_path, readonly=False) as conn:
            if artist_updates:
                conn.executemany("UPDATE library SET artist = ? WHERE id = ?", artist_updates)
            if title_updates:
                conn.executemany("UPDATE library SET title = ? WHERE id = ?", title_updates)
            conn.commit()

    return {
        "mode": mode,
        "total_tracks": len(rows),
        "skipped_already_tagged": skipped_confident,
        "skipped_low_confidence": skipped_low_confidence,
        "total_changes": len(planned),
        "changes": planned,
    }
