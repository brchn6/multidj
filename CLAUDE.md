# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`MultiDJ` (package: `multidj`) is a Python 3.9+ CLI for DJ music library management. It maintains its own SQLite DB (`~/.multidj/library.sqlite`) as the source of truth and syncs to DJ software (Mixxx first; Rekordbox/Serato as future adapters). All write commands are **dry-run by default**, automatic backups are created before any writes, and JSON output is available for machine consumption. Eventually exposed as an MCP server for agent-native access.

Migration from Mixxx-only tool is **complete** (Phases 0–4). All commands now operate on the MultiDJ DB. Phases 6–7 (standalone ingestion, Mixxx crate sync, pipeline command) are also complete.

## Installation and Running

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install -r requirements-dev.txt
source .venv/bin/activate      # then use `multidj` directly
multidj import mixxx --apply   # one-time: populate MultiDJ DB from Mixxx
multidj pipeline --apply       # daily workflow: import→parse→analyze→crates→sync
multidj <command>              # primary entry point
mixxx-tool <command>           # legacy alias (same binary)
```

Override the DB path: `--db <path>` flag or `MULTIDJ_DB_PATH` environment variable.

All track files live in `/home/barc/Music/All_Tracks/`.

## Commands

| Command | Description |
|---|---|
| `pipeline` | Primary daily workflow: chains all 8 steps; `--apply`, `--skip-<step>`, `--music-dir` |
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
| `clean text` | Artist/title/album text cleanup |
| `analyze bpm` | BPM detection via librosa; `--apply`, `--force`, `--limit` (requires librosa) |
| `analyze key` | Key detection via librosa; `--apply`, `--write-tags`, `--force`, `--limit` (requires librosa) |
| `analyze energy` | Energy score (RMS × centroid, normalized 0–1); `--apply`, `--force`, `--limit` (requires librosa) |
| `crates audit` | Crate inventory and classification |
| `crates hide/show/delete` | Bulk crate management |
| `crates rebuild` | Rebuild all auto-crates (Genre:/BPM:/Key:/Energy:/Lang:) from config; `--apply`, `--min-tracks` |
| `dedupe` | Duplicate detection (artist+title or filesize+duration) |

**Global flags** (accepted anywhere in the command line): `--json`, `--db <path>`, `--version`

**Safety flags on write commands**: `--apply` (required to actually write), `--no-backup`, `--limit <N>`

## Architecture

**Layered design:**

1. **`cli.py`** — argparse entry point; hoists global flags (`--json`, `--db`) from any position in argv; routes to command modules
2. **`db.py`** — `connect(db_path, readonly=True)` context manager; auto-applies SQL migrations on write connections; `resolve_db_path()`, `ensure_db_exists()`, `ensure_not_empty()`, `table_exists()`
3. **`backup.py`** — creates timestamped DB copies before every write; returns `BackupResult`
4. **`utils.py`** — `emit(data, json_mode)` for unified JSON/human output
5. **`constants.py`** — uninformative genre list, crate classifier prefixes, shared regex patterns, `CAMELOT_KEY_MAP`, `KNOWN_ADAPTERS`
6. **`config.py`** — `load_config()`, `save_config()`, `get_music_dir()`; reads/writes `~/.multidj/config.toml`; defaults on first run; preserves unknown sections
7. **`pipeline.py`** — `run_pipeline()`: chains 8 steps, one backup at start, per-step error isolation, respects `skip` set
8. **`models.py`** — `LibrarySummary` dataclass
9. **`adapters/base.py`** — `SyncAdapter` ABC (`import_all`, `push_track`, `full_sync`)
10. **`adapters/mixxx.py`** — `MixxxAdapter`: reads Mixxx DB on import, writes back on sync + crate sync; `_push_crates_to_mixxx()` reconciles stale crates and membership
11. **`adapters/directory.py`** — `DirectoryAdapter`: imports audio files from filesystem paths
12. **Command modules** (`scan`, `audit`, `clean`, `analyze`, `parse`, `enrich`, `crates`, `dedupe`) — pure business logic, read-only unless `--apply` is passed

**Migration system:** SQL files in `multidj/migrations/NNN_name.sql` are auto-applied in numeric order when `connect(readonly=False)` is called. Schema version tracked in `schema_version` table.

**MultiDJ DB schema** (`~/.multidj/library.sqlite`):
- `tracks` — canonical track records (`id`, `path`, `artist`, `title`, `album`, `genre`, `bpm`, `key`, `language`, `duration`, `filesize`, `rating`, `play_count`, `remixer`, `energy`, `intro_end`, `outro_start`, `deleted`, `created_at`, `updated_at`)
- `track_tags` — arbitrary key/value metadata per track
- `crates` — named collections with `type` (`hand-curated` vs auto) and `show` flag
- `crate_tracks` — many-to-many join
- `sync_state` — per-track, per-adapter dirty flag; trigger sets `dirty=1` on any `tracks` update

**Key design invariants:**
- `deleted = 0` filter applied everywhere (soft-deleted tracks excluded from all stats and operations)
- Write operations use `executemany()` for batched DB updates
- All analyze commands isolate per-track errors so one bad audio file doesn't abort the batch
- Crates use a three-tier protection model: catch-all ("New Crate") → auto-generated (`Genre:`/`BPM:`/`Key:`/`Energy:`/`Lang:` prefix) → hand-curated (everything else). Hand-curated crates are protected unless `--include-hand-curated` is passed.
- Duplicates and deleted crate tracks use soft-delete (`deleted=1`), not hard delete
- `pipeline` takes one backup at start; individual steps pass `backup_dir=False` sentinel to suppress per-step backups
- `sync mixxx --apply` reconciles Mixxx crates: stale auto-crates deleted, membership clear-and-repopulated. MultiDJ is the source of truth.
- Config (`~/.multidj/config.toml`) controls which crate dimensions are generated; `pipeline` reads config for music_dir and skips analyze steps if corresponding dimension is disabled

## Tests and Linting

```bash
.venv/bin/pytest tests/ -v           # full suite (132 tests)
.venv/bin/pytest tests/test_scan.py  # single module
```

Fixture DB (10 tracks) is in `tests/fixtures/data.py` — this is the ground truth for all test assertions. `make_mixxx_db()` and `make_multidj_db()` in `tests/fixtures/` build fresh SQLite files from it. Each test gets an isolated DB via `tmp_path`.

No linting config. PEP 8 conventions with type hints throughout.
