# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`MultiDJ` (package: `multidj`) is a Python 3.9+ CLI for DJ music library management. It maintains its own SQLite DB (`~/.multidj/library.sqlite`) as the source of truth and syncs to DJ software (Mixxx first; Rekordbox/Serato as future adapters). All write commands are **dry-run by default**, automatic backups are created before any writes, and JSON output is available for machine consumption. Eventually exposed as an MCP server for agent-native access.

All phases through Phase 16 are complete: migration (0–4), ingestion + pipeline (6–7), metadata enrichment (8), semantic embeddings + clustering (12/12b), cue detection (13), triage player (16), Mixxx pre-analysis BLOBs, CLaMP3 integration, DJ next-track suggestion, and library visualization.

## Installation and Running

```bash
uv sync                           # core deps
uv sync --extra analysis          # + librosa (BPM, key, energy)
uv sync --extra embeddings        # + torch, transformers, umap, hdbscan, openai (also for cue detection)
uv sync --extra clamp3            # + CLaMP3 backend (MERT-v1-95M); also needs:
git submodule update --init vendor/clamp3
uv sync --extra enrich            # + musicbrainzngs, python3-discogs-client, rapidfuzz
source .venv/bin/activate
multidj import mixxx --apply      # one-time: populate MultiDJ DB from Mixxx
multidj pipeline --apply          # daily workflow: import→parse→analyze→crates→sync
multidj <command>                 # primary entry point
mixxx-tool <command>              # legacy alias (same binary)
# Optional: mpv media player (required for `multidj triage`)
# Fedora/RHEL: sudo dnf install mpv
```

Override the DB path: `--db <path>` flag or `MULTIDJ_DB_PATH` environment variable.

## Commands

| Command | Description |
|---|---|
| `pipeline` | Primary daily workflow: chains all 17 steps; `--apply`, `--skip-<step>`, `--music-dir` |
| `import mixxx` | One-time pull from `~/.mixxx/mixxxdb.sqlite` into MultiDJ DB |
| `import directory PATH` | Import audio files from a directory; `--apply`, `--no-backup` |
| `sync mixxx` | Push dirty tracks + crates back to Mixxx; `--apply`, `--no-backup` |
| `scan` | Library statistics (track counts, metadata coverage) |
| `backup` | Manual backup |
| `parse` | Propose artist/title/remixer from filenames; `--apply`, `--min-confidence`, `--force` |
| `enrich language` | Report Hebrew tracks detected via Unicode range check (read-only) |
| `enrich metadata` | Three-layer enrichment: file tags → Discogs → MusicBrainz; `--apply`, `--force`, `--limit`, `--write-tags` (requires `[enrich]`) |
| `audit genres` | Genre distribution, collisions, suspicious values |
| `audit metadata` | Field coverage report |
| `clean genres` | Genre normalization (case, uninformative removal, whitespace) |
| `clean text` | Artist/title/album cleanup + promo/download marker removal |
| `analyze bpm` | BPM detection via librosa; start/middle/end windows; `--apply`, `--force`, `--limit` |
| `analyze key` | Key detection via librosa; `--apply`, `--write-tags`, `--force`, `--limit` |
| `analyze energy` | Energy score (RMS × centroid, 0–1); `--apply`, `--force`, `--limit` |
| `analyze embed` | Audio embeddings stored in `embeddings` table; `--model clap\|clamp3`, `--apply`, `--force`, `--limit` |
| `analyze cues` | Structural segmentation (intro/verse/chorus/drop/outro) via allin1 + librosa; `--apply`, `--force`, `--limit` |
| `analyze mixxx-blobs` | Write BeatGrid + KeyMap BLOBs to Mixxx DB; `--apply`, `--force`, `--lock-bpm`, `--limit` |
| `cues clear` | Remove all auto-detected cues from DB; `--apply` |
| `cluster vibe` | UMAP+HDBSCAN clustering → `Vibe/` auto-crates; `--apply`, `--min-cluster-size`, `--model clap\|clamp3` |
| `similar TRACK` | KNN cosine-distance search; `--top N`, `--model clap\|clamp3` (read-only) |
| `suggest TRACK` | DJ next-track suggestion: 70% cosine + 15% BPM + 15% Camelot key; `--top N`, `--bpm-window F`, `--any-cluster`, `--model` |
| `crates audit` | Crate inventory and classification |
| `crates hide/show/delete` | Bulk crate management |
| `crates rebuild` | Rebuild auto-crates (Genre:/BPM:/Key:/Energy:/Lang:/Vibe/); `--apply`, `--min-tracks` |
| `dedupe` | Duplicate detection (artist+title or filesize+duration) |
| `triage` | Keyboard-driven track audition via mpv; KP0=soft-delete, Shift+KP0=hard-delete, KP1–5=rating, n=skip, ←/→=±30s; `--crate NAME`, `--limit N` |
| `report dashboard` | Standalone interactive HTML dashboard; `--output PATH` |

**Global flags** (accepted anywhere): `--json`, `--db <path>`, `--version`

**Safety flags on write commands**: `--apply` (required to write), `--no-backup`, `--limit <N>`

## Architecture

**Layered design:**

1. **`cli.py`** — argparse entry point; hoists global flags from any argv position; routes to command modules; `_format_suggest()` for human output
2. **`db.py`** — `connect(db_path, readonly=True)` context manager; auto-applies SQL migrations on write connections; `resolve_db_path()`, `ensure_db_exists()`, `ensure_not_empty()`, `table_exists()`
3. **`backup.py`** — timestamped DB copies before every write; returns `BackupResult`
4. **`utils.py`** — `emit(data, json_mode)` for unified JSON/human output
5. **`constants.py`** — uninformative genre list, crate classifier prefixes (`Vibe/` included), regex patterns, `CAMELOT_KEY_MAP`, `KNOWN_ADAPTERS`
6. **`config.py`** — `load_config()`, `save_config()`, `get_music_dir()`, `get_llm_config()`, `get_enrich_config()`; reads/writes `~/.multidj/config.toml`
7. **`pipeline.py`** — `run_pipeline()`: 17 steps (import→fix_mismatches→parse→enrich→bpm→key→mixxx_blobs→energy→embed→cluster→cues→genres→clean_text→crates→sync→dedupe→report); one backup at start; per-step error isolation; lazy-imports embed/cluster/cues for graceful degradation
8. **`embed.py`** — dual-backend audio embedding: `analyze_embed()`, `find_similar()`, `store_embedding()`, `load_embeddings_from_db(model_name=)`; dispatches on `model="clap"|"clamp3"`; CLAP uses `laion/larger_clap_music` (512-dim, 3×30s windows); composite PK `(track_id, model_name)` allows both models per track
9. **`embed_clamp3.py`** — CLaMP3 backend: `load_clamp3_model()`, `encode_audio_clamp3()`; MERT-v1-95M → non-overlapping 5s chunks → CLaMP3 SAAS encoder → 768-dim vector; requires `vendor/clamp3` submodule + `[clamp3]` extra. **Note:** CLaMP3 collapses audio-audio discrimination by design — use CLAP for clustering/similarity, CLaMP3 for future text→audio agent search
10. **`suggest.py`** — DJ next-track ranking: `suggest_next()`; score = 0.70×cosine_sim + 0.15×bpm_compat + 0.15×camelot_key_compat; `_parse_camelot()` handles Camelot wheel + musical notation; filters to same `Vibe/` cluster by default
11. **`cluster.py`** — UMAP (dim→10d, cosine) + HDBSCAN → `Vibe/` crates; LLM naming via OpenAI-compatible API (falls back to `Vibe/Cluster-NN`); `model=` param for variable embedding dims
12. **`cues.py`** — `detect_cues(filepath, bpm)` runs allin1 + librosa cross-validation → cue candidates; `analyze_cues()`, `clear_cues()`. `source='manual'` cues never overwritten
13. **`enrich.py`** — three-layer metadata enrichment: file tags (mutagen) → Discogs API → MusicBrainz; fills `release_year`, `label`, `album`, `genre`, `track_tags`
14. **`mixxx_blobs.py`** — hand-rolled protobuf encoder; writes BeatGrid-2.0 + KeyMap-1.0 BLOBs into Mixxx SQLite; no protobuf dependency
15. **`models.py`** — `LibrarySummary` dataclass
16. **`adapters/mixxx.py`** — `MixxxAdapter`: import from Mixxx; push tracks + crates + cues back on sync; `_push_cues_to_mixxx()` writes intro (slot 0, blue) / drop (slot 1, red) / outro (slot 2, green)
17. **`adapters/directory.py`** — `DirectoryAdapter`: imports audio files from filesystem paths
18. **Command modules** (`scan`, `audit`, `clean`, `analyze`, `parse`, `enrich`, `crates`, `dedupe`) — pure business logic, read-only unless `--apply`

**Scripts** (standalone tools, not wired into CLI):
- `scripts/viz_library.py` — interactive UMAP HTML scatter plot: embedding or metadata layout; color by Genre/Cluster/BPM/Key; click any point → sidebar shows top-5 precomputed neighbors with BPM/key/cluster; HDBSCAN clusters shown inline
- `scripts/diagnostics.py` — data science evaluation dashboard: 6-panel HTML (coverage, genre, BPM, key, similarity distribution, cluster diagnostics with intra/inter-cluster sim + genre purity)
- `scripts/genre_detect.py` — zero-shot genre detection: CLAP text embedding similarity → folder heuristic fallback; `--apply` writes to DB

**Migration system:** SQL files in `multidj/migrations/NNN_name.sql` auto-applied in numeric order on `connect(readonly=False)`. Schema version in `schema_version` table. **Critical:** `connect(readonly=True)` skips migrations — commands reading tables from recent migrations must open a write connection first.

**MultiDJ DB schema** (`~/.multidj/library.sqlite`):
- `tracks` — (`id`, `path`, `artist`, `title`, `album`, `genre`, `bpm`, `key`, `language`, `duration`, `filesize`, `rating`, `play_count`, `remixer`, `energy`, `intro_end`, `outro_start`, `release_year`, `label`, `deleted`, `created_at`, `updated_at`)
- `track_tags` — arbitrary key/value metadata per track (`discogs_styles`, `discogs_primary_style`, `catalog_number`)
- `crates` — named collections with `type` and `show` flag
- `crate_tracks` — many-to-many join
- `cue_points` — (`id`, `track_id`, `type`, `position`, `label`, `confidence` ['high'/'low'], `source` ['auto'/'manual'])
- `sync_state` — per-track per-adapter dirty flag; trigger fires on any `tracks` update
- `embeddings` — `(track_id, model_name)` composite PK (migration 007); `vector` BLOB (float32); CLAP=512-dim, CLaMP3=768-dim; multiple models coexist per track

**Key design invariants:**
- `deleted = 0` filter applied everywhere
- Write operations use `executemany()` for batched DB updates
- All analyze commands isolate per-track errors
- Crate three-tier protection: catch-all → auto (`Genre:`/`BPM:`/`Key:`/`Energy:`/`Lang:`/`Vibe/`) → hand-curated
- `Vibe/` crates: clear-and-rebuild each `cluster vibe --apply` run; noise tracks → `Vibe/Unclassified`
- `pipeline` takes one backup at start; steps pass `backup_dir=False` to suppress per-step backups
- `sync mixxx --apply` reconciles Mixxx crates; MultiDJ is source of truth

## Tests and Linting

```bash
.venv/bin/pytest tests/ -v           # run the full suite (342 tests)
.venv/bin/pytest tests/test_scan.py  # single module
```

Fixture DB (10 tracks) is in `tests/fixtures/data.py`. `make_mixxx_db()` and `make_multidj_db()` in `tests/fixtures/` build fresh SQLite files per test via `tmp_path`. CLaMP3-dependent tests use `sys.modules` mocking.

No linting config. PEP 8 conventions with type hints throughout.

## Embedding Models

| Model | Alias | Dim | Strength | Use for |
|---|---|---|---|---|
| `laion/larger_clap_music` | `clap` | 512 | Audio-audio discrimination in music space | Clustering, `similar`, `suggest` |
| `clamp3_saas` (MERT-v1-95M → CLaMP3) | `clamp3` | 768 | Cross-modal alignment (audio ↔ text ↔ MIDI) | Future: text→audio agent vibe search |

CLAP inter-track cosine sim on this library: mean ≈ 0.97 (homogeneous DJ library). Relative ranking is meaningful even with compressed absolute values. CLaMP3 collapses to mean ≈ 0.96 but for different reasons — use only for cross-modal queries.

## Config (`~/.multidj/config.toml`)

```toml
[db]
path = "/path/to/library.sqlite"

[pipeline]
music_dir = "/path/to/Music"

[crates]
bpm = true; key = true; genre = true; energy = true; language = true

[llm]          # optional — for cluster name generation
base_url = "http://localhost:11434/v1"
api_key  = "ollama"
model    = "llama3"

[discogs]      # optional — for metadata enrichment
token    = "..."
user_agent = "MultiDJ/1.0"

[musicbrainz]  # optional
user_agent = "MultiDJ/1.0 contact@example.com"
```
