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
        if re.search(rf"\\b{re.escape(token)}\\b", genre_norm):
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
) -> dict[str, Any]:
    mode = "apply" if apply else "dry_run"

    with connect(db_path, readonly=True) as conn:
        if table_exists(conn, "library") and not table_exists(conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(conn)
        rows = conn.execute(f"""
            SELECT id, artist, title, album FROM tracks
            WHERE {_ACTIVE}
              AND (
                (artist IS NOT NULL AND artist != TRIM(artist))
                OR (title  IS NOT NULL AND title  != TRIM(title))
                OR (album  IS NOT NULL AND album  != TRIM(album))
                OR (artist LIKE '%  %')
                OR (title  LIKE '%  %')
                OR (album  LIKE '%  %')
              )
        """).fetchall()

    planned: list[dict[str, Any]] = []

    for row in rows:
        change: dict[str, Any] = {"track_id": row["id"]}
        has_change = False
        for field in ("artist", "title", "album"):
            val = row[field]
            if val is None:
                continue
            cleaned = _COLLAPSE_SPACES.sub(" ", val.strip())
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
            create_backup(db_path)
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
