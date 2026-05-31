# MVP Progress — multidj

**Stack:** Python 3.9+, SQLite (stdlib only for core), librosa + mutagen (analysis), torch + allin1 (cue detection), CLAP + UMAP + HDBSCAN (embeddings/clustering)
**Entry point:** `multidj` (primary), `mixxx-tool` (legacy alias)
**DB default:** `~/.multidj/library.sqlite`
**Last verified:** 2026-05-28

---

## Completed Features

### Infrastructure
- [x] Package structure (`pyproject.toml`, `__init__.py`, `__main__.py`)
- [x] `db.py` — `connect()` (read-only URI + read-write), `resolve_db_path()`, `ensure_not_empty()`, migration runner
- [x] `backup.py` — timestamped `.sqlite` copies to `~/.multidj/backups/` before every write
- [x] `models.py` — `LibrarySummary` dataclass
- [x] `utils.py` — `emit()` for JSON / human-readable output
- [x] `constants.py` — genres, crate prefixes, regex patterns, `CAMELOT_KEY_MAP`, `KNOWN_ADAPTERS`
- [x] `config.py` — `load_config()`, `save_config()`, `get_music_dir()`, `get_llm_config()`; `~/.multidj/config.toml`
- [x] `cli.py` — argparse with global `--json` / `--db` flag hoisting; all subcommands wired
- [x] `adapters/base.py` — `SyncAdapter` ABC (`import_all`, `push_track`, `full_sync`)
- [x] `adapters/mixxx.py` — `MixxxAdapter`: import + sync + crate sync + hot cue sync
- [x] `adapters/directory.py` — `DirectoryAdapter`: import tracks from filesystem paths
- [x] `pipeline.py` — `run_pipeline()`: chains all 13 steps, one backup at start, step isolation, `--skip-*` flags
- [x] Migrations: `001_initial.sql`, `002_cue_points.sql`, `003_crates_add_type.sql`, `004_cue_points_add_confidence_source.sql`, `005_embeddings.sql`

### Commands
- [x] `import mixxx` — one-time pull from `~/.mixxx/mixxxdb.sqlite`; dry-run + apply
- [x] `import directory PATH` — scan a directory for audio files and import; `--apply`, `--no-backup`
- [x] `sync mixxx` — push dirty tracks + crates + hot cues back to Mixxx; dry-run + apply
- [x] `scan` — active track counts (genre, BPM, key, rating), crate count
- [x] `backup` — manual backup with optional `--backup-dir`
- [x] `audit genres` — genre distribution, case collisions, emoji/uninformative detection
- [x] `audit metadata` — field coverage percentages across active tracks
- [x] `audit mismatches` — detect artist/title swap mismatches vs filenames
- [x] `clean genres` — case normalization, uninformative → NULL, whitespace; `--apply`, `--limit`
- [x] `clean text` — whitespace strip/collapse + trailing garbage cleanup in title/artist/album; `--apply`
- [x] `parse` — extract artist/title/remixer from filenames; confidence tiers; `--apply`, `--force`, `--min-confidence`, `--limit`
- [x] `enrich language` — detect Hebrew tracks by Unicode range; read-only report
- [x] `analyze bpm` — librosa beat tracking (start/mid/end windows); reports variable-BPM; `--apply`, `--force`, `--limit`
- [x] `analyze key` — Krumhansl-Schmuckler key detection via librosa/chroma; `--apply`, `--write-tags`, `--force`, `--limit`
- [x] `analyze energy` — librosa RMS × spectral centroid, normalized 0–1; `--apply`, `--force`, `--limit`
- [x] `analyze embed` — CLAP 512-dim audio embeddings (3-window sampling, mean-pooled); `--apply`, `--force`, `--limit`
- [x] `analyze cues` — structural segmentation via allin1 (neural) + librosa cross-validation; detects intro/verse/chorus/drop/outro; high-confidence cues synced to Mixxx; `--apply`, `--force`, `--limit`
- [x] `cluster vibe` — UMAP (512d→10d) + HDBSCAN → `Vibe/` crates; LLM naming via OpenAI-compatible API; `--apply`
- [x] `similar <track>` — KNN cosine distance in embedding space; `--top N`
- [x] `cues clear` — remove all auto-detected cues and reset `intro_end`/`outro_start`; `--apply`
- [x] `crates audit` — three-tier classification (catch-all / auto / hand-curated), threshold report
- [x] `crates hide/show/delete` — bulk crate management; `--apply`, `--min-tracks`
- [x] `crates rebuild` — delete all auto-crates, recreate from DB; config-driven: Genre:/Lang:/BPM:/Key:/Energy:/Vibe:; `--apply`
- [x] `dedupe` — artist+title and filesize+duration matching; `--apply`, `--by`
- [x] `triage` — mpv-based keyboard audition: KP0=soft-delete, Shift+KP0=hard-delete (rm file), KP1–5=rating, n=skip, ←/→=±30s; `--crate`, `--limit`
- [x] `report dashboard` — standalone interactive HTML dashboard; `--output`
- [x] `pipeline` — chains all 13 steps: import→fix_mismatches→parse→dedupe→bpm→key→energy→cues→embed→cluster→genres→clean_text→crates→sync→report; `--apply`, `--skip-<step>`, `--music-dir`, `--limit`
- [x] `config set-db / set-music-dir / show` — persistent config management

### Phase 13 — Automatic Cue Detection (2026-05-28)
- [x] `multidj/migrations/004_cue_points_add_confidence_source.sql` — adds `confidence` ('high'/'low') + `source` ('auto'/'manual') to `cue_points`
- [x] `multidj/cues.py` — `detect_cues()`, `analyze_cues()`, `clear_cues()`
  - **allin1** (primary): transformer model → labeled segments + bar grid (downbeats)
  - **librosa** (secondary): RMS energy + spectral flux + chroma novelty → transition timestamps
  - Cross-validation: both agree within ±1 bar → `confidence='high'`; allin1 only → `confidence='low'`
  - Bar-snapping: all positions snapped to nearest allin1 downbeat
  - Derived `drop` cue from first `chorus`/`instrumental` segment
  - `source='auto'` on all machine cues; `source='manual'` never overwritten
- [x] Mixxx hot cue sync: intro=slot 0 (blue), drop=slot 1 (red), outro=slot 2 (green); high-confidence only; slots 0/1/2 wiped and repopulated each sync
- [x] Escape hatch: `multidj cues clear --apply` wipes all auto cues + NULLs `intro_end`/`outro_start`
- [x] Pipeline step 8 (after energy, before embed); `--skip-cues` flag; requires `uv sync --extra embeddings`

### Phase 16 — Triage Player (2026-05-28)
- [x] `multidj/triage.py` — `build_triage_queue()`, `write_m3u()`, `launch_session()`, `tag_track()`
- [x] `multidj/assets/triage.lua` — mpv Lua script: KP0=soft-delete, Shift+KP0=hard-delete, KP1–5=rating, n=skip, ←/→=±30s seek; calls `multidj triage tag` on each decision
- [x] `multidj triage [--crate NAME] [--limit N]` — launches mpv with M3U playlist
- [x] `multidj triage tag` — internal write subcommand called by Lua script

### Phase 12 — Semantic Embeddings + Clustering (2026-05-27)
- [x] `multidj/migrations/005_embeddings.sql` — `embeddings` table: `(track_id PK, model_name, vector BLOB, created_at)`
- [x] `multidj/embed.py` — `analyze_embed()`, `find_similar()`, `load_clap_model()`, `store_embedding()`, `load_embeddings_from_db()`
  - Model: `laion/larger_clap_music` (512-dim float32 vectors)
  - 3-window sampling: start / mid / end × 30s, mean-pooled
- [x] `multidj/cluster.py` — `cluster_embeddings()`, `cluster_vibe()`, `name_cluster()`
  - UMAP: 512d→10d (cosine metric); HDBSCAN: automatic cluster count; noise → `Vibe/Unclassified`
  - LLM naming via `[llm]` config; fallback to `Vibe/Cluster-NN`
- [x] PoC verified (2026-05-27): 35 tracks encoded, 3 clusters found, 4 `Vibe/` crates written

### Test Suite
- [x] 271 tests passing (0 failures)
- [x] Fixture: 10-track canonical dataset covering duplicates, Hebrew, case collisions, missing metadata
- [x] Tests: `test_import`, `test_import_directory`, `test_sync`, `test_scan`, `test_audit`, `test_enrich`, `test_parse`, `test_clean`, `test_crates`, `test_dedupe`, `test_analyze`, `test_analyze_energy`, `test_config`, `test_mixxx_crate_sync`, `test_pipeline`, `test_embed`, `test_cluster`, `test_llm_config`, `test_triage`, `test_analyze_cues`, `test_mixxx_cue_sync`, `test_migrations`, `test_report`, `test_session_changes`

### Safety Model
- [x] All commands dry-run by default; nothing written without `--apply`
- [x] Auto backup before every write (skip with `--no-backup`)
- [x] Soft-delete (`deleted=1`) everywhere — all destructive operations are recoverable
- [x] Per-track error isolation in all analyze commands
- [x] `deleted = 0` filter applied in all stats and operations
- [x] Wrong-DB guard: clear error if pointed at Mixxx DB instead of MultiDJ DB

---

## Roadmap Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Package rename `mixxx_tool` → `multidj` | **Done** |
| 1 | DB layer, migration runner, schema v1 | **Done** |
| 2 | `import mixxx` — one-time pull from Mixxx | **Done** |
| 3 | Port all commands to MultiDJ schema | **Done** |
| 4 | `sync mixxx` — push dirty tracks back to Mixxx | **Done** |
| 5 | Remove `mixxx-tool` alias | Deferred |
| 6 | Standalone ingestion — `import directory`, `analyze bpm/energy`, config-driven crates | **Done** |
| 7 | Mixxx crate sync | **Done** |
| 8 | Fingerprint enrichment — pyacoustid → AcoustID for unknown tracks | Planned |
| 9 | Cue point detection | **Done** (as Phase 13) |
| 10 | Mixxx cue sync | **Done** (as Phase 13) |
| 11 | MCP server — expose commands as agent-callable tools | Planned |
| 12 | Semantic embeddings — CLAP → UMAP+HDBSCAN → `Vibe/` crates | **Done** |
| 12b | Similarity queries — `multidj similar` KNN search | **Done** |
| 13 | Automatic cue detection — allin1 + librosa → intro/drop/outro → Mixxx hot cues | **Done** |
| 14 | MCP embedding/playlist tools | Planned |
| 15 | Natural language DJ — LLM → playlist | Vision |
| 16 | Triage player — `multidj triage` mpv + Lua keyboard-driven audition | **Done** |

---

## BPM Range Definitions

| Crate Name | BPM Range |
|---|---|
| `BPM:<90` | 0 – 89 |
| `BPM:90-105` | 90 – 104 |
| `BPM:105-115` | 105 – 114 |
| `BPM:115-125` | 115 – 124 |
| `BPM:125-130` | 125 – 129 |
| `BPM:128-135` | 128 – 134 |
| `BPM:135-160` | 135 – 159 |
| `BPM:160-175` | 160 – 174 |
| `BPM:175+` | 175+ |

Note: 125–130 and 128–135 intentionally overlap — tracks at 128–130 BPM appear in both Tech House and Techno crates.

---

## Known Issues / Open Items

| Priority | Item |
|----------|------|
| Medium | Fingerprint enrichment (Phase 8) not yet built — unknown tracks have no AcoustID lookup |
| Medium | MCP server (Phase 11) not yet built — commands not agent-callable |
| Low | `mixxx-tool` legacy alias still active |
| Low | Crates created directly in Mixxx are overwritten on next `sync mixxx --apply` |
| Low | Energy normalization is library-relative: single-track batch gets `energy=0.5` |

---

## Installation

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install -r requirements-dev.txt
source .venv/bin/activate

# Optional extras
uv sync --extra analysis     # librosa — BPM, key, energy analysis
uv sync --extra embeddings   # torch, torchaudio, transformers, librosa, umap-learn, hdbscan, openai, allin1 — embeddings, clustering, cue detection
```

## Usage Quick-Reference

```bash
# One-time bootstrap
multidj import mixxx --apply               # from Mixxx
multidj import directory ~/Music --apply   # from raw files

# Daily workflow (all 13 steps)
multidj pipeline --apply
multidj pipeline --apply --skip-embed --skip-cluster --skip-cues  # skip slow ML steps

# Analysis
multidj analyze bpm --apply
multidj analyze key --apply
multidj analyze energy --apply
multidj analyze cues --apply               # auto-detect intro/drop/outro (requires embeddings extra)
multidj analyze embed --apply              # CLAP embeddings (requires embeddings extra)
multidj cluster vibe --apply              # Vibe/ crates from embeddings

# DJ tools
multidj similar "Artist - Track.mp3"      # find sonically similar tracks
multidj triage --crate "New Tracks"       # keyboard audition via mpv
multidj cues clear --apply               # wipe all auto-detected cues

# Library maintenance
multidj scan
multidj audit genres
multidj clean genres --apply
multidj crates rebuild --apply
multidj sync mixxx --apply
multidj dedupe --apply

# Testing
.venv/bin/pytest tests/ -v   # 271 tests
```
