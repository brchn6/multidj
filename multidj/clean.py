from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .backup import create_backup
from .constants import (
    EMOJI_OR_SYMBOL_RE,
    GENRE_MULTI_VALUE_SPLIT_RE,
    HEBREW_CHAR_RE,
    HEBREW_METADATA_MIN_TOKENS,
    HEBREW_METADATA_TOKENS,
    JUNK_TOKEN_MIN_WORDS,
    PROTECTED_CANONICAL_GENRES,
    SUSPICIOUS_GENRE_JUNK_TOKENS,
    SUSPICIOUS_MULTI_VALUE_MIN_TOKENS,
    UNINFORMATIVE_GENRES,
)
from .db import connect, table_exists, ensure_not_empty

_ACTIVE = "deleted = 0"
_COLLAPSE_SPACES = re.compile(r"  +")
_TRAILING_BRACKET_GROUP_RE = re.compile(r"\s*[\(\[\{]\s*([^\)\]\}]{1,120})\s*[\)\]\}]\s*$")
_EMPTY_BRACKET_GROUP_RE = re.compile(r"\s*[\(\[\{]\s*[\)\]\}]\s*")
_TRAILING_NOISE_PHRASE_RE = re.compile(
    r"\s*(?:[-|:]\s*)?"
    r"(" 
    r"(?:official\s+)?(?:lyric\s+video|lyrics|music\s+video|video|audio)"
    r"|(?:official\s+)?visualizer"
    r"|free\s*(?:download|d[\/_⧸]?l)?"
    r"|d[\/_⧸]?l"
    r"|download"
    r"|out\s*now!?"
    r"|preview"
    r"|teaser"
    r"|available\s+on\s+spotify"
    r"|single\s+version"
    r"|extended"
    r"|(?:\d{4}\s+)?remaster(?:ed)?"
    r"|numa\s+edit"
    r")\s*$",
    re.IGNORECASE,
)
_BPM_MARKER_RE = re.compile(r"^\d{2,3}\s*bpm$", re.IGNORECASE)
_TRAILING_SEPARATOR_RE = re.compile(r"\s*[-|:]\s*$")
_LEADING_SEPARATOR_RE = re.compile(r"^\s*[-|:：/]\s*")
_LEADING_ARTIST_00_RE = re.compile(r"^\s*00[\s_-]+")
_LEADING_ARTIST_NOISE_RE = re.compile(
    r"^\s*[\(\[\{]?\s*"
    r"(?:free\s*(?:download|d[\/_⧸]?l)?|d[\/_⧸]?l|download)"
    r"\s*[\)\]\}]?\s*(?:[:|\-：]+\s*)+",
    re.IGNORECASE,
)
_ARTIST_INLINE_NOISE_TOKEN_RE = re.compile(
    r"\b(?:free|download|d(?:[/_])?l)\b",
    re.IGNORECASE,
)
_SYMBOL_ONLY_RE = re.compile(r"^[\W_]+$", re.UNICODE)

_TITLE_NOISE_EXACT_MARKERS: frozenset[str] = frozenset({
    "official lyric video",
    "official music video",
    "official video",
    "lyric video",
    "lyrics",
    "official audio",
    "audio",
    "official visualizer",
    "visualizer",
    "free",
    "free download",
    "free dl",
    "free d/l",
    "free d_l",
    "dl",
    "d/l",
    "d_l",
    "download",
    "out now",
    "out now!",
    "preview",
    "teaser",
    "available on spotify",
    "click buy to download",
    "single version",
    "extended",
    "remaster",
    "remastered",
    "numa edit",
    "free donalod",
    "free donwload",
})


def _normalize_marker_text(text: str) -> str:
    normalized = text.lower().replace("⧸", "/")
    normalized = _COLLAPSE_SPACES.sub(" ", normalized).strip(" .!*-_|:;")
    return normalized


def _is_title_noise_marker(marker: str) -> bool:
    normalized = _normalize_marker_text(marker)
    if not normalized:
        return False
    if normalized in _TITLE_NOISE_EXACT_MARKERS:
        return True
    if _BPM_MARKER_RE.match(normalized):
        return True
    if re.search(r"\bfree\s*(?:download|d[/_]l)?\b", normalized):
        return True
    if re.search(r"\bd(?:[/_])?l\b", normalized):
        return True
    if re.search(r"\bfree\s*don[aw]l?o?d\b", normalized):
        return True
    if re.search(r"\b(?:official\s+)?(?:lyric\s+video|lyrics|music\s+video|video|audio)\b", normalized):
        return True
    if "visualizer" in normalized:
        return True
    if re.search(r"\bout\s*now\b|\bpreview\b|\bteaser\b", normalized):
        return True
    if "available on spotify" in normalized:
        return True
    if re.search(r"\bsingle\s+version\b", normalized):
        return True
    if normalized == "extended":
        return True
    if re.search(r"\b(?:\d{4}\s+)?remaster(?:ed)?\b", normalized):
        return True
    if re.search(r"\bnuma\s+edit\b", normalized):
        return True
    return False


def _clean_trailing_noise(text: str) -> str:
    cleaned = text.strip()
    while True:
        updated = cleaned

        bracket_match = _TRAILING_BRACKET_GROUP_RE.search(updated)
        if bracket_match and _is_title_noise_marker(bracket_match.group(1)):
            updated = updated[:bracket_match.start()]

        updated = _TRAILING_NOISE_PHRASE_RE.sub("", updated)
        updated = _COLLAPSE_SPACES.sub(" ", updated).strip()
        updated = _TRAILING_SEPARATOR_RE.sub("", updated).strip()
        if _SYMBOL_ONLY_RE.match(updated or ""):
            updated = ""
        if updated == cleaned:
            return updated
        cleaned = updated


def clean_artist_noise(artist: str) -> str:
    cleaned = _LEADING_ARTIST_00_RE.sub("", artist.strip())
    cleaned = _LEADING_ARTIST_NOISE_RE.sub("", cleaned)
    cleaned = _ARTIST_INLINE_NOISE_TOKEN_RE.sub("", cleaned)
    cleaned = _EMPTY_BRACKET_GROUP_RE.sub(" ", cleaned)
    cleaned = _COLLAPSE_SPACES.sub(" ", cleaned).strip()
    cleaned = _LEADING_SEPARATOR_RE.sub("", cleaned).strip()
    return _clean_trailing_noise(cleaned)


def clean_title_noise(title: str) -> str:
    # Remove mapped promotional/download markers only from trailing title suffixes.
    return _clean_trailing_noise(title)


def _append_null_changes(
    planned: list[dict[str, Any]],
    entries: list[tuple[int, str]],
    reason: str,
) -> None:
    for track_id, genre in entries:
        planned.append({
            "track_id": track_id,
            "old_genre": genre,
            "new_genre": None,
            "reason": reason,
        })


def _split_multi_value_tokens(genre_norm: str) -> list[str]:
    if not any(sep in genre_norm for sep in (",", "/", "|")):
        return []
    return [token.strip() for token in GENRE_MULTI_VALUE_SPLIT_RE.split(genre_norm) if token.strip()]


def _contains_junk_token(genre_norm: str) -> bool:
    for token in SUSPICIOUS_GENRE_JUNK_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", genre_norm):
            return True
    return any(token in genre_norm for token in HEBREW_METADATA_TOKENS)


def clean_genres(
    db_path: str | None = None,
    apply: bool = False,
    limit: int | None = None,
    backup: bool = True,
    backup_dir: str | None = None,
) -> dict[str, Any]:
    mode = "apply" if apply else "dry_run"

    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)
        rows = conn.execute(
            f"SELECT id, genre FROM tracks"
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
            _append_null_changes(planned, entries, "uninformative")
            continue

        if EMOJI_OR_SYMBOL_RE.match(norm_key):
            _append_null_changes(planned, entries, "symbol_only")
            continue

        multi_tokens = _split_multi_value_tokens(norm_key)
        token_count = len(multi_tokens)
        has_multi_value = token_count > 1
        all_tokens_protected = has_multi_value and all(
            token in PROTECTED_CANONICAL_GENRES for token in multi_tokens
        )
        contains_hebrew = bool(HEBREW_CHAR_RE.search(norm_key))
        has_hebrew_metadata = any(token in norm_key for token in HEBREW_METADATA_TOKENS)
        has_junk_token = _contains_junk_token(norm_key)

        if (
            contains_hebrew
            and has_multi_value
            and not all_tokens_protected
            and (token_count >= HEBREW_METADATA_MIN_TOKENS or has_hebrew_metadata)
        ):
            _append_null_changes(planned, entries, "hebrew_metadata_junk")
            continue

        if (
            has_multi_value
            and token_count >= SUSPICIOUS_MULTI_VALUE_MIN_TOKENS
            and not all_tokens_protected
        ):
            _append_null_changes(planned, entries, "suspicious_multi_value")
            continue

        if has_junk_token and (
            (has_multi_value and not all_tokens_protected)
            or len(norm_key.split()) >= JUNK_TOKEN_MIN_WORDS
        ):
            _append_null_changes(planned, entries, "junk_token")
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

    backup_path: str | None = None
    if apply and planned:
        if backup:
            backup_result = create_backup(db_path, backup_dir=backup_dir)
            backup_path = backup_result.backup_path
        with connect(db_path, readonly=False) as conn:
            for change in planned:
                conn.execute(
                    "UPDATE tracks SET genre = ? WHERE id = ?",
                    (change["new_genre"], change["track_id"]),
                )
            conn.commit()

    result: dict[str, Any] = {
        "mode": mode,
        "total_changes": len(planned),
        "changes": planned,
    }
    if backup_path is not None:
        result["backup_path"] = backup_path
    return result


def clean_text(
    db_path: str | None = None,
    apply: bool = False,
    limit: int | None = None,
    backup: bool = True,
    backup_dir: str | None = None,
) -> dict[str, Any]:
    mode = "apply" if apply else "dry_run"

    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)
        query = f"""
            SELECT id, artist, title, album FROM tracks
            WHERE {_ACTIVE}
              AND (
                (artist IS NOT NULL AND artist != TRIM(artist))
                OR (title  IS NOT NULL AND title  != TRIM(title))
                OR (album  IS NOT NULL AND album  != TRIM(album))
                OR (artist IS NOT NULL AND TRIM(artist) LIKE '00 %')
                OR (artist IS NOT NULL AND TRIM(artist) LIKE '00-%')
                OR (artist IS NOT NULL AND TRIM(artist) LIKE '00_%')
                OR (artist IS NOT NULL AND (artist LIKE '%(%' OR artist LIKE '%[%' OR artist LIKE '%{{%'))
                OR (artist IS NOT NULL AND (
                    LOWER(artist) LIKE '%free%'
                    OR LOWER(artist) LIKE '%download%'
                    OR LOWER(artist) LIKE '%d/l%'
                    OR LOWER(artist) LIKE '%d_l%'
                    OR LOWER(artist) LIKE '% dl%'
                ))
                OR (artist LIKE '%  %')
                OR (title  LIKE '%  %')
                OR (album  LIKE '%  %')
                OR (title IS NOT NULL AND (title LIKE '%(%' OR title LIKE '%[%' OR title LIKE '%{{%'))
                OR (title IS NOT NULL AND (
                    LOWER(title) LIKE '%official%'
                    OR LOWER(title) LIKE '%lyric%'
                    OR LOWER(title) LIKE '%lyrics%'
                    OR LOWER(title) LIKE '%visualizer%'
                    OR LOWER(title) LIKE '%audio%'
                    OR LOWER(title) LIKE '%video%'
                    OR LOWER(title) LIKE '%download%'
                    OR LOWER(title) LIKE '%free dl%'
                    OR LOWER(title) LIKE '%out now%'
                    OR LOWER(title) LIKE '%preview%'
                    OR LOWER(title) LIKE '%teaser%'
                    OR LOWER(title) LIKE '%bpm%'
                    OR LOWER(title) LIKE '%single version%'
                    OR LOWER(title) LIKE '%extended%'
                    OR LOWER(title) LIKE '%remaster%'
                    OR LOWER(title) LIKE '%numa edit%'
                    OR LOWER(title) LIKE '%donalod%'
                    OR LOWER(title) LIKE '%donwload%'
                ))
        """
        query += """
              )
        """
        rows = conn.execute(query).fetchall()

    planned: list[dict[str, Any]] = []

    for row in rows:
        change: dict[str, Any] = {"track_id": row["id"]}
        has_change = False
        for field in ("artist", "title", "album"):
            val = row[field]
            if val is None:
                continue
            cleaned = _COLLAPSE_SPACES.sub(" ", val.strip())
            if field == "title":
                cleaned = clean_title_noise(cleaned)
            elif field == "artist":
                cleaned = clean_artist_noise(cleaned)
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
            create_backup(db_path, backup_dir=backup_dir)
        with connect(db_path, readonly=False) as conn:
            for change in planned:
                track_id = change["track_id"]
                for field in ("artist", "title", "album"):
                    if f"new_{field}" in change:
                        conn.execute(
                            f"UPDATE tracks SET {field} = ? WHERE id = ?",
                            (change[f"new_{field}"], track_id),
                        )
            conn.commit()

    return {
        "mode": mode,
        "total_changes": len(planned),
        "changes": planned,
    }
