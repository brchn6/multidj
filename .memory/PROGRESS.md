# MultiDJ — Project Progress Log

Chronological record of every phase, milestone, and significant change.
Dates are hard-coded from git commits and session records.

---

## 2026-03-21 — Project Born: MultiDJ Design Spec

**Context:** `mixxx_multitool` existed as a minimal CLI. Decided to graduate it into a
proper named project with a stable schema, adapter pattern, and agent-friendly output.

**Decisions made:**
- Project name: `multidj` (package), entry points `multidj` and `mixxx-tool` (legacy alias)
- SQLite at `~/.multidj/library.sqlite` — MultiDJ owns the DB, not Mixxx
- Source of truth: MultiDJ DB is always authoritative; Mixxx is a downstream sync target
- Schema versioned via `schema_version` table; migrations auto-applied on startup
- `--apply` required for all mutations; dry-run is default
- `--json` global flag for machine-consumable output
- Auto-backup before every write operation
- Adapter pattern: `SyncAdapter` ABC → `MixxxAdapter` first; Rekordbox/Serato later

**Schema (initial):** `tracks` (id, path, artist, title, album, genre, bpm, key, language,
duration, filesize, rating, play_count, remixer, energy, intro_end, outro_start,
deleted, created_at, updated_at)

**Docs:** `docs/superpowers/specs/2026-03-21-multidj-design.md`

---

## 2026-03-21 — Migration Plan: mixxx_multitool → MultiDJ

**Scope:** Migrate all existing `mixxx_multitool` commands in-place without breaking
existing workflows. Commands preserved: scan, backup, parse, audit genres, audit
metadata, clean genres, clean text, analyze bpm, analyze key, sync mixxx, import mixxx.

**Key:** No flag renames, no behavior renames — all old CLI calls still work.

**Docs:** `docs/superpowers/plans/2026-03-21-multidj-migration.md`

---

## 2026-03-21 — Phase 0–4 Complete: Migration, Schema, Core CLI

**What shipped:**
- `multidj/db.py` — `connect()` context manager, `resolve_db_path()`, migration runner
- `multidj/backup.py` — timestamped `.sqlite` copies, `BackupResult`
- `multidj/utils.py` — `emit(data, json_mode)` unified output
- `multidj/constants.py` — genre list, crate prefixes, regex patterns
- `multidj/cli.py` — argparse entry, global flag hoisting (`--json`, `--db`)
- All migration SQL in `multidj/migrations/`
- Command modules: `scan`, `audit`, `clean`, `analyze`, `parse`, `crates`, `dedupe`
- `adapters/base.py`, `adapters/mixxx.py`, `adapters/directory.py`

---

## 2026-03-22 — Phase 1 Stabilization

Minor fixes to migration runner and import path handling after initial wiring.

---

## 2026-04-01 — v2 Parse + Enrich + Rebuild

**What shipped:**
- `parse` command: artist/title/remixer extraction from filenames with confidence scoring
- `enrich language`: Hebrew detection via Unicode range check (read-only)
- `crates rebuild`: multi-dimension auto-crate builder (Genre:/Lang:/BPM:/Key:)
- Three-tier crate protection model established (see DECISIONS.md)

**Docs:** `docs/superpowers/plans/2026-03-21-v2-parse-enrich-rebuild.md`

---

## 2026-04-06 — DJ Pipeline v2 Design

**Context:** Decided the daily workflow needed a single orchestrating command.

**Design decisions:**
- `multidj pipeline --apply` chains all steps in order
- One backup at the very start; individual steps get `backup_dir=False` sentinel
- Pipeline is idempotent — all analyze steps skip already-processed tracks
- `--skip-<step>` flags for any step
- Config file (not DB) controls which crate dimensions are generated

**Docs:** `docs/superpowers/plans/2026-04-06-multidj-v2-dj-pipeline.md`

---

## 2026-04-22 — Phase 5–7: Pipeline, Energy, Config-Driven Crates

**What shipped:**
- `multidj/pipeline.py` — `run_pipeline()`: initial 8 steps
- `multidj/config.py` — `load_config()`, `save_config()`, `get_music_dir()`; TOML at
  `~/.multidj/config.toml`; preserves unknown sections on save
- `analyze energy` — RMS × spectral centroid, normalized 0–1 library-relative
- `Key:` and `Energy:` crate dimensions added to `crates rebuild`
- `adapters/mixxx.py` — `_push_crates_to_mixxx()`: stale auto-crate deletion +
  membership reconciliation
- **132 tests passing**

**Docs:** `docs/superpowers/specs/2026-04-22-pipeline-design.md`,
`docs/superpowers/plans/2026-04-22-pipeline.md`

---

## 2026-04-22 — Feature Brainstorm Session

Outlined next set of features: embeddings, clustering, cue detection, triage player,
metadata enrichment. Prioritized embeddings → cues → triage → enrich.

**Docs:** `docs/superpowers/plans/2026-04-22-feature-brainstorm.md`

---

## 2026-04-30 — Report Dashboard

Added `multidj report dashboard` — standalone interactive HTML dashboard generated
locally. No server required.

---

## 2026-05-01 — Import Directory Polish

`import directory PATH` command stabilized: recursively finds audio files, deduplicates
by filesize+duration on import, falls back to parent dir name as `album` when no
embedded tag.

---

## 2026-05-03 — Deduplication Enhancement

`dedupe` command: suffix-stripping normalization (removes "(Radio Edit)", "- Single",
etc. before matching), improved grouping logic. Also wired as auto-step in
`import directory`.

---

## 2026-05-05 — Config + Mixxx DB Path

- `[mixxx]` section added to `DEFAULT_CONFIG`
- `get_mixxx_db_path(cfg)` helper in `config.py`
- Pipeline, `sync mixxx`, `import mixxx`, `analyze mixxx-blobs` all fall back to
  `config.toml [mixxx].path` when `--mixxx-db` not passed

---

## 2026-05-27 — Phase 12: Semantic Embeddings + Clustering

**What shipped:**
- `multidj/embed.py` — CLAP audio embedding via `laion/larger_clap_music` (512-dim);
  3-window sampling (start/mid/end × 30s); `embeddings` table; `analyze embed`
- `multidj/cluster.py` — UMAP (512d→10d, cosine) + HDBSCAN → `Vibe/` auto-crates;
  LLM cluster naming via OpenAI-compatible API (falls back to `Vibe/Cluster-NN`)
- `multidj similar TRACK` — KNN cosine-distance search
- `pyproject.toml` — `[embeddings]` extra (torch, transformers, umap-learn, hdbscan,
  openai, allin1)
- `[crates]` config dimension for Vibe/ added

**Key finding:** CLAP inter-track cosine sim on this DJ library ≈ 0.97 (homogeneous
electronic music). Absolute values are compressed but relative ranking is meaningful.

**Docs:** `docs/superpowers/specs/2026-05-27-embeddings-clustering-design.md`,
`docs/superpowers/plans/2026-05-27-embeddings-clustering.md`

---

## 2026-05-28 — Phase 13: Cue Detection

**What shipped:**
- `multidj/cues.py` — `detect_cues(filepath, bpm)`: allin1 primary + librosa
  cross-validation; writes `cue_points` table (type, position, label, confidence,
  source)
- `cues clear` — remove auto-detected cues; never touches `source='manual'`
- `analyze cues --apply [--force] [--limit N]`
- `adapters/mixxx.py` — `_push_cues_to_mixxx()`: intro→slot 0 (blue), drop→slot 1
  (red), outro→slot 2 (green); high-confidence only; slots wiped and repopulated
  each sync

**Schema:** `cue_points` table (migration 005 adds it)

**Docs:** `docs/superpowers/specs/2026-05-28-cue-detection-design.md`,
`docs/superpowers/plans/2026-05-28-cue-detection.md`

---

## 2026-05-28 — Phase 16: Triage Player

**What shipped:**
- `multidj triage` — keyboard-driven track audition via mpv
  - KP0 = soft-delete, Shift+KP0 = hard-delete
  - KP1–5 = rating
  - n = skip, ←/→ = ±30s seek
  - `--crate NAME`, `--limit N`
- Optional dep: `mpv` media player (system package, not pip)

**Status after 2026-05-28:** Phases 12, 13, 16 complete. 271 tests passing.

**Docs:** `docs/superpowers/specs/2026-05-27-triage-player-design.md`,
`docs/superpowers/plans/2026-05-28-triage-player.md`

---

## 2026-05-31 — Phase 8: Metadata Enrichment

**What shipped:**
- `multidj/enrich.py` — three-layer enrichment:
  1. File tags via mutagen (`read_file_tags()`)
  2. Discogs API (`search_discogs()`, fuzzy scoring via rapidfuzz)
  3. MusicBrainz (`search_musicbrainz()`)
  Fills: `release_year`, `label`, `album`, `genre`, `track_tags`
  (`discogs_styles`, `discogs_primary_style`, `catalog_number`)
- `enrich metadata --apply [--force] [--limit N] [--write-tags]`
- `enrich language` (already existed, now grouped under enrich)
- Migration 006: `release_year` (int) + `label` (text) columns on `tracks`
- `get_enrich_config()` in `config.py` — reads `[discogs]` and `[musicbrainz]` sections
- Pipeline expanded to 17 steps: `enrich` → step 4 (after parse, before bpm)
- `pyproject.toml` — `[enrich]` extra (musicbrainzngs, python3-discogs-client, rapidfuzz)

**Docs:** `docs/superpowers/specs/2026-05-31-metadata-enrichment-design.md`,
`docs/superpowers/plans/2026-05-31-metadata-enrichment.md`

---

## 2026-05-31 — Mixxx Pre-Analysis BLOBs (Phase 8b)

**What shipped:**
- `multidj/mixxx_blobs.py` — hand-rolled protobuf encoder (zero dependency on
  `protobuf` package)
- `pack_beatgrid()` → valid `track::io::BeatGrid` (proto2 LITE_RUNTIME)
- `pack_keymap()` → valid `KeyMap-1.0` BLOB
- `analyze mixxx-blobs --apply [--force] [--lock-bpm] [--limit N]`
- Writes BLOBs directly into Mixxx's `analysis` table so tracks open pre-analyzed

**Decision:** No `protobuf` package dependency — hand-rolled varint/length-delimited
encoding verified bit-for-bit against real Mixxx-produced BLOBs.

---

## 2026-06-03 — Deduplication Suffix Normalization

Enhanced `dedupe` with suffix-stripping before matching:
removes "(Radio Edit)", "(Extended Mix)", "- Single", "- Remaster", etc. so
variants of the same track group correctly.

---

## 2026-06-04 — Pipeline Report Link + Config Fallback Fix

- Pipeline prints clickable `file://` link to HTML report at end
- Fixed Mixxx DB path resolution in pipeline: `args.mixxx_db or get_mixxx_db_path(cfg)`

---

## 2026-06-07–08 — Import Directory Polish

- `import directory` defaults to `music_dir` from config when no path given
- Fall back to parent dir name as `album` when no embedded tag
- Auto-dedupe by artist+title after each import

---

## 2026-06-08 — BeatGrid BLOB Fix (Critical)

**Problem:** `pack_beatgrid()` was writing a raw 16-byte `struct.pack("<dd", bpm,
first_beat)` tagged as `BeatGrid-2.0`. Mixxx's protobuf parser rejected every BLOB
with `"Failed to deserialize Beats: Parsing failed"` and re-analyzed from scratch.

**Fix:** Rewrote to produce valid `track::io::BeatGrid` protobuf. Verified bit-for-bit
against real Mixxx BLOBs from three tracks (155 BPM, 142 BPM, 140 BPM).

**Impact:** Headless BPM flow now works end-to-end: pipeline steps import→bpm→mixxx-blobs
produce valid BeatGrids. No Mixxx GUI needed for new tracks.

---

## 2026-06-08 — Import Mixxx Analysis Command

**What shipped:**
- `multidj/import_mixxx_analysis.py`
- `multidj import mixxx-analysis --apply [--force] [--limit N] [--mixxx-db PATH]`
- Reads Mixxx's own BPM/key analysis (`library.bpm`, `library.key`) via path matching
  and imports into MultiDJ tracks table
- Dry-run by default; per-track error isolation

**Use case:** Bootstrap MultiDJ from Mixxx's existing analysis, or get ground-truth
training data.

**Tests added:** `test_mixxx_blobs.py` (8), `test_import_mixxx_analysis.py` (9),
`test_beatgrid.py` (7 rewritten). **314 tests passing.**

---

## 2026-06-08 — Mixxx Crate Refinements

- Stop pushing `bpm` and `key` fields to Mixxx `library` table (Mixxx manages those
  itself via its own analysis engine; writing them caused conflicts)
- `sync mixxx`: after sync, copy DB to local `~/.mixxx/mixxxdb.sqlite` from Dropbox

---

## 2026-06-19 — Phase 12b: CLaMP3 Embedding Backend

**What shipped:**
- `vendor/clamp3` git submodule (sanderwood/clamp3)
- `multidj/embed_clamp3.py` — two-stage pipeline:
  MERT-v1-95M (feature extraction) → CLaMP3 SAAS encoder → 768-dim vector
  Non-overlapping 5s chunks; mean-pool across chunks
- Migration 007: `embeddings` table gets composite PK `(track_id, model_name)` —
  CLAP and CLaMP3 coexist per track
- `embed.py` updated: `model="clap"|"clamp3"`, `load_embeddings_from_db(model_name=)`,
  `find_similar(model=)`
- `cluster.py`: `model=` param, variable embedding dims
- `cli.py`: `--model` flag on `analyze embed`, `cluster vibe`, `similar`
- `pyproject.toml`: `[clamp3]` extra

**Key finding:** CLaMP3 collapses audio-audio discrimination by design (cross-modal
optimization). Mean inter-track cosine sim → 0.96+ on this library.
Decision: use CLAP for clustering/similarity; CLaMP3 only for future text→audio
agent vibe search.

**Install:**
```
uv sync --extra clamp3
git submodule update --init vendor/clamp3
```

---

## 2026-06-19 — DJ Next-Track Suggestion

**What shipped:**
- `multidj/suggest.py` — `suggest_next()`:
  - Score = 0.70 × cosine_sim + 0.15 × bpm_compat + 0.15 × camelot_key_compat
  - BPM compat: linear decay to 0 at `bpm_window` BPM (default ±15); missing → 0.5
  - Key compat: 1.0 same, 0.75 adjacent Camelot or relative major/minor, 0.0 else;
    missing → 0.5
  - Filters to same `Vibe/` cluster by default; `--any-cluster` overrides
  - `_parse_camelot()` handles Camelot notation (9B/1A) + musical notation (Gmin/Cmaj)
- `cli.py`: `multidj suggest TRACK [--top N] [--bpm-window F] [--any-cluster] [--model]`
- 19 unit + integration tests in `tests/test_suggest.py`

---

## 2026-06-19 — Library Visualization (scripts/viz_library.py)

**What shipped:**
- Interactive UMAP HTML scatter plot, fully self-contained (no server)
- UMAP 512d→10d (cosine) → HDBSCAN euclidean: mirrors production `cluster vibe` pipeline
- Click any point → sidebar shows top-5 precomputed neighbors with BPM/key/cluster
- Color toggle: Genre / Cluster / BPM / Key
- Neighbor indices + cosine sims embedded as JSON in HTML for instant JS lookup
- `--neighbors N`, `--min-cluster-size N` args

**Fix:** Original ran HDBSCAN directly on normalized 512d vectors (all noise, cosine
distances ≈ 0.027). Fix: UMAP reduce first → HDBSCAN on 10d euclidean.

Result on real library: 15 clusters from 1674 embedded tracks.

---

## 2026-06-19 — Data Science Diagnostics (scripts/diagnostics.py)

**What shipped:**
- 6-panel self-contained HTML dashboard (Plotly.js CDN)
  1. Library Coverage — BPM/Key/Genre/Energy/Embedding %
  2. Genre Distribution — top-20 bar chart
  3. BPM Distribution — histogram
  4. Camelot Key Usage — minor(A) vs major(B) grouped bars
  5. Embedding Cosine Similarity Distribution — histogram of pairwise sims
  6. Cluster Diagnostics — per-cluster table (size, intra-sim, genre purity, BPM stats)
     + inter-cluster mean similarity
- `--sample N` controls how many embeddings for similarity panel
- `scripts/genre_detect.py` — zero-shot CLAP genre classification (text embedding
  cosine similarity) → folder-heuristic fallback; `--apply` writes to DB

---

## 2026-06-19 — Zero-Shot Genre Detection (scripts/genre_detect.py)

**What shipped:**
- CLAP text-embedding cosine similarity against genre prompt bank (23 genres)
- Softmax sharpening of scores (`× 10` exponent) to get probability-like values
- Folder-name heuristic fallback (regex table, 20 rules) when no embedding
- `--apply` writes genre to DB; `--min-conf` threshold (default 0.20)
- CLaMP3 encodes 10 tracks at 28.7s/track mean; inter-track cosine sims
  max=0.928, mean=0.863, min=0.000

---

## 2026-06-20 — Merge: feat/clamp3-integration → dev (366 tests passing)

**Merge commit:** `541c769`

Resolved conflicts:
- `CLAUDE.md` (3 blocks): merged architecture module lists, kept verbose design
  invariants, replaced stale Sync Notes with clean config TOML template
- `uv.lock` (2 blocks): took feat branch version (adds `requests`, `samplings`,
  `soundfile` for `[clamp3]` extra)

Branch `feat/clamp3-integration` deleted after successful merge.

**Current state of dev branch: 366 tests, 0 failures.**

---

## Current Status (2026-06-20)

| Phase | Feature | Status |
|---|---|---|
| 0–4 | Migration, schema, core CLI | Complete |
| 5 | pipeline command (8→17 steps) | Complete |
| 6 | import directory | Complete |
| 7 | BPM/key/energy analysis | Complete |
| 8 | Metadata enrichment (file tags + Discogs + MusicBrainz) | Complete |
| 8b | Mixxx pre-analysis BLOBs (BeatGrid + KeyMap) | Complete |
| 12 | CLAP embeddings + UMAP/HDBSCAN clustering | Complete |
| 12b | CLaMP3 embedding backend | Complete |
| 13 | Cue detection (allin1 + librosa) | Complete |
| 16 | Triage player (mpv) | Complete |
| — | DJ next-track suggestion | Complete |
| — | Library UMAP visualization | Complete |
| — | Data science diagnostics dashboard | Complete |
| — | Zero-shot genre detection | Complete |

**Test count:** 366 (2026-06-20)
**Branch:** dev
**DB:** `~/.multidj/library.sqlite` (real: Dropbox path via config.toml)
**Real library:** ~3489 active tracks, ~1674 with CLAP embeddings
