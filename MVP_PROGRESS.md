# MVP Progress — multidj

**Stack:** Python 3.9+, SQLite (stdlib only for core), librosa + mutagen (optional, key analysis)
**Entry point:** `multidj` (primary), `mixxx-tool` (legacy alias)
**DB default:** `~/.multidj/library.sqlite`
**Last verified:** 2026-04-01

---

## Completed Features

### Infrastructure
- [x] Package structure (`pyproject.toml`, `__init__.py`, `__main__.py`)
- [x] `db.py` — `connect()` (read-only URI + read-write), `resolve_db_path()`, `ensure_not_empty()`, migration runner (`_apply_migrations()`)
- [x] `backup.py` — timestamped `.sqlite` copies to `~/.multidj/backups/` before every write
- [x] `models.py` — `LibrarySummary` dataclass with `to_dict()`
- [x] `utils.py` — `emit()` for JSON / human-readable output
- [x] `constants.py` — `UNINFORMATIVE_GENRES`, `EMOJI_OR_SYMBOL_RE`, `AUTO_CRATE_PREFIXES`, `CATCH_ALL_CRATE_NAMES`, `CAMELOT_SUFFIX_RE`, `NOISE_PREFIX_RE`, `DUPLICATE_SUFFIX_RE`, `REBUILD_CRATE_RE`, `KNOWN_ADAPTERS`
- [x] `cli.py` — argparse with global `--json` / `--db` flag hoisting; all subcommands wired
- [x] `adapters/base.py` — `SyncAdapter` ABC (`import_all`, `push_track`, `full_sync`)
- [x] `adapters/mixxx.py` — `MixxxAdapter` with full import + sync implementation
- [x] `migrations/001_initial.sql` — MultiDJ schema v1 (`tracks`, `track_tags`, `crates`, `crate_tracks`, `sync_state`, `schema_version`)

### Commands
- [x] `import mixxx` — one-time pull from `~/.mixxx/mixxxdb.sqlite`; dry-run + apply; `timesplayed→play_count`; key lookup via Mixxx `keys` table; sets `sync_state.dirty=0` for all imported tracks
- [x] `sync mixxx` — push dirty tracks back to Mixxx; dry-run + apply; backs up Mixxx DB first; marks `dirty=0` on success
- [x] `scan` — active track counts (genre, BPM, key, rating), crate count, optional table list
- [x] `backup` — manual backup with optional `--backup-dir`
- [x] `audit genres` — genre distribution, case collisions, emoji/uninformative detection (`--top N`)
- [x] `audit metadata` — field coverage percentages across active tracks
- [x] `clean genres` — dry-run/apply: case normalization, uninformative → NULL, whitespace (`--limit`, `--no-backup`)
- [x] `clean text` — dry-run/apply: whitespace strip/collapse in artist/title/album
- [x] `analyze key` — Krumhansl-Schmuckler key detection via librosa/chroma; `--apply`, `--write-tags`, `--no-sync-db`, `--force`, `--limit`
- [x] `crates audit` — three-tier classification (catch-all / auto / hand-curated), threshold report, `--summary`, `--min-tracks`
- [x] `crates hide` — set `show=0` on small auto crates (reversible), `--include-hand-curated`, `--apply`
- [x] `crates show` — restore hidden crates, optional `--min-tracks` threshold
- [x] `crates delete` — permanent delete of auto crates + their track assignments, `--apply`
- [x] `crates rebuild` — delete all Genre:/Lang: auto-crates, recreate from current DB data; `--min-tracks`, `--apply`
- [x] `dedupe` — artist+title and filesize+duration matching; keeper = most-played → highest-rated → largest; `--by`, `--apply`
- [x] `parse` — extract artist/title/remixer/featuring from filenames; confidence tiers (high/medium/low); `--apply`, `--force`, `--min-confidence`, `--limit`
- [x] `enrich language` — detect Hebrew tracks by Unicode range (U+0590–U+05FF, U+FB1D–U+FB4F); read-only report

### Test Suite
- [x] `tests/fixtures/data.py` — 10-track canonical fixture (9 active + 1 deleted) covering duplicates, Hebrew, case collisions, whitespace genres, uninformative genres, missing metadata
- [x] `tests/fixtures/mixxx_factory.py` — builds Mixxx-schema SQLite from fixture data
- [x] `tests/fixtures/multidj_factory.py` — builds MultiDJ-schema SQLite in post-import state
- [x] `tests/conftest.py` — `mixxx_db`, `multidj_db`, `multidj_db_conn` pytest fixtures
- [x] `tests/test_safety.py` — cross-cutting invariants: dry-run never writes, apply writes, backup created
- [x] `tests/test_import.py` — 12 tests (count, field mapping, play_count, sync_state, idempotency, key lookup)
- [x] `tests/test_sync.py` — 6 tests (dry-run, apply pushes data, marks clean, skips clean, dirty trigger)
- [x] `tests/test_scan.py`, `test_audit.py`, `test_enrich.py`, `test_parse.py`, `test_clean.py`, `test_crates.py`, `test_dedupe.py`, `test_analyze.py`
- [x] **92 tests passing**

### Safety Model
- [x] All commands dry-run by default; nothing written without `--apply`
- [x] Auto backup before every write (skip with `--no-backup`)
- [x] `dedupe --apply` and `crates delete --apply` use soft-delete (`deleted=1`) — recoverable
- [x] Per-track error isolation in `analyze key` — one bad file does not abort the batch
- [x] All stats exclude soft-deleted tracks (`deleted = 0` filter everywhere)
- [x] Wrong-DB guard in every command: clear error if pointed at a Mixxx DB instead of MultiDJ DB
- [x] `sync mixxx --apply` backs up Mixxx DB before writing

---

## Migration Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Package rename `mixxx_tool` → `multidj` | **Done** |
| 1 | New DB layer: `~/.multidj/library.sqlite`, migration runner, schema v1 | **Done** |
| 2 | `import mixxx` — one-time pull from Mixxx into MultiDJ DB | **Done** |
| 3 | Port all commands to MultiDJ schema (`tracks` table, `deleted` column) | **Done** |
| 4 | `sync mixxx` — push dirty tracks back to Mixxx | **Done** |
| 5 | Remove `mixxx-tool` alias once transition confirmed | Deferred |

---

## Current Library Snapshot (2026-03-21)

| Metric | Value |
|--------|-------|
| Active tracks | 1,844 |
| Tracks with BPM | 1,844 (100%) |
| Tracks with genre | 1,199 (65%) |
| Tracks with key | 217 (12%) |
| Tracks with rating | 3 |
| Total crates | 392 |
| Track file location | `/home/barc/Music/All_Tracks/` |

---

## Known Issues / Open Items

| Priority | Item |
|----------|------|
| High | Run `multidj import mixxx --apply` to populate the MultiDJ DB from Mixxx |
| Low | 1,627 tracks missing key — run `multidj analyze key --apply` after import |
| Low | Genre normalization pending — run `multidj clean genres --apply` after import |

---

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| MultiDJ owns `~/.multidj/library.sqlite` | Software-agnostic source of truth; DJ apps are sync targets |
| `sync_state` dirty-flag trigger | Any `UPDATE tracks` automatically marks affected adapter rows dirty — sync is always aware |
| `INSERT OR REPLACE` on import | Idempotent import; running twice produces same result |
| `PRAGMA table_info(keys)` before key lookup | Mixxx's keys table column name is not guaranteed — defensive detection |
| Wrong-DB guard in every command | Clear error when user accidentally points at Mixxx DB before running import |
| Fixture DB with 10 canonical tracks | Ground truth for all tests; covers every edge case the commands handle |
| `backup_dir` param on write commands | Required for test isolation — tests can't write to `~/.multidj/backups/` |
| Keeper sort: plays → rating → filesize | Deterministic, no user input needed; preserves most-used copy |
| `timesplayed` → `play_count` | MultiDJ uses standard naming; Mixxx quirk hidden behind adapter |

---

## Pending / Future

- [ ] `import directory <path>` — scan filesystem, add tracks via mutagen tag read (Phase 2b)
- [ ] `multidj mcp` — MCP server for agent-native calls (`scan_library`, `search_tracks`, `update_track`, `sync_adapter`, etc.)
- [ ] `organize` command — move/rename files by metadata pattern
- [ ] `score` command — rate tracks by play count, rating, recency
- [ ] Rekordbox and Serato sync adapters
- [ ] Quality evaluation layer — parse accuracy %, dedupe precision on real library
- [ ] Test suite CI integration

---

## Usage Quick-Reference

```bash
# Bootstrap
multidj import mixxx --apply

# Daily workflow
multidj scan
multidj audit genres
multidj clean genres --apply
multidj parse --apply
multidj crates rebuild --apply
multidj sync mixxx --apply

# One-off operations
multidj dedupe
multidj dedupe --apply
multidj analyze key --apply    # slow, requires librosa
multidj enrich language

# Testing
pytest tests/ -v
```
