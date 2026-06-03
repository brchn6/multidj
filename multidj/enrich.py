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


_SCORE_THRESHOLD = 0.85


def _fuzzy_score(a: str, b: str) -> float:
    """Return normalized 0–1 token-set similarity between two strings."""
    try:
        from rapidfuzz import fuzz
    except ImportError:
        import difflib
        return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return fuzz.token_set_ratio(a, b) / 100.0


def _match_score(candidate_artist: str, candidate_title: str,
                 query_artist: str, query_title: str) -> float:
    """Combined score: minimum of artist and title similarity."""
    return min(
        _fuzzy_score(candidate_artist, query_artist),
        _fuzzy_score(candidate_title, query_title),
    )


def search_discogs(
    artist: str,
    title: str,
    client: Any,
    *,
    threshold: float = _SCORE_THRESHOLD,
) -> dict[str, Any] | None:
    """Search Discogs for artist+title. Returns metadata dict or None if no confident match."""
    time.sleep(2.5)  # 25 req/min rate limit
    try:
        results = client.search(f"{artist} {title}", type="release")
        if len(results) == 0:
            return None
        release = results[0]
        candidate_artist = release.artists[0].name if release.artists else ""
        candidate_title = release.title or ""
        score = _match_score(candidate_artist, candidate_title, artist, title)
        if score < threshold:
            return None
        label_name = release.labels[0].name if release.labels else None
        return {
            "styles": release.styles or [],
            "release_year": release.year or None,
            "label": label_name,
            "catalog_number": (release.data or {}).get("catno") or None,
            "score": score,
            "source": "discogs",
        }
    except Exception:
        return None


def search_musicbrainz(
    artist: str,
    title: str,
    user_agent: str,
    *,
    threshold: float = _SCORE_THRESHOLD,
) -> dict[str, Any] | None:
    """Search MusicBrainz for artist+title. Returns metadata dict or None."""
    try:
        import musicbrainzngs
    except ImportError:
        return None

    musicbrainzngs.set_useragent(*user_agent.split("/", 1)[0:1], "1.0", user_agent)
    time.sleep(1.0)  # 1 req/sec rate limit
    try:
        result = musicbrainzngs.search_recordings(
            artist=artist, recording=title, limit=5
        )
        recordings = result.get("recording-list", [])
        if not recordings:
            return None

        rec = recordings[0]
        credits = rec.get("artist-credit", [])
        candidate_artist = credits[0]["artist"]["name"] if credits else ""
        candidate_title = rec.get("title", "")
        score = _match_score(candidate_artist, candidate_title, artist, title)
        if score < threshold:
            return None

        releases = rec.get("release-list", [])
        release_year: int | None = None
        album: str | None = None
        label: str | None = None
        if releases:
            rel = releases[0]
            date_str = rel.get("date", "")
            if date_str:
                try:
                    release_year = int(date_str[:4])
                except ValueError:
                    pass
            album = rel.get("title") or None
            label_info = rel.get("label-info-list", [])
            if label_info:
                label = label_info[0].get("label", {}).get("name") or None

        tags = rec.get("tag-list", [])
        genre: str | None = tags[0]["name"] if tags else None

        out: dict[str, Any] = {"score": score, "source": "musicbrainz"}
        if release_year:
            out["release_year"] = release_year
        if album:
            out["album"] = album
        if label:
            out["label"] = label
        if genre:
            out["genre"] = genre
        return out
    except Exception:
        return None


_TAG_WRITE_MAP = {
    # field_name: (id3_tag, flac_key, m4a_key)
    "release_year": ("TDRC", "date", "\xa9day"),
    "album":        ("TALB", "album", "\xa9alb"),
    "label":        ("TPUB", "organization", "aART"),
    "genre":        ("TCON", "genre", "\xa9gen"),
}


def _write_file_tags(filepath: str, fields: dict[str, Any]) -> None:
    """Write enriched fields back to audio file tags. Skips if mutagen unavailable or file missing."""
    if not fields:
        return
    try:
        import mutagen
        import mutagen.id3 as id3
    except ImportError:
        return

    try:
        f = mutagen.File(filepath)
    except Exception:
        return
    if f is None:
        return

    tags = f.tags
    is_id3 = tags is not None and hasattr(tags, "getall")

    for field, value in fields.items():
        if value is None or field not in _TAG_WRITE_MAP:
            continue
        id3_tag, flac_key, _m4a_key = _TAG_WRITE_MAP[field]
        str_val = str(value)

        if is_id3:
            frame_cls = getattr(id3, id3_tag, None)
            if frame_cls:
                f.tags.delall(id3_tag)
                f.tags.add(frame_cls(encoding=3, text=str_val))
        else:
            # FLAC / Vorbis / M4A — key=value list interface
            try:
                f[flac_key] = str_val
            except Exception:
                pass

    f.save()


def enrich_track(
    track: Any,
    *,
    discogs_client: Any | None,
    mb_user_agent: str,
    write_tags: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Orchestrate three-layer enrichment for one track. Returns changeset dict.

    `write_tags=True` calls _write_file_tags() on the audio file after computing
    the changeset. DB writes are the caller's responsibility.
    """
    track_id = track["id"]
    artist = track["artist"] or ""
    title = track["title"] or ""
    filepath = track["path"]

    # Fields present in the DB row that we may fill in
    db_fields = {
        "release_year": track["release_year"],
        "label": track["label"],
        "album": track["album"],
        "genre": track["genre"],
    }

    changes: dict[str, Any] = {}  # field -> new_value
    styles: list[str] = []
    source: str | None = None
    score: float | None = None

    def _accept(field: str, value: Any) -> bool:
        """Accept a value if the field is empty (or force is set)."""
        if value is None:
            return False
        if not force and db_fields.get(field) is not None:
            return False
        return True

    # Layer 1: file tags
    file_data = read_file_tags(filepath)
    for field in ("release_year", "label", "album", "genre"):
        if _accept(field, file_data.get(field)):
            changes[field] = file_data[field]
            if source is None:
                source = "file_tags"

    # Layer 2: Discogs
    if discogs_client is not None and artist and title:
        discogs_data = search_discogs(artist, title, discogs_client)
        if discogs_data:
            for field in ("release_year", "label"):
                if field not in changes and _accept(field, discogs_data.get(field)):
                    changes[field] = discogs_data[field]
            first_style = discogs_data.get("styles", [None])[0] if discogs_data.get("styles") else None
            if "genre" not in changes and _accept("genre", first_style):
                changes["genre"] = first_style
            styles = discogs_data.get("styles", [])
            source = "discogs"
            score = discogs_data.get("score")

    # Layer 3: MusicBrainz (only if still missing fields)
    needs_mb = any(
        (force or db_fields.get(f) is None) and f not in changes
        for f in ("release_year", "label", "album", "genre")
    )
    if needs_mb and artist and title:
        mb_data = search_musicbrainz(artist, title, mb_user_agent)
        if mb_data:
            for field in ("release_year", "label", "album", "genre"):
                if _accept(field, mb_data.get(field)) and field not in changes:
                    changes[field] = mb_data[field]
            if source is None:
                source = "musicbrainz"
                score = mb_data.get("score")

    if write_tags and changes:
        try:
            _write_file_tags(filepath, changes)
        except Exception:
            pass  # file write errors are non-fatal

    return {
        "track_id": track_id,
        "artist": artist,
        "title": title,
        "changes": changes,
        "styles": styles,
        "source": source,
        "score": score,
        "error": None,
    }


def enrich_metadata(
    db_path: str | None = None,
    *,
    apply: bool = False,
    write_tags: bool = False,
    force: bool = False,
    limit: int | None = None,
    enrich_cfg: dict[str, Any] | None = None,
    backup_dir: str | None | bool = None,
) -> dict[str, Any]:
    """Three-layer metadata enrichment for all active tracks.

    Layers: file tags → Discogs → MusicBrainz. Only fills empty fields
    (unless force=True). Writes to DB on apply; optionally writes file tags.
    Pass backup_dir=False to suppress backup (used by pipeline which already
    takes one backup at the start).
    """
    from .backup import create_backup

    if enrich_cfg is None:
        from .config import get_enrich_config
        enrich_cfg = get_enrich_config()

    with connect(db_path, readonly=False) as _guard:
        ensure_not_empty(_guard)

    where = "1=1" if force else (
        "release_year IS NULL OR label IS NULL OR album IS NULL OR genre IS NULL"
    )
    sql = f"""
        SELECT id, artist, title, path, genre, album, release_year, label
        FROM tracks
        WHERE ({where}) AND deleted = 0
        ORDER BY artist, title
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    count_sql = f"SELECT COUNT(*) FROM tracks WHERE ({where}) AND deleted = 0"

    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(sql).fetchall()
        total_candidates = conn.execute(count_sql).fetchone()[0]

    mode = "apply" if apply else "dry_run"

    if not apply:
        _progress(f"Dry-run: {total_candidates:,} tracks would be enriched")
        return {
            "mode": mode,
            "total_candidates": total_candidates,
            "processed": min(len(rows), limit or len(rows)),
            "applied": 0,
            "errors": 0,
            "error_details": [],
            "changesets": [],
        }

    if backup_dir is not False:
        create_backup(db_path, backup_dir=backup_dir)

    # Build Discogs client if configured
    discogs_client_obj: Any = None
    discogs_cfg = enrich_cfg.get("discogs")
    if discogs_cfg:
        try:
            import discogs_client as _dc
            discogs_client_obj = _dc.Client(
                discogs_cfg.get("user_agent", "multidj/1.0"),
                user_token=discogs_cfg["token"],
            )
        except ImportError:
            _progress("[enrich] discogs_client not installed; skipping Discogs layer")

    mb_user_agent = enrich_cfg.get("musicbrainz", {}).get(
        "user_agent", "multidj/1.0 (bar.cohen@weizmann.ac.il)"
    )

    changesets: list[dict[str, Any]] = []
    error_details: list[dict] = []
    applied_count = 0
    total = len(rows)

    _progress(f"Enriching {total:,} tracks...")

    for i, row in enumerate(rows, 1):
        label = f"{row['artist'] or ''} - {row['title'] or ''}".strip(" -") or row["path"]
        _progress(f"[{i:{len(str(total))}}/{total}] {label[:60]}", end="")
        try:
            cs = enrich_track(
                row,
                discogs_client=discogs_client_obj,
                mb_user_agent=mb_user_agent,
                write_tags=write_tags,
                force=force,
            )
            changesets.append(cs)

            if not cs["changes"] and not cs["styles"]:
                _progress("  —")
                continue

            with connect(db_path, readonly=False) as conn:
                if cs["changes"]:
                    set_parts = ", ".join(f"{k} = ?" for k in cs["changes"])
                    vals = list(cs["changes"].values()) + [row["id"]]
                    conn.execute(
                        f"UPDATE tracks SET {set_parts} WHERE id = ?", vals
                    )

                # Write track_tags (styles, enrichment audit)
                tag_rows: list[tuple] = []
                if cs["styles"]:
                    tag_rows.append((row["id"], "discogs_styles",
                                     ", ".join(cs["styles"])))
                    tag_rows.append((row["id"], "discogs_primary_style",
                                     cs["styles"][0]))
                if cs["source"]:
                    tag_rows.append((row["id"], "enrichment_source", cs["source"]))
                if cs["score"] is not None:
                    tag_rows.append((row["id"], "enrichment_score",
                                     f"{cs['score']:.3f}"))

                conn.executemany(
                    "INSERT OR REPLACE INTO track_tags (track_id, key, value) VALUES (?,?,?)",
                    tag_rows,
                )
                conn.commit()

            applied_count += 1
            _progress("  ✓")

        except Exception as exc:
            cs_err = {
                "track_id": row["id"],
                "artist": row["artist"],
                "title": row["title"],
                "error": str(exc),
            }
            error_details.append(cs_err)
            _progress(f"  ✗ {exc}")

    return {
        "mode": mode,
        "total_candidates": total_candidates,
        "processed": total,
        "applied": applied_count,
        "errors": len(error_details),
        "error_details": error_details,
        "changesets": changesets,
    }
