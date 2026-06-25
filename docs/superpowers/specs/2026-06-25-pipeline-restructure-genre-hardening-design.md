# Pipeline Restructure + Genre Hardening — Design Spec

**Date:** 2026-06-25
**Status:** Approved

---

## Context

MultiDJ's current pipeline is a flat 10-step list that mixes ingestion, analysis, enrichment,
and Mixxx sync without clear phase boundaries. This makes it hard to reason about what state
a track is in, what was pushed to Mixxx, and what's been enriched. Two things are broken:

1. No structured separation between "prepare the data" and "send it to Mixxx" — so it's
   unclear whether BPM/key from Mixxx's own analysis is being respected or overwritten.
2. Genre data is weak: missing or generic genres aren't enriched, and there's no record of
   where a genre value came from — which blocks agent-assisted mixing queries.

This spec restructures the pipeline into four explicit phases and adds a layered genre
hardening step, designed so a downstream agent (via MCP, future) can query genre data with
confidence about its provenance.

---

## Phase Structure

The pipeline becomes four named phases, each independently runnable via `--phase <name>`:

```
PHASE 1 — INGEST
  import          scan music dir → MultiDJ DB
  dedupe          remove duplicates
  fix_mismatches  fix artist/title swaps
  parse           extract artist/title from filenames

PHASE 2 — ANALYZE
  mixxx_import    pull Mixxx's existing BPM/key BEFORE audio analysis
  bpm             audio analysis — skip if track already has a value
  key             audio analysis — skip if track already has a value
  mixxx_blobs     push BeatGrid/KeyMap to Mixxx for NEW tracks only; log every skip/write
  energy          RMS × centroid score
  embed           CLAP embeddings — skip if already embedded
  cues            intro/drop/outro — skip if already detected

PHASE 3 — ENRICH
  clean_text      strip promo markers from artist/title
  enrich_meta     Discogs → MusicBrainz (release_year, label, album)
  enrich_genre    NEW: layered genre hardening (see below)
  clean_genres    normalize genre strings

PHASE 4 — SYNC
  cluster         rebuild Vibe/ crates from embeddings
  crates          rebuild Genre:/BPM:/Key:/Energy:/Lang: crates
  sync            push dirty tracks + crates + cues → Mixxx
  report          regenerate HTML dashboard
```

`multidj pipeline --apply` runs all four phases.
`multidj pipeline --phase analyze --apply` runs only Phase 2.

---

## Schema: Migration 008

```sql
ALTER TABLE tracks ADD COLUMN genre_source TEXT;
ALTER TABLE tracks ADD COLUMN genre_confidence REAL;
```

| Column | Type | Values |
|---|---|---|
| `genre_source` | TEXT | `'file'` \| `'discogs'` \| `'musicbrainz'` \| `'clap'` \| `'manual'` |
| `genre_confidence` | REAL | 0.0–1.0 (CLAP cosine sim; web sources default to 1.0) |

`track_tags` already stores `discogs_styles` and `discogs_primary_style` — no change needed.

### Agent-readable per-track context

```
genre            = "Tech House"
genre_source     = "discogs"             ← how much to trust it
genre_confidence = 1.0
discogs_styles   = "Tech House, Deep House"  ← full style hierarchy
embeddings       = [clap vector]         ← audio-based similarity
vibe_cluster     = "Vibe/Cluster-04"    ← neighborhood in audio space
```

This gives a downstream agent enough to answer: *"find tracks that sound like Tech House but
are in a different Vibe cluster"* — blending metadata trust with audio evidence.

---

## Genre Hardening: `enrich_genre` Step

Decision tree per track — stops at the first hit:

```
1. genre_source = 'manual'
   →  SKIP (protected forever — only human writes to this)

2. FILE: existing genre is specific (not in UNINFORMATIVE_GENRES, not empty)
   →  genre_source='file', confidence=1.0

3. DISCOGS: query by artist+title
   →  hit: genre = discogs_primary_style
           track_tags.discogs_styles = all styles joined (e.g. "Tech House, Deep House")
           source='discogs', confidence=1.0

4. MUSICBRAINZ: query by artist+title
   →  hit: genre = highest-voted tag not in UNINFORMATIVE_GENRES
           (fall back to highest-voted tag if all are uninformative)
           source='musicbrainz', confidence=1.0

5. CLAP: score track's existing embedding against ELECTRONIC_GENRE_LABELS
   (requires embed step to have run; if no embedding present, fall through to 6)
   →  top score ≥ 0.25: genre = top label, source='clap', confidence=<score>
   →  top score < 0.25: no write

6. NO HIT: leave genre unchanged — a missing genre is better than a wrong one
```

### ELECTRONIC_GENRE_LABELS (added to `constants.py`)

~25 electronic genre strings targeted to a DJ library:
Tech House, Deep House, Melodic Techno, Minimal Techno, Acid Techno, Industrial Techno,
Progressive House, Electro House, Bass House, Melodic House & Techno, Organic House,
Techno, House, Trance, Progressive Trance, Drum and Bass, Dubstep, Garage, Afro House,
Nu-Disco, Disco, Funk, Ambient, Downtempo, Hip-Hop

`enrich_genre` is incremental: skips tracks with non-null `genre_source` unless `--force`.

---

## BPM/Key Protection Contract

The protection is a two-directional guarantee:

```
Mixxx analyzed first:
  mixxx_import fills MultiDJ bpm/key from Mixxx
  bpm + key steps skip (already has value)
  mixxx_blobs logs: "SKIPPED — Mixxx already owns BPM for <track>"

MultiDJ analyzed first (new track Mixxx never saw):
  bpm + key write to MultiDJ DB
  mixxx_blobs writes BeatGrid/KeyMap blobs → Mixxx reads them at load time
  mixxx_blobs logs: "WROTE BeatGrid for <track> (128.0 BPM)"

sync step (Phase 4):
  NEVER writes bpm or key columns to Mixxx — only artist/title/genre/rating/etc.
```

Every track lands in one of two clean states after Phase 2:
- **Mixxx analyzed it first** → MultiDJ imported those values, blobs untouched
- **MultiDJ analyzed it first** → MultiDJ wrote blobs, Mixxx reads them at load time

---

## Files to Modify

| File | Change |
|---|---|
| `multidj/pipeline.py` | Restructure steps into 4 phases; add `--phase` arg; add `enrich_genre` step |
| `multidj/constants.py` | Add `ELECTRONIC_GENRE_LABELS` list |
| `multidj/migrations/008_genre_source.sql` | New migration (`genre_source`, `genre_confidence` columns) |
| `multidj/enrich_genre.py` | New module: `enrich_genre(conn, apply, force, limit)` |
| `multidj/cli.py` | Wire `--phase` flag on `pipeline` command |
| `multidj/adapters/mixxx.py` | Add explicit SKIPPED/WROTE logging to `mixxx_blobs` |
| `tests/test_enrich_genre.py` | New test module covering the genre decision tree |

`multidj/enrich.py` is unchanged — `enrich_meta` continues to handle Discogs/MusicBrainz
metadata (artist, title, album, release_year, label). Genre hardening is a separate concern.

---

## Verification

1. `multidj pipeline --phase ingest --apply` — tracks import cleanly, no duplicates
2. `multidj pipeline --phase analyze --apply` — mixxx_blobs output shows SKIPPED for
   pre-analyzed tracks; new tracks show WROTE with BPM value
3. `multidj pipeline --phase enrich --apply` — `genre_source` and `genre_confidence`
   populated; spot-check 10 tracks across file/discogs/clap sources
4. `multidj pipeline --phase sync --apply` — Mixxx crates updated; BPM/key columns in
   Mixxx DB unchanged for pre-analyzed tracks
5. `pytest tests/ -v` — all tests pass including new `test_enrich_genre.py`
