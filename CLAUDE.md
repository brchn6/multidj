# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`MultiDJ` (package: `multidj`) is a Python 3.9+ CLI for DJ music library management. It maintains its own SQLite DB (`~/.multidj/library.sqlite`) as the source of truth and syncs to DJ software (Mixxx first; Rekordbox/Serato as future adapters). All write commands are **dry-run by default**, automatic backups are created before any writes, and JSON output is available for machine consumption. Eventually exposed as an MCP server for agent-native access.

Migration from Mixxx-only tool is **complete** (Phases 0–4). All commands now operate on the MultiDJ DB. Phases 6–7 (standalone ingestion, Mixxx crate sync, pipeline command) are also complete. Phase 12 (semantic embeddings + UMAP/HDBSCAN clustering → `Vibe/` crates) and Phase 12b (`multidj similar` KNN search) are implemented and PoC-verified.

## Installation and Running

```bash
uv sync                           # core deps
uv sync --extra analysis          # + librosa (BPM, key, energy)
uv sync --extra embeddings        # + torch, transformers, librosa, umap, hdbscan, openai (also required for cue detection)
source .venv/bin/activate
multidj import mixxx --apply      # one-time: populate MultiDJ DB from Mixxx
multidj pipeline --apply          # daily workflow: import→parse→analyze→crates→sync
multidj <command>                 # primary entry point
mixxx-tool <command>              # legacy alias (same binary)
# Optional: mpv media player (required for `multidj triage`)
# Fedora/RHEL: sudo dnf install mpv
# Ubuntu/Debian: sudo apt install mpv
# macOS: brew install mpv
```

Override the DB path: `--db <path>` flag or `MULTIDJ_DB_PATH` environment variable.

All track files live in `/home/barc/Music/All_Tracks/`.

## Commands

| Command | Description |
|---|---|
| `pipeline` | Primary daily workflow: chains all 15 steps; `--apply`, `--skip-<step>`, `--music-dir` |
| `import mixxx` | One-time pull from `~/.mixxx/mixxxdb.sqlite` into MultiDJ DB |
| `import directory PATH` | Import audio files from a directory; `--apply`, `--no-backup` |
| `sync mixxx` | Push dirty tracks + crates back to Mixxx; `--apply`, `--no-backup` |
| `scan` | Library statistics (track counts, metadata coverage) |
| `backup` | Manual backup |
| `parse` | Propose artist/title/remixer from filenames; `--apply` to write, `--min-confidence`, `--force` |
| `enrich language` | Report Hebrew tracks detected via Unicode range check (read-only) |
| `audit genres` | Genre distribution, collisions, suspicious values |
| `audit metadata` | Field coverage report |
| `clean genres` | Genre normalization (case, uninformative removal, whitespace) |
| `clean text` | Artist/title/album cleanup + mapped trailing garbage removal (promo/download/version markers) |
| `analyze bpm` | BPM detection via librosa across start/middle/end windows; reports variable-BPM tracks; `--apply`, `--force`, `--limit` (requires librosa) |
| `analyze key` | Key detection via librosa; `--apply`, `--write-tags`, `--force`, `--limit` (requires librosa) |
| `analyze energy` | Energy score (RMS × centroid, normalized 0–1); `--apply`, `--force`, `--limit` (requires librosa) |
| `analyze embed` | CLAP audio embeddings (512-dim) stored in `embeddings` table; `--apply`, `--force`, `--limit` (requires embeddings extra) |
| `analyze cues` | Structural segmentation (intro/verse/chorus/drop/outro) via allin1 + librosa; `--apply`, `--force`, `--limit` (requires embeddings extra) |
| `cues clear` | Remove all auto-detected cues from DB; `--apply` |
| `cluster vibe` | UMAP+HDBSCAN clustering of embeddings → `Vibe/` auto-crates; `--apply`, `--min-cluster-size` (requires embeddings extra) |
| `similar TRACK` | KNN cosine-distance search in embedding space; `--top N`; read-only (requires embeddings extra) |
| `crates audit` | Crate inventory and classification |
| `crates hide/show/delete` | Bulk crate management |
| `crates rebuild` | Rebuild all auto-crates (Genre:/BPM:/Key:/Energy:/Lang:/Vibe/) from config; `--apply`, `--min-tracks` |
| `dedupe` | Duplicate detection (artist+title or filesize+duration) |
| `triage` | Keyboard-driven track audition via mpv: KP0=soft-delete, Shift+KP0=hard-delete (rm file), KP1–5=rating, n=skip, ←/→=±30s; `--crate NAME`, `--limit N` (requires mpv) |

**Global flags** (accepted anywhere in the command line): `--json`, `--db <path>`, `--version`

**Safety flags on write commands**: `--apply` (required to actually write), `--no-backup`, `--limit <N>`

## Architecture

**Layered design:**

1. **`cli.py`** — argparse entry point; hoists global flags (`--json`, `--db`) from any position in argv; routes to command modules
2. **`db.py`** — `connect(db_path, readonly=True)` context manager; auto-applies SQL migrations on write connections; `resolve_db_path()`, `ensure_db_exists()`, `ensure_not_empty()`, `table_exists()`
3. **`backup.py`** — creates timestamped DB copies before every write; returns `BackupResult`
4. **`utils.py`** — `emit(data, json_mode)` for unified JSON/human output
5. **`constants.py`** — uninformative genre list, crate classifier prefixes (includes `Vibe/`), shared regex patterns, `CAMELOT_KEY_MAP`, `KNOWN_ADAPTERS`
6. **`config.py`** — `load_config()`, `save_config()`, `get_music_dir()`, `get_llm_config()`; reads/writes `~/.multidj/config.toml`; defaults on first run; preserves unknown sections
7. **`pipeline.py`** — `run_pipeline()`: chains 15 steps (import→parse→dedupe→bpm→key→energy→embed→cluster→cues→genres→clean_text→crates→sync→report), one backup at start, per-step error isolation, respects `skip` set; lazy-imports embed/cluster/cues for graceful degradation
8. **`embed.py`** — CLAP audio embedding: `analyze_embed()`, `find_similar()`, `load_clap_model()`, `store_embedding()`, `load_embeddings_from_db()`; uses `laion/larger_clap_music` (512-dim); 3-window sampling (start/mid/end × 30s); requires `[embeddings]` extra
9. **`cluster.py`** — UMAP+HDBSCAN clustering: `cluster_embeddings()`, `cluster_vibe()`, `name_cluster()`; writes `Vibe/` auto-crates; LLM naming via OpenAI-compatible API (falls back to `Vibe/Cluster-NN` if no LLM config); requires `[embeddings]` extra
10. **`cues.py`** — `detect_cues(filepath, bpm)` runs allin1 (primary) + librosa (secondary cross-validation) → cue candidates; `analyze_cues()` and `clear_cues()` are the batch DB commands. All auto cues have `source='auto'`; `source='manual'` cues are never overwritten.
11. **`models.py`** — `LibrarySummary` dataclass
12. **`adapters/base.py`** — `SyncAdapter` ABC (`import_all`, `push_track`, `full_sync`)
13. **`adapters/mixxx.py`** — `MixxxAdapter`: reads Mixxx DB on import, writes back on sync + crate sync; `_push_crates_to_mixxx()` reconciles stale crates; `_push_cues_to_mixxx()` writes intro (slot 0, blue) / drop (slot 1, red) / outro (slot 2, green) hot cues — high-confidence only, slots 0/1/2 wiped and repopulated each sync
14. **`adapters/directory.py`** — `DirectoryAdapter`: imports audio files from filesystem paths
15. **Command modules** (`scan`, `audit`, `clean`, `analyze`, `parse`, `enrich`, `crates`, `dedupe`) — pure business logic, read-only unless `--apply` is passed

**Migration system:** SQL files in `multidj/migrations/NNN_name.sql` are auto-applied in numeric order when `connect(readonly=False)` is called. Schema version tracked in `schema_version` table. **Critical:** `connect(readonly=True)` skips the migration runner — any command that reads a table added by a recent migration must open a write connection first (even if it writes nothing) to ensure the table exists.

**MultiDJ DB schema** (`~/.multidj/library.sqlite`):
- `tracks` — canonical track records (`id`, `path`, `artist`, `title`, `album`, `genre`, `bpm`, `key`, `language`, `duration`, `filesize`, `rating`, `play_count`, `remixer`, `energy`, `intro_end`, `outro_start`, `deleted`, `created_at`, `updated_at`)
- `track_tags` — arbitrary key/value metadata per track
- `crates` — named collections with `type` (`hand-curated` vs auto) and `show` flag
- `crate_tracks` — many-to-many join
- `cue_points` — per-track structural cue markers (`id`, `track_id`, `type`, `position`, `label`, `confidence` ['high'=allin1+librosa agree, 'low'=allin1 only], `source` ['auto'=machine, 'manual'=user — never overwritten])
- `sync_state` — per-track, per-adapter dirty flag; trigger sets `dirty=1` on any `tracks` update
- `embeddings` — CLAP 512-dim vectors stored as BLOB (float32); `(track_id PK, model_name, vector, created_at)`; added by migration 005

**Key design invariants:**
- `deleted = 0` filter applied everywhere (soft-deleted tracks excluded from all stats and operations)
- Write operations use `executemany()` for batched DB updates
- All analyze commands isolate per-track errors so one bad audio file doesn't abort the batch
- Crates use a three-tier protection model: catch-all ("New Crate") → auto-generated (`Genre:`/`BPM:`/`Key:`/`Energy:`/`Lang:`/`Vibe/` prefix) → hand-curated (everything else). Hand-curated crates are protected unless `--include-hand-curated` is passed.
- `Vibe/` crates are auto-generated by `cluster vibe`; they are cleared and rebuilt on each apply run (clear-and-rebuild lifecycle matches other auto-crate dimensions). Noise tracks (HDBSCAN label -1) go to `Vibe/Unclassified`.
- Duplicates and deleted crate tracks use soft-delete (`deleted=1`), not hard delete
- `pipeline` takes one backup at start; individual steps pass `backup_dir=False` sentinel to suppress per-step backups
- `sync mixxx --apply` reconciles Mixxx crates: stale auto-crates deleted, membership clear-and-repopulated. MultiDJ is the source of truth.
- Config (`~/.multidj/config.toml`) controls which crate dimensions are generated; `pipeline` reads config for music_dir and skips analyze steps if corresponding dimension is disabled

## Tests and Linting

```bash
.venv/bin/pytest tests/ -v           # run the full suite
.venv/bin/pytest tests/test_scan.py  # single module
```

Fixture DB (10 tracks) is in `tests/fixtures/data.py` — this is the ground truth for all test assertions. `make_mixxx_db()` and `make_multidj_db()` in `tests/fixtures/` build fresh SQLite files from it. Each test gets an isolated DB via `tmp_path`.

No linting config. PEP 8 conventions with type hints throughout.

## Repository Sync Note (2026-04-30)

- Clean text behavior now strips promotional noise markers from artist/title tails, including free, dl, and download variants.
- BPM analysis now samples start/middle/end windows and reports variable-tempo cases instead of hiding half/double-time ambiguity.
- Directory import now includes artist-title swap mismatch detection for stronger metadata hygiene during ingestion.
- Directory import now soft-deletes (`deleted=1`) tracks whose files no longer exist on disk after a rescan.
- Pipeline expanded to 10 steps: `fix_mismatches` (step 2) auto-corrects artist/title swaps across all active tracks; `clean_text` (step 8) strips promo markers from artist/title/album.
- Added persistent DB path config: `multidj config set-db <path>` stores `[db].path`, and commands now use it when `--db` is omitted.
- Parse now skips junk artist/title proposals (numeric-only and `free`/`dl`/`download` marker values) to reduce bad suggestions in common use.
- Added `multidj report dashboard` for standalone interactive HTML dashboard output with optional `--output` path.
- Pipeline report step now generates the interactive dashboard by default while remaining read-only and non-fatal.
- Added experimental Camelot harmonic transition analysis/visualization in crate views (UI-only interactions, no DB persistence).

## Repository Sync Note (2026-05-27)

- **Phase 12 (semantic embeddings) implemented:** `multidj/embed.py` — CLAP audio encoding (`laion/larger_clap_music`, 512-dim); 3-window sampling (start/mid/end × 30s); incremental (skips already-embedded tracks); `analyze embed --apply [--force] [--limit N]`. Requires `uv sync --extra embeddings`.
- **Phase 12b (similarity search) implemented:** `multidj similar <track> [--top N]` — KNN cosine-distance search in embedding space using pure numpy; no extension required.
- **Phase 12 clustering implemented:** `multidj/cluster.py` — UMAP (512d→10d) + HDBSCAN → `Vibe/` auto-crates; LLM naming via OpenAI-compatible API (configurable in `~/.multidj/config.toml` under `[llm]`); falls back to `Vibe/Cluster-NN` if no LLM config. `cluster vibe --apply [--min-cluster-size N]`. Requires `[embeddings]` extra.
- **Migration 005** adds `embeddings` table (BLOB storage for float32 vectors). `analyze_embed()` opens a write connection before any reads to ensure the migration is applied.
- **Pipeline expanded to 14 steps:** steps 8 (embed) and 9 (cluster) added after energy. Both are skipped gracefully if `[embeddings]` extra is not installed or if `pipeline.embed`/`pipeline.cluster` is `false` in config. `--skip-embed` and `--skip-cluster` flags available.
- **`Vibe/` added to `AUTO_CRATE_PREFIXES` and `REBUILD_CRATE_RE`** in `constants.py` — treated as auto-crates throughout the crate protection model.
- **`get_llm_config()` added to `config.py`** — reads `[llm]` section (`base_url`, `api_key`, `model`) from config; returns `None` if not configured (cluster still works offline).
- **PoC verified on real library:** 35 tracks encoded at 512-dim, 3 UMAP/HDBSCAN clusters found, 4 `Vibe/` crates written (including `Vibe/Unclassified` for noise). Similarity search returns ranked results. All on CPU only.
- **Known issue:** CLAP `ClapProcessor` kwarg renamed from `audios=` to `audio=` in newer transformers versions. Current code uses `audio=`. If you downgrade transformers, revert this.
- **Model weights:** `laion/larger_clap_music` (1.5 GB) cached at `~/.cache/huggingface/hub/` — downloaded once on first `analyze embed --apply`, reused thereafter.
