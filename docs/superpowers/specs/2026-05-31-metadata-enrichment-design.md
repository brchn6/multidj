# Phase 8 — Metadata Enrichment Design

**Date:** 2026-05-31  
**Status:** Approved, ready for implementation planning  
**Phase:** 8

---

## Overview

Enrich tracks in the MultiDJ DB with metadata from three sources in order: file tags already embedded in the audio file, Discogs (best genre/style taxonomy for electronic music), and MusicBrainz (release year, album, label, ISRC). The goal is fully automated, zero-configuration enrichment that makes every track in the library gig-ready and queryable by a future MCP agent building playlists.

SoundCloud metadata fetching (likes, shares, SC-specific tags) is noted as future work — same pipeline slot, same config pattern when built.

---

## Command Interface

```
multidj enrich metadata [--apply] [--force] [--limit N] [--write-tags]
```

Sits under the existing `enrich` subcommand alongside `enrich language`.

| Flag | Behaviour |
|------|-----------|
| `--apply` | Required to write. Dry-run by default (reports what would change). |
| `--force` | Re-enrich tracks already enriched. Without this, only tracks missing enrichment data are processed. |
| `--limit N` | Process at most N tracks (for testing or incremental runs). |
| `--write-tags` | Also write enriched fields back to audio file tags (ID3/FLAC) via mutagen. Opt-in, consistent with `analyze key --write-tags`. |

Dry-run output lists per-track changes: which fields would be written, which source they came from, confidence score.

---

## Pipeline Integration

New step 4, inserted after `parse` (which provides artist + title for search) and before `dedupe`:

```
import → fix_mismatches → parse → enrich → dedupe → bpm → key → energy →
cues → embed → cluster → genres → clean_text → crates → sync → report
```

`--skip-enrich` flag on `pipeline`. Step degrades gracefully if:
- The `[enrich]` extra is not installed → logged warning, step skipped
- No Discogs token configured → Layer 2 silently skipped, Layers 1 + 3 still run
- No network access → per-track errors isolated, batch continues

---

## Three-Layer Enrichment Logic

Each track is processed through layers in order. **A later layer never overwrites a field already written by an earlier layer, and no layer overwrites an existing non-null DB value** (unless `--force` is passed).

### Layer 1 — File tag re-read (free, instant)

Read all ID3 / FLAC / AAC tags from the audio file via mutagen. Fields captured:

| Tag field | Maps to DB column |
|-----------|-------------------|
| `TDRC` / `date` | `release_year` |
| `TALB` / `album` | `album` |
| `TPUB` / `organization` | `label` |
| `TCON` / `genre` | `genre` |

Many promo and download files have these tags embedded; the current importer reads only basic fields. Layer 1 captures what's already there for free before hitting any network.

### Layer 2 — Discogs lookup (best genre/style data)

Search Discogs by `"<artist> <title>"`. Score each result candidate with rapidfuzz against both artist and title fields separately. Accept if `min(artist_score, title_score) >= 85`. Pull from the accepted match:

- `styles` → stored in `track_tags` as `key='discogs_style'` (one row per style value)
- `label` → `tracks.label` if empty
- `year` → `tracks.release_year` if empty
- `catno` (catalog number) → `track_tags` as `key='catalog_number'`

**Skipped automatically** if no `[discogs]` token is in config — no error, no warning beyond a one-time "Discogs not configured" note in the run summary.

Rate limit: 25 requests/minute (authenticated). Enforced internally with a token-bucket limiter. No user configuration needed.

### Layer 3 — MusicBrainz lookup (release metadata fallback)

Search MusicBrainz recordings by artist + title using the same fuzzy scoring threshold (85%). Only runs for tracks still missing at least one target field after Layers 1 + 2. Pull from the best match:

- `first-release-date` → `tracks.release_year` if empty
- `releases[0].title` → `tracks.album` if empty
- `releases[0].label-info[0].label.name` → `tracks.label` if empty
- `isrc` → `track_tags` as `key='isrc'`
- `tags` (MusicBrainz folksonomy) → `tracks.genre` if empty (first tag by vote count)

Rate limit: 1 request/second. Enforced internally. No user configuration needed.

---

## Conflict Policy

| Scenario | Behaviour |
|----------|-----------|
| DB field is NULL | Write enriched value. |
| DB field already has a value | Skip (preserve existing data). |
| `--force` passed | Overwrite all fields from enrichment sources. |
| `track_tags` style entries | Always clear and re-insert on re-run (multi-value, idempotent). |
| `source='manual'` cue points | Never touched (enrichment does not affect cue_points). |

---

## Data Storage

### Migration 006

New columns added to `tracks`:

```sql
ALTER TABLE tracks ADD COLUMN release_year INTEGER;
ALTER TABLE tracks ADD COLUMN label TEXT;
```

### track_tags entries written by enrichment

| key | value example | source |
|-----|---------------|--------|
| `discogs_style` | `Deep House` | Discogs (one row per style) |
| `catalog_number` | `WAP 163` | Discogs |
| `isrc` | `GBCEJ0300050` | MusicBrainz |
| `enrichment_source` | `discogs` / `musicbrainz` / `file_tags` | audit trail |
| `enrichment_score` | `0.93` | fuzzy match score normalized to 0–1 (rapidfuzz returns 0–100; divide by 100 before storing) |

### File tags written (when `--write-tags`)

Uses mutagen. Writes to ID3 (MP3), FLAC, and M4A containers:

| DB field | ID3 tag | FLAC tag |
|----------|---------|----------|
| `release_year` | `TDRC` | `DATE` |
| `genre` | `TCON` | `GENRE` |
| `album` | `TALB` | `ALBUM` |
| `label` | `TPUB` | `ORGANIZATION` |

Only fields that were actually changed by enrichment are written to the file. File writes use the same per-track error isolation as DB writes.

---

## Dependencies

Added under a new `[enrich]` optional extra in `pyproject.toml`:

```toml
[project.optional-dependencies]
enrich = [
    "musicbrainzngs>=0.7",
    "discogs_client>=2.3",  # PyPI: verify exact package name (discogs_client vs python3-discogs-client) at implementation time
    "rapidfuzz>=3.0",
]
```

Install: `uv sync --extra enrich`

The pipeline step checks for importability of these packages at runtime and degrades gracefully if not installed — same pattern as `[embeddings]` extra.

---

## Configuration

In `~/.multidj/config.toml`:

```toml
[discogs]
token = "your_personal_access_token"   # free at discogs.com/settings/developers
user_agent = "multidj/1.0"

[musicbrainz]
user_agent = "multidj/1.0 (bar.cohen@weizmann.ac.il)"
```

The MusicBrainz user agent is pre-populated with the project maintainer's email by default in `config.py`. New users inherit it; the API requires a valid contact address to identify the caller.

`get_enrich_config()` added to `config.py` — reads both sections, returns `None` for Discogs if token is missing (Layer 2 skips silently).

---

## New Module: `multidj/enrich.py`

Key functions:

| Function | Purpose |
|----------|---------|
| `read_file_tags(path)` | Returns dict of enrichable fields from audio file tags |
| `search_discogs(artist, title, client)` | Returns best-match metadata dict or None |
| `search_musicbrainz(artist, title)` | Returns best-match metadata dict or None |
| `enrich_track(track, layers, apply, write_tags)` | Orchestrates all three layers for one track; returns changeset |
| `analyze_enrich(db_path, apply, write_tags, force, limit)` | Batch command: iterates tracks, calls enrich_track, reports summary |

Error isolation: exceptions inside `enrich_track` are caught per-track, logged, and the batch continues — identical to `analyze_bpm`, `analyze_key`, etc.

---

## CLI Wiring

- `cli.py`: add `enrich metadata` subcommand under existing `enrich` parser
- `pipeline.py`: insert `enrich` as step 4; add `--skip-enrich` flag; lazy-import `enrich.analyze_enrich`; pass `backup_dir=False` sentinel

---

## Testing

- Unit tests: mock Discogs + MusicBrainz HTTP responses; test fuzzy scoring threshold; test conflict policy (no overwrite of existing values); test `--force` overwrite; test Layer 1 tag extraction for MP3/FLAC
- Integration test: fixture DB track with NULL `release_year`/`label`; run `analyze_enrich` with mocked APIs; assert DB updated + file tags written
- Graceful degradation test: run without `[enrich]` extra importable → no crash, step skipped with warning

---

## Future Work

- **SoundCloud enrichment** — fetch play count, like count, repost count, and SC genre tags by artist+title search. Same three-layer slot, same config pattern (`[soundcloud] token`). Stored in `track_tags` as `sc_play_count`, `sc_like_count`, `sc_genre`. Enables agent to factor social popularity into playlist building.
- **Beatport enrichment** — genre taxonomy is best-in-class for electronic music but has no free public API; revisit if an unofficial or paid route becomes available.
