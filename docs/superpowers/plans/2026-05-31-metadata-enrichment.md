# Phase 8 — Metadata Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `multidj enrich metadata` — a three-layer command that reads file tags, queries Discogs and MusicBrainz, and fills in release_year, label, genre, and style tags for every track with zero user friction.

**Architecture:** File tags are read first (free, instant), then Discogs is queried for genre/style (best for electronic music), then MusicBrainz for release metadata fallback. Each layer only fills fields the previous layers left empty. Results are written to the MultiDJ DB and optionally back to audio file tags. A new pipeline step 4 (after `parse`, before `dedupe`) runs it automatically.

**Tech Stack:** `musicbrainzngs`, `python3-discogs-client` (imports as `discogs_client`), `rapidfuzz`, `mutagen` (already a core dep). New `[enrich]` optional extra. Migration 006 adds `release_year` and `label` columns to `tracks`. Multi-value styles stored comma-separated in `track_tags` (`key='discogs_styles'`).

**Spec:** `docs/superpowers/specs/2026-05-31-metadata-enrichment-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `multidj/migrations/006_enrichment.sql` | Add `release_year`, `label` columns to `tracks` |
| Modify | `multidj/config.py` | Add `get_enrich_config()` |
| Modify | `pyproject.toml` | Add `[enrich]` optional extra |
| Modify | `multidj/enrich.py` | Add all enrichment logic (helpers + batch command) |
| Modify | `multidj/cli.py` | Add `enrich metadata` subcommand, `--skip-enrich` pipeline flag |
| Modify | `multidj/pipeline.py` | Insert step 4, renumber steps 4→16 |
| Create | `tests/test_enrich_metadata.py` | All tests for Phase 8 |

---

## Task 1: Migration 006 — release_year and label columns

**Files:**
- Create: `multidj/migrations/006_enrichment.sql`
- Test: `tests/test_enrich_metadata.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_enrich_metadata.py
import sqlite3
import pytest


def test_migration_006_adds_release_year_and_label(multidj_db):
    """Migration 006 must add release_year and label to tracks."""
    conn = sqlite3.connect(str(multidj_db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT release_year, label FROM tracks LIMIT 1").fetchone()
        assert row is not None
        assert row["release_year"] is None  # new column starts NULL
        assert row["label"] is None
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py::test_migration_006_adds_release_year_and_label -v
```

Expected: FAIL with `OperationalError: no such column: release_year`

- [ ] **Step 3: Create the migration file**

```sql
-- 006_enrichment.sql — Phase 8: metadata enrichment columns
ALTER TABLE tracks ADD COLUMN release_year INTEGER;
ALTER TABLE tracks ADD COLUMN label TEXT;
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py::test_migration_006_adds_release_year_and_label -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add multidj/migrations/006_enrichment.sql tests/test_enrich_metadata.py
git commit -m "feat: migration 006 — add release_year and label columns to tracks"
```

---

## Task 2: get_enrich_config() in config.py

**Files:**
- Modify: `multidj/config.py`
- Test: `tests/test_enrich_metadata.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_enrich_metadata.py
from multidj.config import get_enrich_config


def test_get_enrich_config_returns_none_without_discogs(tmp_path):
    """Returns None for discogs when [discogs] token is absent."""
    cfg = {}
    result = get_enrich_config(cfg)
    assert result["discogs"] is None
    assert result["musicbrainz"]["user_agent"] == "multidj/1.0 (bar.cohen@weizmann.ac.il)"


def test_get_enrich_config_returns_discogs_when_token_set(tmp_path):
    """Returns discogs dict when token is configured."""
    cfg = {
        "discogs": {
            "token": "mytoken",
            "user_agent": "multidj/1.0",
        }
    }
    result = get_enrich_config(cfg)
    assert result["discogs"] is not None
    assert result["discogs"]["token"] == "mytoken"
    assert result["discogs"]["user_agent"] == "multidj/1.0"


def test_get_enrich_config_musicbrainz_custom_agent(tmp_path):
    """Custom MusicBrainz user_agent is respected."""
    cfg = {"musicbrainz": {"user_agent": "myapp/2.0 (custom@example.com)"}}
    result = get_enrich_config(cfg)
    assert result["musicbrainz"]["user_agent"] == "myapp/2.0 (custom@example.com)"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "enrich_config" -v
```

Expected: FAIL with `ImportError: cannot import name 'get_enrich_config'`

- [ ] **Step 3: Implement get_enrich_config() in config.py**

Add this function at the end of `multidj/config.py`:

```python
def get_enrich_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return enrichment config with discogs (or None) and musicbrainz sections."""
    if cfg is None:
        cfg = load_config()
    discogs = cfg.get("discogs", {})
    mb = cfg.get("musicbrainz", {})
    return {
        "discogs": {
            "token": discogs["token"],
            "user_agent": discogs.get("user_agent", "multidj/1.0"),
        } if discogs.get("token") else None,
        "musicbrainz": {
            "user_agent": mb.get("user_agent", "multidj/1.0 (bar.cohen@weizmann.ac.il)"),
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "enrich_config" -v
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add multidj/config.py tests/test_enrich_metadata.py
git commit -m "feat: add get_enrich_config() to config.py"
```

---

## Task 3: [enrich] extra in pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the optional extra**

In `pyproject.toml`, add an `enrich` key under `[project.optional-dependencies]`:

```toml
[project.optional-dependencies]
analysis = [
    "librosa>=0.11.0",
    "mutagen>=1.47",
]
embeddings = [
    "torch>=2.0",
    "torchaudio>=2.0",
    "transformers>=4.40",
    "librosa>=0.11.0",
    "umap-learn>=0.5",
    "hdbscan>=0.8",
    "openai>=1.0",
    "allin1>=0.1.0",
]
enrich = [
    "musicbrainzngs>=0.7",
    "python3-discogs-client>=2.3",
    "rapidfuzz>=3.0",
]
```

- [ ] **Step 2: Verify uv resolves the extra**

```bash
uv sync --extra enrich --dry-run 2>&1 | head -20
```

Expected: output lists `musicbrainzngs`, `discogs_client`, `rapidfuzz` as packages to install (or already installed). No error.

- [ ] **Step 3: Install the extra**

```bash
uv sync --extra enrich
```

- [ ] **Step 4: Verify imports work**

```bash
.venv/bin/python -c "import musicbrainzngs; import discogs_client; import rapidfuzz; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "build: add [enrich] extra — musicbrainzngs, python3-discogs-client, rapidfuzz"
```

---

## Task 4: read_file_tags() in enrich.py

Reads embedded audio file tags (ID3/FLAC) to extract enrichable fields without any network call.

**Files:**
- Modify: `multidj/enrich.py`
- Test: `tests/test_enrich_metadata.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_enrich_metadata.py
from unittest.mock import MagicMock, patch


def _make_id3_mock(tdrc=None, talb=None, tpub=None, tcon=None):
    """Build a mock mutagen ID3 object with the given tag values."""
    tags = {}
    if tdrc:
        t = MagicMock()
        t.text = [tdrc]
        tags["TDRC"] = t
    if talb:
        t = MagicMock()
        t.text = [talb]
        tags["TALB"] = t
    if tpub:
        t = MagicMock()
        t.text = [tpub]
        tags["TPUB"] = t
    if tcon:
        t = MagicMock()
        t.text = [tcon]
        tags["TCON"] = t

    mock_file = MagicMock()
    mock_file.tags = tags
    mock_file.tags.get = lambda k, d=None: tags.get(k, d)
    mock_file.tags.__contains__ = lambda self, k: k in tags

    # hasattr(tags, 'getall') is used to detect ID3 vs FLAC
    type(mock_file.tags).getall = MagicMock(return_value=[])
    return mock_file


def test_read_file_tags_extracts_id3_year(tmp_path):
    from multidj.enrich import read_file_tags
    with patch("mutagen.File", return_value=_make_id3_mock(tdrc="2003-05-12")):
        result = read_file_tags("/fake/track.mp3")
    assert result["release_year"] == 2003


def test_read_file_tags_extracts_id3_label(tmp_path):
    from multidj.enrich import read_file_tags
    with patch("mutagen.File", return_value=_make_id3_mock(tpub="Warp Records")):
        result = read_file_tags("/fake/track.mp3")
    assert result["label"] == "Warp Records"


def test_read_file_tags_extracts_id3_album_and_genre(tmp_path):
    from multidj.enrich import read_file_tags
    with patch("mutagen.File", return_value=_make_id3_mock(talb="Mezzanine", tcon="Trip Hop")):
        result = read_file_tags("/fake/track.mp3")
    assert result["album"] == "Mezzanine"
    assert result["genre"] == "Trip Hop"


def test_read_file_tags_returns_empty_on_no_file(tmp_path):
    from multidj.enrich import read_file_tags
    with patch("mutagen.File", return_value=None):
        result = read_file_tags("/fake/missing.mp3")
    assert result == {}


def test_read_file_tags_skips_bad_year(tmp_path):
    from multidj.enrich import read_file_tags
    with patch("mutagen.File", return_value=_make_id3_mock(tdrc="not-a-year")):
        result = read_file_tags("/fake/track.mp3")
    assert "release_year" not in result


def _make_flac_mock(date=None, album=None, organization=None, genre=None):
    """Build a mock mutagen FLAC/Vorbis object."""
    tags = {}
    if date:
        tags["date"] = [date]
    if album:
        tags["album"] = [album]
    if organization:
        tags["organization"] = [organization]
    if genre:
        tags["genre"] = [genre]

    mock_file = MagicMock()
    mock_file.tags = tags
    mock_file.tags.__contains__ = lambda self, k: k in tags
    mock_file.tags.get = lambda k, d=None: tags.get(k, d)
    # No 'getall' attribute — signals FLAC/Vorbis
    del type(mock_file.tags).getall
    return mock_file


def test_read_file_tags_extracts_flac_fields(tmp_path):
    from multidj.enrich import read_file_tags
    with patch("mutagen.File", return_value=_make_flac_mock(
        date="1998", album="Selected Ambient Works", organization="R&S Records", genre="Ambient"
    )):
        result = read_file_tags("/fake/track.flac")
    assert result["release_year"] == 1998
    assert result["album"] == "Selected Ambient Works"
    assert result["label"] == "R&S Records"
    assert result["genre"] == "Ambient"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "read_file_tags" -v
```

Expected: FAIL with `ImportError: cannot import name 'read_file_tags'`

- [ ] **Step 3: Implement read_file_tags() in enrich.py**

Add to `multidj/enrich.py` (keep the existing `enrich_language` function, add below it):

```python
from __future__ import annotations

import sys
import time
from typing import Any

from .db import connect, ensure_not_empty, table_exists


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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "read_file_tags" -v
```

Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add multidj/enrich.py tests/test_enrich_metadata.py
git commit -m "feat: add read_file_tags() — layer 1 file tag extraction"
```

---

## Task 5: _fuzzy_score() and search_discogs()

**Files:**
- Modify: `multidj/enrich.py`
- Test: `tests/test_enrich_metadata.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_enrich_metadata.py


def test_fuzzy_score_exact_match():
    from multidj.enrich import _fuzzy_score
    assert _fuzzy_score("DJ Tiesto", "DJ Tiesto") == 1.0


def test_fuzzy_score_close_match():
    from multidj.enrich import _fuzzy_score
    score = _fuzzy_score("Massive Attack", "Massive Atack")  # typo
    assert score > 0.85


def test_fuzzy_score_poor_match():
    from multidj.enrich import _fuzzy_score
    score = _fuzzy_score("Carl Cox", "Aphex Twin")
    assert score < 0.5


def _make_discogs_release(artist="DJ Tiesto", title="Red Lights",
                          styles=None, year=2004, label="Black Hole"):
    rel = MagicMock()
    rel.artists = [MagicMock()]
    rel.artists[0].name = artist
    rel.title = title
    rel.styles = styles or ["Trance", "Progressive Trance"]
    rel.year = year
    rel.labels = [MagicMock()]
    rel.labels[0].name = label
    rel.data = {"catno": "BHNL 012"}
    return rel


def test_search_discogs_returns_match_above_threshold():
    from multidj.enrich import search_discogs
    mock_release = _make_discogs_release()
    mock_results = MagicMock()
    mock_results.__len__ = lambda self: 1
    mock_results.__getitem__ = lambda self, i: mock_release

    mock_client = MagicMock()
    mock_client.search.return_value = mock_results

    with patch("time.sleep"):
        result = search_discogs("DJ Tiesto", "Red Lights", mock_client)

    assert result is not None
    assert result["styles"] == ["Trance", "Progressive Trance"]
    assert result["release_year"] == 2004
    assert result["label"] == "Black Hole"
    assert result["catalog_number"] == "BHNL 012"
    assert result["score"] > 0.85


def test_search_discogs_returns_none_below_threshold():
    from multidj.enrich import search_discogs
    mock_release = _make_discogs_release(artist="Completely Different Artist", title="Unrelated")
    mock_results = MagicMock()
    mock_results.__len__ = lambda self: 1
    mock_results.__getitem__ = lambda self, i: mock_release

    mock_client = MagicMock()
    mock_client.search.return_value = mock_results

    with patch("time.sleep"):
        result = search_discogs("Carl Cox", "Pressure", mock_client)

    assert result is None


def test_search_discogs_returns_none_on_empty_results():
    from multidj.enrich import search_discogs
    mock_results = MagicMock()
    mock_results.__len__ = lambda self: 0

    mock_client = MagicMock()
    mock_client.search.return_value = mock_results

    with patch("time.sleep"):
        result = search_discogs("Unknown", "Track", mock_client)

    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "fuzzy_score or search_discogs" -v
```

Expected: FAIL with `ImportError: cannot import name '_fuzzy_score'`

- [ ] **Step 3: Implement _fuzzy_score() and search_discogs() in enrich.py**

Add after `read_file_tags()`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "fuzzy_score or search_discogs" -v
```

Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add multidj/enrich.py tests/test_enrich_metadata.py
git commit -m "feat: add _fuzzy_score() and search_discogs() — Discogs layer"
```

---

## Task 6: search_musicbrainz()

**Files:**
- Modify: `multidj/enrich.py`
- Test: `tests/test_enrich_metadata.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_enrich_metadata.py


def _make_mb_result(artist="Massive Attack", title="Teardrop",
                    date="1998-03-09", album="Mezzanine",
                    label="Virgin", genre_tag="Trip Hop"):
    recording = {
        "id": "abc-123",
        "title": title,
        "artist-credit": [{"artist": {"name": artist}}],
        "release-list": [{
            "title": album,
            "date": date,
            "label-info-list": [{"label": {"name": label}}],
        }],
        "tag-list": [{"name": genre_tag, "count": "10"}],
    }
    return {"recording-list": [recording], "recording-count": 1}


def test_search_musicbrainz_returns_match():
    from multidj.enrich import search_musicbrainz
    with patch("musicbrainzngs.search_recordings", return_value=_make_mb_result()), \
         patch("musicbrainzngs.set_useragent"), \
         patch("time.sleep"):
        result = search_musicbrainz("Massive Attack", "Teardrop",
                                    user_agent="multidj/1.0 (test@example.com)")
    assert result is not None
    assert result["release_year"] == 1998
    assert result["album"] == "Mezzanine"
    assert result["label"] == "Virgin"
    assert result["genre"] == "Trip Hop"
    assert result["score"] > 0.85


def test_search_musicbrainz_returns_none_on_empty():
    from multidj.enrich import search_musicbrainz
    with patch("musicbrainzngs.search_recordings", return_value={"recording-list": []}), \
         patch("musicbrainzngs.set_useragent"), \
         patch("time.sleep"):
        result = search_musicbrainz("Nobody", "Unknown",
                                    user_agent="multidj/1.0 (test@example.com)")
    assert result is None


def test_search_musicbrainz_returns_none_below_threshold():
    from multidj.enrich import search_musicbrainz
    mb_result = _make_mb_result(artist="Completely Different", title="Unrelated Song")
    with patch("musicbrainzngs.search_recordings", return_value=mb_result), \
         patch("musicbrainzngs.set_useragent"), \
         patch("time.sleep"):
        result = search_musicbrainz("Carl Cox", "Pressure",
                                    user_agent="multidj/1.0 (test@example.com)")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "search_musicbrainz" -v
```

Expected: FAIL with `ImportError: cannot import name 'search_musicbrainz'`

- [ ] **Step 3: Implement search_musicbrainz() in enrich.py**

Add after `search_discogs()`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "search_musicbrainz" -v
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add multidj/enrich.py tests/test_enrich_metadata.py
git commit -m "feat: add search_musicbrainz() — MusicBrainz layer"
```

---

## Task 7: _write_file_tags()

**Files:**
- Modify: `multidj/enrich.py`
- Test: `tests/test_enrich_metadata.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_enrich_metadata.py


def test_write_file_tags_calls_mutagen_save():
    from multidj.enrich import _write_file_tags

    mock_file = MagicMock()
    mock_file.__setitem__ = MagicMock()

    with patch("mutagen.File", return_value=_make_id3_mock()) as mock_mutagen_file:
        mock_mutagen_file.return_value = mock_file
        _write_file_tags("/fake/track.mp3", {"release_year": 2003, "label": "Warp"})

    mock_file.save.assert_called_once()


def test_write_file_tags_noop_on_none_file():
    from multidj.enrich import _write_file_tags
    with patch("mutagen.File", return_value=None):
        # Should not raise
        _write_file_tags("/fake/missing.mp3", {"release_year": 2003})


def test_write_file_tags_noop_on_empty_fields():
    from multidj.enrich import _write_file_tags
    mock_file = MagicMock()
    with patch("mutagen.File", return_value=mock_file):
        _write_file_tags("/fake/track.mp3", {})
    mock_file.save.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "write_file_tags" -v
```

Expected: FAIL with `ImportError: cannot import name '_write_file_tags'`

- [ ] **Step 3: Implement _write_file_tags() in enrich.py**

Add after `search_musicbrainz()`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "write_file_tags" -v
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add multidj/enrich.py tests/test_enrich_metadata.py
git commit -m "feat: add _write_file_tags() — write enriched fields back to audio tags"
```

---

## Task 8: enrich_track() — single-track orchestrator

**Files:**
- Modify: `multidj/enrich.py`
- Test: `tests/test_enrich_metadata.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_enrich_metadata.py


def _make_track_row(track_id=1, artist="Carl Cox", title="Pressure",
                    genre=None, album=None, release_year=None, label=None,
                    filepath="/music/Carl_Cox_-_Pressure.mp3"):
    row = MagicMock()
    row.__getitem__ = lambda self, k: {
        "id": track_id, "artist": artist, "title": title,
        "genre": genre, "album": album,
        "release_year": release_year, "label": label,
        "path": filepath,
    }[k]
    return row


def test_enrich_track_applies_file_tag_year(multidj_db):
    from multidj.enrich import enrich_track
    track = _make_track_row(release_year=None)

    with patch("multidj.enrich.read_file_tags", return_value={"release_year": 2001}), \
         patch("multidj.enrich.search_discogs", return_value=None), \
         patch("multidj.enrich.search_musicbrainz", return_value=None):
        changeset = enrich_track(track, discogs_client=None, mb_user_agent="ua",
                                 write_tags=False)

    assert changeset["changes"].get("release_year") == 2001
    assert changeset["source"] == "file_tags"


def test_enrich_track_does_not_overwrite_existing_value(multidj_db):
    from multidj.enrich import enrich_track
    track = _make_track_row(release_year=1999)  # already has year

    with patch("multidj.enrich.read_file_tags", return_value={"release_year": 2001}), \
         patch("multidj.enrich.search_discogs", return_value=None), \
         patch("multidj.enrich.search_musicbrainz", return_value=None):
        changeset = enrich_track(track, discogs_client=None, mb_user_agent="ua",
                                 write_tags=False)

    assert "release_year" not in changeset["changes"]


def test_enrich_track_force_overwrites_existing(multidj_db):
    from multidj.enrich import enrich_track
    track = _make_track_row(release_year=1999)

    with patch("multidj.enrich.read_file_tags", return_value={"release_year": 2001}), \
         patch("multidj.enrich.search_discogs", return_value=None), \
         patch("multidj.enrich.search_musicbrainz", return_value=None):
        changeset = enrich_track(track, discogs_client=None, mb_user_agent="ua",
                                 apply=False, write_tags=False, force=True)

    assert changeset["changes"].get("release_year") == 2001


def test_enrich_track_prefers_discogs_styles_over_musicbrainz(multidj_db):
    from multidj.enrich import enrich_track
    track = _make_track_row(genre=None)

    discogs_data = {
        "styles": ["Techno", "Minimal Techno"],
        "release_year": 2003,
        "label": "Tresor",
        "catalog_number": None,
        "score": 0.95,
        "source": "discogs",
    }
    with patch("multidj.enrich.read_file_tags", return_value={}), \
         patch("multidj.enrich.search_discogs", return_value=discogs_data), \
         patch("multidj.enrich.search_musicbrainz", return_value=None):
        changeset = enrich_track(track, discogs_client=MagicMock(), mb_user_agent="ua",
                                 write_tags=False)

    assert changeset["changes"].get("genre") == "Techno"
    assert changeset["styles"] == ["Techno", "Minimal Techno"]
    assert changeset["source"] == "discogs"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "enrich_track" -v
```

Expected: FAIL with `ImportError: cannot import name 'enrich_track'`

- [ ] **Step 3: Implement enrich_track() in enrich.py**

Add after `_write_file_tags()`:

```python
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
                if _accept(field, discogs_data.get(field)):
                    changes[field] = discogs_data[field]
            if _accept("genre", discogs_data.get("styles", [None])[0] if discogs_data.get("styles") else None):
                changes["genre"] = discogs_data["styles"][0]
            styles = discogs_data.get("styles", [])
            source = "discogs"
            score = discogs_data.get("score")

    # Layer 3: MusicBrainz (only if still missing fields)
    missing = {f for f in ("release_year", "label", "album", "genre") if f not in changes and _accept(f, None if force else None)}
    # Re-evaluate: run MB if any target field is still empty
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "enrich_track" -v
```

Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add multidj/enrich.py tests/test_enrich_metadata.py
git commit -m "feat: add enrich_track() — single-track three-layer orchestrator"
```

---

## Task 9: enrich_metadata() — batch command

**Files:**
- Modify: `multidj/enrich.py`
- Test: `tests/test_enrich_metadata.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_enrich_metadata.py
import sqlite3 as _sqlite3


def test_enrich_metadata_dry_run(multidj_db):
    from multidj.enrich import enrich_metadata
    with patch("multidj.enrich.read_file_tags", return_value={"release_year": 2001}), \
         patch("multidj.enrich.search_discogs", return_value=None), \
         patch("multidj.enrich.search_musicbrainz", return_value=None):
        result = enrich_metadata(str(multidj_db), apply=False)

    assert result["mode"] == "dry_run"
    assert result["total_candidates"] >= 1
    assert result["applied"] == 0


def test_enrich_metadata_apply_writes_release_year(multidj_db):
    from multidj.enrich import enrich_metadata
    with patch("multidj.enrich.read_file_tags", return_value={"release_year": 2001}), \
         patch("multidj.enrich.search_discogs", return_value=None), \
         patch("multidj.enrich.search_musicbrainz", return_value=None):
        result = enrich_metadata(str(multidj_db), apply=True)

    assert result["applied"] >= 1
    conn = _sqlite3.connect(str(multidj_db))
    count = conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE release_year = 2001 AND deleted = 0"
    ).fetchone()[0]
    conn.close()
    assert count >= 1


def test_enrich_metadata_apply_writes_discogs_styles(multidj_db):
    from multidj.enrich import enrich_metadata
    discogs_data = {
        "styles": ["Techno", "Minimal Techno"],
        "release_year": 2003,
        "label": "Tresor",
        "catalog_number": "TRS 001",
        "score": 0.95,
        "source": "discogs",
    }
    with patch("multidj.enrich.read_file_tags", return_value={}), \
         patch("multidj.enrich.search_discogs", return_value=discogs_data), \
         patch("multidj.enrich.search_musicbrainz", return_value=None):
        result = enrich_metadata(str(multidj_db), apply=True,
                                 enrich_cfg={"discogs": {"token": "tok", "user_agent": "u"},
                                             "musicbrainz": {"user_agent": "ua"}})

    conn = _sqlite3.connect(str(multidj_db))
    row = conn.execute(
        "SELECT value FROM track_tags WHERE key='discogs_styles' LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert "Techno" in row[0]


def test_enrich_metadata_limit(multidj_db):
    from multidj.enrich import enrich_metadata
    with patch("multidj.enrich.read_file_tags", return_value={"release_year": 2001}), \
         patch("multidj.enrich.search_discogs", return_value=None), \
         patch("multidj.enrich.search_musicbrainz", return_value=None):
        result = enrich_metadata(str(multidj_db), apply=False, limit=2)

    assert result["processed"] == 2


def test_enrich_metadata_error_isolation(multidj_db):
    from multidj.enrich import enrich_metadata

    def _bad_tags(path):
        raise RuntimeError("audio read failure")

    with patch("multidj.enrich.read_file_tags", side_effect=_bad_tags), \
         patch("multidj.enrich.search_discogs", return_value=None), \
         patch("multidj.enrich.search_musicbrainz", return_value=None):
        result = enrich_metadata(str(multidj_db), apply=True)

    assert result["errors"] >= 1
    assert result["applied"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "enrich_metadata" -v
```

Expected: FAIL with `ImportError: cannot import name 'enrich_metadata'`

- [ ] **Step 3: Implement enrich_metadata() in enrich.py**

Add after `enrich_track()`:

```python
def enrich_metadata(
    db_path: str | None = None,
    *,
    apply: bool = False,
    write_tags: bool = False,
    force: bool = False,
    limit: int | None = None,
    enrich_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Three-layer metadata enrichment for all active tracks.

    Layers: file tags → Discogs → MusicBrainz. Only fills empty fields
    (unless force=True). Writes to DB on apply; optionally writes file tags.
    """
    from .backup import create_backup

    if enrich_cfg is None:
        from .config import get_enrich_config
        enrich_cfg = get_enrich_config()

    with connect(db_path, readonly=True) as _guard:
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

                # Write track_tags (styles, catalog_number, enrichment audit)
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "enrich_metadata" -v
```

Expected: 5 PASS

- [ ] **Step 5: Run all enrich tests together**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add multidj/enrich.py tests/test_enrich_metadata.py
git commit -m "feat: add enrich_metadata() — batch three-layer enrichment command"
```

---

## Task 10: CLI wiring — enrich metadata subcommand

**Files:**
- Modify: `multidj/cli.py`
- Test: `tests/test_enrich_metadata.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_enrich_metadata.py
from multidj.cli import main as cli_main


def test_cli_enrich_metadata_dry_run(multidj_db, capsys):
    from multidj.enrich import enrich_metadata as _em
    with patch("multidj.cli.enrich_metadata", wraps=_em) as mock_em, \
         patch("multidj.enrich.read_file_tags", return_value={}), \
         patch("multidj.enrich.search_discogs", return_value=None), \
         patch("multidj.enrich.search_musicbrainz", return_value=None):
        rc = cli_main(["--db", str(multidj_db), "enrich", "metadata"])
    assert rc == 0
    mock_em.assert_called_once()
    call_kwargs = mock_em.call_args
    assert call_kwargs.kwargs.get("apply") is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py::test_cli_enrich_metadata_dry_run -v
```

Expected: FAIL (argument parsing error or `enrich_metadata` not imported in cli)

- [ ] **Step 3: Add enrich metadata to cli.py**

**3a.** Add import at the top of `multidj/cli.py` alongside the other enrich import:

```python
from .enrich import enrich_language, enrich_metadata
```

**3b.** In `build_parser()`, find the `enrich_sub` block and add the `metadata` subparser:

```python
    # ── enrich ────────────────────────────────────────────────────────────────
    enrich_p = sub.add_parser("enrich", help="Enrich track metadata from external signals")
    enrich_sub = enrich_p.add_subparsers(dest="enrich_target", required=True)
    enrich_sub.add_parser("language", help="Detect Hebrew tracks (Unicode range check)")

    p_enrich_meta = enrich_sub.add_parser(
        "metadata",
        help="Fill release_year, label, genre from file tags + Discogs + MusicBrainz",
    )
    p_enrich_meta.add_argument("--apply",       action="store_true",
                               help="Write changes (default: dry-run)")
    p_enrich_meta.add_argument("--force",       action="store_true",
                               help="Re-enrich tracks that already have enrichment data")
    p_enrich_meta.add_argument("--limit",       type=int, default=None,
                               help="Cap number of tracks processed")
    p_enrich_meta.add_argument("--write-tags",  action="store_true", dest="write_tags",
                               help="Also write enriched fields back to audio file tags")
```

**3c.** Add `_format_enrich_metadata()` near the other format functions:

```python
def _format_enrich_metadata(data: dict) -> str:
    mode = data["mode"]
    candidates = data["total_candidates"]
    processed = data["processed"]
    applied = data["applied"]
    errors = data["errors"]
    lines = [
        f"Metadata enrichment — {mode}",
        "",
        f"  Candidates : {candidates:,}",
        f"  Processed  : {processed:,}",
        f"  Applied    : {applied:,}",
        f"  Errors     : {errors:,}",
    ]
    if errors and data.get("error_details"):
        lines.append("")
        lines.append("  Errors:")
        for e in data["error_details"][:5]:
            lines.append(f"    [{e['track_id']}] {e['artist'] or ''} — {e['title'] or ''}: {e['error']}")
        if errors > 5:
            lines.append(f"    ... and {errors - 5} more (use --json for full list)")
    return "\n".join(lines)
```

**3d.** In the command dispatch (the big `elif args.command ==` block), add inside `elif args.command == "enrich":`:

```python
    elif args.command == "enrich":
        if args.enrich_target == "language":
            data = enrich_language(args.db)
            emit(_format_enrich_language(data) if not args.json else data, as_json=args.json)
            return 0
        elif args.enrich_target == "metadata":
            from .config import get_enrich_config, load_config
            cfg = load_config()
            result = enrich_metadata(
                args.db,
                apply=args.apply,
                force=args.force,
                limit=args.limit,
                write_tags=args.write_tags,
                enrich_cfg=get_enrich_config(cfg),
            )
            emit(_format_enrich_metadata(result) if not args.json else result, as_json=args.json)
            return 0
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py::test_cli_enrich_metadata_dry_run -v
```

Expected: PASS

- [ ] **Step 5: Smoke-test the CLI manually**

```bash
multidj --db ~/.multidj/library.sqlite enrich metadata --limit 3
```

Expected: human-readable output showing "Candidates : N", "Processed : 3", "Applied : 0" (dry-run).

- [ ] **Step 6: Commit**

```bash
git add multidj/cli.py tests/test_enrich_metadata.py
git commit -m "feat: wire enrich metadata CLI subcommand + _format_enrich_metadata()"
```

---

## Task 11: Pipeline integration — step 4 + --skip-enrich

**Files:**
- Modify: `multidj/pipeline.py`
- Modify: `multidj/cli.py`
- Test: `tests/test_enrich_metadata.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_enrich_metadata.py
from multidj.pipeline import run_pipeline


def test_pipeline_includes_enrich_step(multidj_db):
    """Pipeline result must include an 'enrich' step."""
    with patch("multidj.enrich.read_file_tags", return_value={}), \
         patch("multidj.enrich.search_discogs", return_value=None), \
         patch("multidj.enrich.search_musicbrainz", return_value=None):
        result = run_pipeline(db_path=str(multidj_db), apply=False)
    step_names = [s["step"] for s in result["steps"]]
    assert "enrich" in step_names


def test_pipeline_skip_enrich(multidj_db):
    """--skip-enrich must skip the enrich step."""
    result = run_pipeline(db_path=str(multidj_db), apply=False, skip={"enrich"})
    enrich_steps = [s for s in result["steps"] if s["step"] == "enrich"]
    assert enrich_steps[0]["status"] == "skipped"


def test_pipeline_enrich_before_dedupe(multidj_db):
    """enrich must appear at step index 3 (after parse, before dedupe)."""
    with patch("multidj.enrich.read_file_tags", return_value={}), \
         patch("multidj.enrich.search_discogs", return_value=None), \
         patch("multidj.enrich.search_musicbrainz", return_value=None):
        result = run_pipeline(db_path=str(multidj_db), apply=False)
    step_names = [s["step"] for s in result["steps"]]
    enrich_idx = step_names.index("enrich")
    parse_idx = step_names.index("parse")
    dedupe_idx = step_names.index("dedupe")
    assert parse_idx < enrich_idx < dedupe_idx
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "pipeline" -v
```

Expected: FAIL (`enrich` not in step_names)

- [ ] **Step 3: Insert enrich as step 4 in pipeline.py**

In `multidj/pipeline.py`, add the import at the top:

```python
from .enrich import enrich_metadata as _enrich_metadata
```

Then find the `# Step 4: Deduplicate tracks` comment and insert the enrich step immediately before it. Also update the subsequent step comments to increment by 1. The new section:

```python
    # Step 4: Enrich metadata from file tags + Discogs + MusicBrainz
    # enrich_metadata degrades gracefully if discogs/musicbrainz extras are missing
    from .config import get_enrich_config as _gec
    steps.append(_run_step(
        "enrich", _enrich_metadata,
        db_path=db_path, apply=apply,
        limit=limit,
        enrich_cfg=_gec(cfg),
    ))

    # Step 5: Deduplicate tracks (before analysis to avoid wasted compute)
    steps.append(_run_step(
        "dedupe", _dedupe,
        db_path=db_path, apply=apply, backup=False,
        limit=limit,
    ))
```

Update the comment numbers for all subsequent steps (Step 5→6 through Step 15→16).

- [ ] **Step 4: Add --skip-enrich to CLI pipeline args**

In `multidj/cli.py`, in `build_parser()`, find the `--skip-parse` line and add after it:

```python
    p_pipeline.add_argument("--skip-enrich",           action="store_true", dest="skip_enrich")
```

Then in the pipeline dispatch block, add:

```python
        if args.skip_enrich:          skip.add("enrich")
```

- [ ] **Step 5: Run pipeline tests**

```bash
.venv/bin/pytest tests/test_enrich_metadata.py -k "pipeline" -v
```

Expected: 3 PASS

- [ ] **Step 6: Run the full test suite to check for regressions**

```bash
.venv/bin/pytest tests/ -v 2>&1 | tail -30
```

Expected: all existing tests still PASS; new enrich tests PASS.

- [ ] **Step 7: Commit**

```bash
git add multidj/pipeline.py multidj/cli.py tests/test_enrich_metadata.py
git commit -m "feat: insert enrich as pipeline step 4; add --skip-enrich flag"
```

---

## Acceptance Checklist

Before declaring Phase 8 complete, verify all of these:

- [ ] `multidj enrich metadata --limit 3` runs without error (dry-run output shown)
- [ ] `multidj enrich metadata --apply --limit 3` writes `release_year`/`label` to at least one track in the DB
- [ ] `multidj pipeline --apply --skip-embed --skip-cluster --skip-cues --limit 5` includes enrich step in output
- [ ] `multidj pipeline --apply --skip-enrich --limit 5` shows enrich as skipped
- [ ] `uv sync --extra enrich && .venv/bin/pytest tests/ -v` — all tests pass
- [ ] `multidj enrich metadata --json --limit 1` emits valid JSON with `mode`, `total_candidates`, `applied` keys
