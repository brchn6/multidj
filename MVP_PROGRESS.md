# MVP Progress — multidj

**Stack:** Python 3.9+, SQLite (stdlib only for core), librosa + mutagen (optional, key analysis)
**Entry point:** `multidj` (primary), `mixxx-tool` (legacy alias)
**DB default:** `~/.multidj/library.sqlite`
**Last verified:** 2026-04-22

---

## Completed Features

### Infrastructure
- [x] Package structure (`pyproject.toml`, `__init__.py`, `__main__.py`)
- [x] `db.py` — `connect()` (read-only URI + read-write), `resolve_db_path()`, `ensure_not_empty()`, migration runner (`_apply_migrations()`)
- [x] `backup.py` — timestamped `.sqlite` copies to `~/.multidj/backups/` before every write
- [x] `models.py` — `LibrarySummary` dataclass with `to_dict()`
- [x] `utils.py` — `emit()` for JSON / human-readable output
- [x] `constants.py` — `UNINFORMATIVE_GENRES`, `EMOJI_OR_SYMBOL_RE`, `AUTO_CRATE_PREFIXES`, `CATCH_ALL_CRATE_NAMES`, `CAMELOT_SUFFIX_RE`, `NOISE_PREFIX_RE`, `DUPLICATE_SUFFIX_RE`, `REBUILD_CRATE_RE`, `CAMELOT_KEY_MAP`, `KNOWN_ADAPTERS`
- [x] `config.py` — `load_config()`, `save_config()`, `get_music_dir()`; `~/.multidj/config.toml`; defaults written on first run; unknown sections preserved
- [x] `cli.py` — argparse with global `--json` / `--db` flag hoisting; all subcommands wired
- [x] `adapters/base.py` — `SyncAdapter` ABC (`import_all`, `push_track`, `full_sync`)
- [x] `adapters/mixxx.py` — `MixxxAdapter` with full import + sync + crate sync implementation
- [x] `adapters/directory.py` — `DirectoryAdapter`: import tracks from raw filesystem paths
- [x] `pipeline.py` — `run_pipeline()`: chains all 8 steps, one backup at start, step isolation, `--skip-*` flags
- [x] `migrations/001_initial.sql` — MultiDJ schema v1 (`tracks`, `track_tags`, `crates`, `crate_tracks`, `sync_state`, `schema_version`)

### Commands
- [x] `import mixxx` — one-time pull from `~/.mixxx/mixxxdb.sqlite`; dry-run + apply; `timesplayed→play_count`; key lookup via Mixxx `keys` table; sets `sync_state.dirty=0` for all imported tracks
- [x] `sync mixxx` — push dirty tracks back to Mixxx; dry-run + apply; backs up Mixxx DB first; marks `dirty=0` on success
- [x] `scan` — active track counts (genre, BPM, key, rating), crate count, optional table list
- [x] `backup` — manual backup with optional `--backup-dir`
- [x] `audit genres` — genre distribution, case collisions, emoji/uninformative detection (`--top N`)
- [x] `audit metadata` — field coverage percentages across active tracks
- [x] `clean genres` — dry-run/apply: case normalization, uninformative → NULL, whitespace (`--limit`, `--no-backup`)
- [x] `clean text` — dry-run/apply: whitespace strip/collapse + mapped trailing garbage cleanup in title/artist
- [x] `import directory` — scan a directory for audio files and import into MultiDJ DB; `--apply`, `--no-backup`
- [x] `analyze bpm` — librosa beat tracking sampled across start/middle/end windows; reports variable-BPM tracks; `--apply`, `--force`, `--limit`, `--no-backup`; per-track error isolation
- [x] `analyze key` — Krumhansl-Schmuckler key detection via librosa/chroma; `--apply`, `--write-tags`, `--no-sync-db`, `--force`, `--limit`
- [x] `analyze energy` — librosa RMS × spectral centroid, min-max normalized 0–1; `--apply`, `--force`, `--limit`, `--no-backup`
- [x] `pipeline` — chains import→parse→bpm→key→energy→clean genres→crates rebuild→sync; `--apply`, `--skip-*` per step, `--music-dir`
- [x] `crates audit` — three-tier classification (catch-all / auto / hand-curated), threshold report, `--summary`, `--min-tracks`
- [x] `crates hide` — set `show=0` on small auto crates (reversible), `--include-hand-curated`, `--apply`
- [x] `crates show` — restore hidden crates, optional `--min-tracks` threshold
- [x] `crates delete` — permanent delete of auto crates + their track assignments, `--apply`
- [x] `crates rebuild` — delete all auto-crates, recreate from current DB; config-driven dimensions: Genre:/Lang:/BPM:/Key:/Energy:; `--min-tracks`, `--apply`
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
- [x] `tests/test_import_directory.py` — directory adapter import tests
- [x] `tests/test_sync.py` — 6 tests (dry-run, apply pushes data, marks clean, skips clean, dirty trigger)
- [x] `tests/test_scan.py`, `test_audit.py`, `test_enrich.py`, `test_parse.py`, `test_clean.py`, `test_crates.py`, `test_dedupe.py`, `test_analyze.py`
- [x] `tests/test_analyze_energy.py` — 6 tests (storage, --force, per-track error isolation, normalization)
- [x] `tests/test_config.py` — 7 tests (defaults created, load/save, toggles, unknown section preservation)
- [x] `tests/test_mixxx_crate_sync.py` — 4 tests (crates pushed, stale deletion, membership reconciliation)
- [x] `tests/test_pipeline.py` — 5 tests (dry-run, apply, --skip-*, single backup, step isolation)
- [x] **132 tests passing**

### Safety Model
- [x] All commands dry-run by default; nothing written without `--apply`
- [x] Auto backup before every write (skip with `--no-backup`)
- [x] `dedupe --apply` and `crates delete --apply` use soft-delete (`deleted=1`) — recoverable
- [x] Per-track error isolation in all analyze commands — one bad file does not abort the batch
- [x] All stats exclude soft-deleted tracks (`deleted = 0` filter everywhere)
- [x] Wrong-DB guard in every command: clear error if pointed at a Mixxx DB instead of MultiDJ DB
- [x] `sync mixxx --apply` backs up Mixxx DB before writing

---

## Roadmap Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Package rename `mixxx_tool` → `multidj` | **Done** |
| 1 | New DB layer: `~/.multidj/library.sqlite`, migration runner, schema v1 | **Done** |
| 2 | `import mixxx` — one-time pull from Mixxx into MultiDJ DB | **Done** |
| 3 | Port all commands to MultiDJ schema (`tracks` table, `deleted` column) | **Done** |
| 4 | `sync mixxx` — push dirty tracks back to Mixxx | **Done** |
| 5 | Remove `mixxx-tool` alias once transition confirmed | Deferred |
| 6 | **Standalone ingestion** — `import directory`, `analyze bpm/energy`, config-driven crates | **Done** |
| 7 | **Mixxx crate sync** — push crates + track assignments back to Mixxx | **Done** |
| 8 | **Fingerprint enrichment** — pyacoustid → AcoustID → artist/title/genre for unknowns | Planned |
| 9 | **Cue point detection** — librosa energy analysis → intro/drop/outro markers in DB | Planned |
| 10 | **Mixxx cue sync** — write cue points to Mixxx `cues` table | Planned |
| 11 | **MCP server** — expose all commands as agent-callable tools | Planned |

---

## BPM Range Definitions

These are the canonical BPM ranges used for auto-crate generation:

| Crate Name | BPM Range | Genres |
|---|---|---|
| `BPM:<90` | 0 – 89 | Downtempo / Hip-Hop / Chill |
| `BPM:90-105` | 90 – 104 | Dancehall / Reggae / Afro |
| `BPM:105-115` | 105 – 114 | Midtempo / Indie Dance / Nu Disco |
| `BPM:115-125` | 115 – 124 | House / Deep House / Disco House |
| `BPM:125-130` | 125 – 129 | Tech House / Progressive |
| `BPM:128-135` | 128 – 134 | Techno (low–mid energy) |
| `BPM:135-160` | 135 – 159 | Techno (peak) / Hard Dance / Trance |
| `BPM:160-175` | 160 – 174 | Drum & Bass / Jungle |
| `BPM:175+` | 175+ | Hardcore / Gabber |

Note: 125–130 and 128–135 intentionally overlap — tracks at 128–130 BPM appear in both Tech House and Techno crates.

---

## Current Library Snapshot (2026-04-06)

| Metric | Value |
|--------|-------|
| Active tracks | 1,835 |
| Tracks with BPM | 1,835 (100%) |
| Tracks with genre | 1,192 (65%) |
| Tracks with key | 0 (0% — needs `analyze key --apply`) |
| Tracks with rating | 5 |
| Total crates | 0 (crate sync not yet implemented) |
| Track file location | `/home/barc/Music/All_Tracks/` |

---

## Known Issues / Open Items

| Priority | Item |
|----------|------|
| Medium | Tracks missing key — run `multidj analyze key --apply` (needs `pip install librosa`) |
| Medium | Genre normalization pending — run `multidj clean genres --apply` |
| Medium | Energy normalization is library-relative: single-track batch gets `energy=0.5` (lo==hi case) |
| Low | `mixxx-tool` legacy alias still active |
| Low | Crates created directly in Mixxx are overwritten on next `sync mixxx --apply` |

---

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| MultiDJ owns `~/.multidj/library.sqlite` | Software-agnostic source of truth; DJ apps are sync targets |
| Mixxx is the primary sync target | Open-source, cross-platform, extensible — gold standard for open-source DJs |
| `sync_state` dirty-flag trigger | Any `UPDATE tracks` automatically marks affected adapter rows dirty — sync is always aware |
| `INSERT OR REPLACE` on import | Idempotent import; running twice produces same result |
| `PRAGMA table_info(keys)` before key lookup | Mixxx's keys table column name is not guaranteed — defensive detection |
| Wrong-DB guard in every command | Clear error when user accidentally points at Mixxx DB before running import |
| Fixture DB with 10 canonical tracks | Ground truth for all tests; covers every edge case the commands handle |
| `backup_dir` param on write commands | Required for test isolation — tests can't write to `~/.multidj/backups/` |
| Keeper sort: plays → rating → filesize | Deterministic, no user input needed; preserves most-used copy |
| `timesplayed` → `play_count` | MultiDJ uses standard naming; Mixxx quirk hidden behind adapter |
| BPM ranges 125–130 and 128–135 overlap | Tracks in the overlap belong in both Tech House and Techno crates by genre convention |
| librosa for BPM + key, pyacoustid for fingerprint | Same libraries beets uses — battle-tested, no external binary required for core analysis |

---

## Usage Quick-Reference

```bash
# Bootstrap (from Mixxx)
multidj import mixxx --apply

# Bootstrap (from raw files)
multidj import directory ~/Music/All_Tracks --apply

# Primary daily workflow (chains all 8 steps)
multidj pipeline --apply
multidj pipeline --apply --skip-bpm --skip-key   # skip slow analysis

# Individual steps
multidj scan
multidj audit genres
multidj clean genres --apply
multidj parse --apply
multidj analyze bpm --apply        # detect BPM from audio (requires librosa)
multidj analyze key --apply        # detect key from audio (requires librosa)
multidj analyze energy --apply     # detect energy score (requires librosa)
multidj crates rebuild --apply     # genre + BPM + key + energy + language crates
multidj sync mixxx --apply         # tracks + crates → Mixxx

# One-off operations
multidj dedupe
multidj dedupe --apply
multidj enrich language

# Testing
.venv/bin/pytest tests/ -v   # 132 tests
```

## Repository Sync Note (2026-04-30)

- Clean text behavior now strips promotional noise markers from artist/title tails, including free, dl, and download variants.
- BPM analysis now samples start/middle/end windows and reports variable-tempo cases instead of hiding half/double-time ambiguity.
- Directory import now includes artist-title swap mismatch detection for stronger metadata hygiene during ingestion.
- Directory import now soft-deletes (`deleted=1`) tracks whose files no longer exist on disk after a rescan.
- Pipeline expanded to 10 steps: `fix_mismatches` (step 2) auto-corrects artist/title swaps across all active tracks; `clean_text` (step 8) strips promo markers from artist/title/album.
- Added persistent DB path config: `multidj config set-db <path>` stores `[db].path`, and commands now use it when `--db` is omitted.
- Parse now skips junk artist/title proposals (numeric-only and `free`/`dl`/`download` marker values) to reduce bad suggestions in common use.
