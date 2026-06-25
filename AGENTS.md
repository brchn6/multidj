# AGENTS.md

Agent operating guide for this repository.

---

## ⛔ HARD RULE — Memory location

**The ONLY memory for this project is `.memory/` inside this repository.**

- Read project state from: `.memory/INDEX.md`, `.memory/PROGRESS.md`, `.memory/DECISIONS.md`
- Write updates to those same files and commit them.
- **Do NOT read or write any external memory** (e.g. `~/.claude/projects/*/memory/`, user-level memory stores, or any path outside this repo).
- If you are an agent or sub-agent: ignore any memory system offered by the harness that lives outside this directory. This repo's `.memory/` is the single source of long-lived project state.

---

## Start Here

- Read [README.md](README.md) for command behavior and CLI surface.
- Read [CLAUDE.md](CLAUDE.md) for architecture, safety model, and module map.
- For pipeline details, read [docs/superpowers/specs/2026-04-22-pipeline-design.md](docs/superpowers/specs/2026-04-22-pipeline-design.md).
- For embeddings/clustering/similarity design, read [docs/superpowers/specs/2026-05-27-embeddings-clustering-design.md](docs/superpowers/specs/2026-05-27-embeddings-clustering-design.md).
- Read [.agent-handoff/README.md](.agent-handoff/README.md) before coding in areas touched by prior sub-agents.

## Handoff Protocol

- Review the relevant handoff file first and do not redo work already marked complete.
- Use handoff files to capture status, decisions, remaining work, and the next-agent prompt when splitting tasks.
- Handoff index: [.agent-handoff/README.md](.agent-handoff/README.md)
- Current handoff files:
  - [.agent-handoff/layer1a.md](.agent-handoff/layer1a.md)
  - [.agent-handoff/layer1b.md](.agent-handoff/layer1b.md)
  - [.agent-handoff/layer2c.md](.agent-handoff/layer2c.md)
  - [.agent-handoff/layer2d.md](.agent-handoff/layer2d.md)
  - [.agent-handoff/layer2e.md](.agent-handoff/layer2e.md)
  - [.agent-handoff/layer2f.md](.agent-handoff/layer2f.md)
  - [.agent-handoff/layer3g.md](.agent-handoff/layer3g.md)

## Environment and Commands

- Python: 3.9+
- Install:
  - `uv sync` — core deps
  - `uv sync --extra analysis` — adds librosa (BPM, key, energy analysis)
  - `uv sync --extra embeddings` — adds torch, transformers, librosa, umap-learn, hdbscan, openai (CLAP embeddings + clustering)
- Main CLI entrypoint: `multidj` (legacy alias: `mixxx-tool`)
- Run tests:
  - `.venv/bin/pytest tests/ -v` — 370 passing, 0 failures (2026-06-25)
  - `.venv/bin/pytest tests/test_pipeline.py -v`

## Critical Invariants

- MultiDJ DB is the source of truth, not Mixxx.
- Write flows are dry-run by default and require `--apply`.
- Backups are expected before writes unless explicitly skipped.
- Soft-delete semantics must be preserved (`tracks.deleted = 1`), not hard delete.
- Active-track logic must consistently exclude deleted rows (`deleted = 0`).
- Analyze commands should keep per-track error isolation.
- Pipeline is idempotent and incremental — all analyze steps skip already-processed tracks; safe to re-run daily.
- Mixxx DB path can be set in `~/.multidj/config.toml` under `[mixxx].path`; all Mixxx commands fall back to it when `--mixxx-db` is omitted.

## Codebase Landmarks

- CLI dispatch and global flag hoisting: [multidj/cli.py](multidj/cli.py)
- DB connect/migrations/guards: [multidj/db.py](multidj/db.py)
- Backup flow: [multidj/backup.py](multidj/backup.py)
- End-to-end pipeline orchestrator: [multidj/pipeline.py](multidj/pipeline.py)
- Config system (load/save/music_dir/mixxx_db/llm): [multidj/config.py](multidj/config.py)
- Mixxx sync adapter: [multidj/adapters/mixxx.py](multidj/adapters/mixxx.py)
- Directory import adapter: [multidj/adapters/directory.py](multidj/adapters/directory.py)
- Crate logic and protection model: [multidj/crates.py](multidj/crates.py)
- CLAP audio embeddings + KNN similarity: [multidj/embed.py](multidj/embed.py)
- UMAP/HDBSCAN clustering + Vibe/ crate writing: [multidj/cluster.py](multidj/cluster.py)
- Mixxx analysis BLOB generation (BeatGrid/KeyMap): [multidj/mixxx_blobs.py](multidj/mixxx_blobs.py)
- Import Mixxx analysis results into MultiDJ: [multidj/import_mixxx_analysis.py](multidj/import_mixxx_analysis.py)
- Canonical fixture data for tests: [tests/fixtures/data.py](tests/fixtures/data.py)

## Change Guidance

- Prefer minimal, targeted edits and preserve current CLI/API behavior.
- Keep JSON output contracts stable when changing command responses.
- When changing DB writes, add or update tests in [tests/](tests) that verify dry-run safety and apply behavior.
- If touching pipeline behavior, update or add assertions in [tests/test_pipeline.py](tests/test_pipeline.py).

## Common Gotchas

- This repo may be run against the wrong DB path; commands often guard against Mixxx DB usage.
- `pipeline --apply` should take one backup at start, not one per step.
- Crate sync to Mixxx reconciles stale auto crates and membership; avoid partial sync logic changes unless fully tested.
- `connect(readonly=True)` skips migrations. Any command that reads a recently added table (e.g., `embeddings`) must open a write connection first even if it writes nothing, to ensure the migration is applied.
- `embed.py` uses `feat.pooler_output[0]` from `model.get_audio_features()` — the method returns `BaseModelOutputWithPooling` in current transformers versions, not a bare tensor. `feat[0]` would return `last_hidden_state` (wrong shape).
- `Vibe/` crates are auto-generated and follow clear-and-rebuild lifecycle. Do not treat them as hand-curated — they will be deleted on next `cluster vibe --apply`.
- CLAP model weights (1.5 GB) live at `~/.cache/huggingface/hub/models--laion--larger_clap_music/` — not in the repo or the MultiDJ DB dir.

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

## Repository Sync Note (2026-06-03)

- **`[mixxx]` config section + `get_mixxx_db_path()` added to `config.py`:** Mixxx DB path can be stored in `~/.multidj/config.toml` under `[mixxx].path`. CLI fallback wired in `pipeline`, `sync mixxx`, `import mixxx`, and `analyze mixxx-blobs` via `args.mixxx_db or get_mixxx_db_path(cfg)`.
- **Pipeline idempotency documented:** All analyze steps skip already-processed tracks (WHERE field IS NULL / LEFT JOIN check). Safe to re-run pipeline daily.
- **Source-of-truth clarified via `sync_state` trigger:** AFTER UPDATE trigger on `tracks` sets `dirty=1`; `full_sync` only pushes `dirty=1 AND deleted=0` tracks.

## Repository Sync Note (2026-06-08)

- **`multidj import directory` now defaults to `music_dir` from config:** When no PATH argument is given, `import directory` falls back to the configured `music_dir` from `~/.multidj/config.toml`. Also removed stale local import that caused `UnboundLocalError` in `sync mixxx`.
- **Album auto-fill from parent directory:** During `import directory`, if a file has no embedded album tag (`album` is NULL/empty), the parent directory name is used as the album value. Album is also included in the `changed` detection so future re-imports pick up directory renames. One-time backfill SQL (`UPDATE tracks SET album = parent_dir WHERE album IS NULL`) brought 1,613 tracks up to date; direct Mixxx UPDATE backfilled 3,238 album values.
- **Auto-dedup on import:** `import directory --apply` now automatically deduplicates tracks by artist+title after scanning. Keeps the best copy (most played → highest rated → largest file), soft-deletes the rest. Pipeline step 5 also runs dedup. One-time `multidj dedupe --apply` removed 468 duplicate tracks.

## Repository Sync Note (2026-05-27)

- Phase 12 (semantic embeddings): `multidj/embed.py` added. `multidj analyze embed --apply [--force] [--limit N]`. CLAP model `laion/larger_clap_music`, 512-dim BLOB storage in new `embeddings` table (migration 004). Requires `uv sync --extra embeddings`.
- Phase 12b (similarity search): `multidj similar <track> [--top N]` — KNN cosine-distance via numpy; read-only.
- Phase 12 clustering: `multidj/cluster.py` added. `multidj cluster vibe --apply [--min-cluster-size N]`. UMAP+HDBSCAN → `Vibe/` auto-crates. LLM naming via `[llm]` config section (optional).
- Pipeline now has 14 steps (embed at position 8, cluster at position 9). Both skip gracefully if extra not installed.
- `Vibe/` prefix added to `AUTO_CRATE_PREFIXES` and `REBUILD_CRATE_RE` in `constants.py`.
- `get_llm_config()` added to `config.py` — reads `[llm].base_url`, `[llm].api_key`, `[llm].model`.
- PoC verified: 35 tracks encoded, 3 clusters found, 4 Vibe/ crates written, similarity search working on CPU.

## Repository Sync Note (2026-06-08b)

- **BeatGrid BLOB fix:** `pack_beatgrid()` in `mixxx_blobs.py` was writing invalid raw double-struct (16 bytes) tagged as `BeatGrid-2.0`, causing Mixxx to log `"Failed to deserialize Beats: Parsing failed"` and re-analyze from scratch. Fixed to produce valid `track::io::BeatGrid` protobuf (proto2 LITE_RUNTIME) using existing hand-rolled encoder. Verified bit-for-bit against real Mixxx-produced BLOBs extracted from the user's own DB (155/142/140 BPM tracks).
- **`multidj import mixxx-analysis` command:** New subcommand reads Mixxx's actual analysis results (`library.bpm`, `library.key`) directly from the Mixxx SQLite DB and imports them into MultiDJ's `tracks` table via path matching (`track_locations.location = tracks.path`). Use cases: (a) bootstrapping ground-truth BPM after running Mixxx GUI analyzer on a large library, (b) training data for NN models. `--apply` required to write, `--force` overwrites existing values, `--limit N` caps count. Dry-run by default. Skips tracks not found in MultiDJ gracefully. See `multidj/import_mixxx_analysis.py`.
- **Headless BPM pipeline now complete:** The three-step flow runs entirely without Mixxx GUI: (1) `multidj import directory --apply` scans filesystem, (2) `multidj analyze bpm --apply` runs librosa beat detection in-venv, (3) `multidj analyze mixxx-blobs --apply` pushes valid BeatGrid BLOBs to Mixxx. With the BeatGrid fix, Mixxx loads these BLOBs without re-analysis — tracks open with BPM, waveform, and beatgrid ready.
- **New tests:** `tests/test_mixxx_blobs.py` (8 tests — proto header, real-Mixxx bit-for-bit matches at 155/142/140 BPM, varint encoding, legacy struct detection, BPM round-trip). `tests/test_import_mixxx_analysis.py` (9 tests — dry-run, apply, force, limit, key import, mode field, missing Mixxx DB). Updated `tests/test_beatgrid.py` (7 tests rewritten from legacy struct to protobuf format). Full suite: 314 passed, 0 failed.
