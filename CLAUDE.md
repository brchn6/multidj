# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`mixxx_multitool` is a Python 3.9+ CLI for batch tag management on Mixxx music libraries. It operates on a SQLite database (`~/.mixxx/mixxxdb.sqlite`) and is designed for agent use: all write commands are **dry-run by default**, automatic backups are created before any writes, and JSON output is available for machine consumption.

## Installation and Running

```bash
pip install -e .
python -m mixxx_tool <command>   # or: mixxx-tool <command> after install
```

Override the DB path: `--db <path>` flag or `MIXXX_DB_PATH` environment variable.

All track files live in `/home/barc/Music/All_Tracks/` (consolidated from `~/MusicPool/` on 2026-03-21).

## Commands

| Command | Description |
|---|---|
| `scan` | Library statistics (track counts, metadata coverage) |
| `backup` | Manual backup |
| `parse` | Propose artist/title/remixer from filenames; `--apply` to write, `--min-confidence`, `--force` |
| `enrich language` | Report Hebrew tracks detected via Unicode range check (read-only) |
| `audit genres` | Genre distribution, collisions, suspicious values |
| `audit metadata` | Field coverage report |
| `clean genres` | Genre normalization (case, uninformative removal, whitespace) |
| `clean text` | Artist/title/album text cleanup |
| `analyze key` | Key detection via librosa (requires `pip install librosa mutagen`) |
| `crates audit` | Crate inventory and classification |
| `crates hide/show/delete` | Bulk crate management |
| `crates rebuild` | Delete all Genre:/BPM:/Lang: crates, recreate from current DB data |
| `dedupe` | Duplicate detection (artist+title or filesize+duration) |

**Global flags** (accepted anywhere in the command line): `--json`, `--db <path>`, `--version`

**Safety flags on write commands**: `--apply` (required to actually write), `--no-backup`, `--limit <N>`

## Architecture

**Layered design:**

1. **`cli.py`** — argparse entry point; hoists global flags (`--json`, `--db`) from any position in argv; routes to command modules
2. **`db.py`** — `connect()` context manager, `resolve_db_path()`, `ensure_db_exists()`, `table_exists()`
3. **`backup.py`** — creates timestamped copies in `~/.mixxx/backups/` before every write; returns `BackupResult`
4. **`utils.py`** — `emit(data, json_mode)` for unified JSON/human output
5. **`constants.py`** — uninformative genre list, crate classifier prefixes, shared regex patterns
6. **Command modules** (`scan`, `audit`, `clean`, `analyze`, `parse`, `enrich`, `crates`, `dedupe`) — pure business logic, read-only unless `--apply` is passed

**Key design invariants:**
- `mixxx_deleted = 0` filter applied everywhere (soft-deleted tracks excluded from all stats and operations)
- Write operations use `executemany()` for batched DB updates
- `analyze.py` isolates per-track errors so one bad audio file doesn't abort the batch
- Crates use a three-tier protection model: catch-all ("New Crate") → auto-generated (`Genre:`/`BPM:` prefix) → hand-curated (everything else). Hand-curated crates are protected unless `--include-hand-curated` is passed.
- Duplicates and deleted crate tracks use soft-delete (`mixxx_deleted=1`), not hard delete

**Mixxx DB tables used:** `library` (tracks), `track_locations` (file paths), `crates`, `crate_tracks`

## Tests and Linting

No automated tests exist (intentionally deferred; planned as `pytest` with a fixture DB). No linting config is set up. The codebase follows PEP 8 conventions with type hints throughout.
