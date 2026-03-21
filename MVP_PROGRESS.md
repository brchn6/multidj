# MVP Progress — mixxx_multitool

**Stack:** Python 3.9+, SQLite (stdlib only for core), librosa + mutagen (optional, key analysis)
**Entry point:** `python -m mixxx_tool` or `mixxx-tool` (installed via pip)
**DB default:** `~/.mixxx/mixxxdb.sqlite`
**Last verified:** 2026-03-21

---

## Completed Features

### Infrastructure
- [x] Package structure (`pyproject.toml`, `__init__.py`, `__main__.py`)
- [x] `db.py` — `connect()` (read-only URI + read-write), `resolve_db_path()`, `table_exists()`
- [x] `backup.py` — timestamped `.sqlite` copies to `~/.mixxx/backups/` before every write
- [x] `models.py` — `LibrarySummary` dataclass with `to_dict()`
- [x] `utils.py` — `emit()` for JSON / human-readable output
- [x] `constants.py` — single source of truth: `UNINFORMATIVE_GENRES`, `EMOJI_OR_SYMBOL_RE`, `AUTO_CRATE_PREFIXES`, `CATCH_ALL_CRATE_NAMES`, `CAMELOT_SUFFIX_RE`, `NOISE_PREFIX_RE`, `DUPLICATE_SUFFIX_RE`, `REBUILD_CRATE_RE`
- [x] `cli.py` — argparse with global `--json` / `--db` flag hoisting before subcommand dispatch

### Commands
- [x] `scan` — active track counts (genre, BPM, key, rating), crate count, table list
- [x] `backup` — manual backup with optional `--backup-dir`
- [x] `audit genres` — genre distribution, case collisions, emoji/uninformative detection (`--top N`)
- [x] `audit metadata` — field coverage percentages across active tracks
- [x] `clean genres` — dry-run/apply: case normalization, uninformative → NULL, whitespace (`--limit`, `--no-backup`)
- [x] `clean text` — dry-run/apply: whitespace strip/collapse in artist/title/album
- [x] `analyze key` — Krumhansl-Schmuckler key detection via librosa/chroma; `--apply`, `--write-tags`, `--no-sync-db`, `--force`, `--limit`
- [x] `crates audit` — three-tier classification (catch-all / auto / hand-curated), threshold report, `--summary` mode, `--min-tracks`
- [x] `crates hide` — set `show=0` on small auto crates (reversible), `--include-hand-curated`, `--apply`
- [x] `crates show` — restore hidden crates, optional `--min-tracks` threshold
- [x] `crates delete` — permanent delete of auto crates + their track assignments, `--apply`
- [x] `crates rebuild` — delete all Genre:/BPM:/Lang: crates, recreate from current DB data; `--min-tracks`, `--apply`
- [x] `dedupe` — artist+title and filesize+duration matching; keeper = most-played → highest-rated → largest; `--by`, `--apply`
- [x] `parse` — extract artist/title/remixer/featuring from filenames; confidence tiers (high/medium/low); `--apply`, `--force`, `--min-confidence`, `--limit`
- [x] `enrich language` — detect Hebrew tracks by Unicode range (U+0590–U+05FF, U+FB1D–U+FB4F); read-only report

### Safety Model
- [x] All commands dry-run by default; nothing written without `--apply`
- [x] Auto backup before every write (skip with `--no-backup`)
- [x] `dedupe --apply` and `crates delete --apply` use soft-delete (`mixxx_deleted=1`) — recoverable
- [x] Per-track error isolation in `analyze key` — one bad file does not abort the batch
- [x] All stats exclude soft-deleted tracks (`mixxx_deleted = 0` filter everywhere)
- [x] `track_locations.fs_deleted` correctly ignored (Mixxx internal flag, all rows = 1)

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
| Usable crates (≥5 tracks) | 40 |
| Small auto crates (<5) | 348 |
| Small hand-curated (<5) | 3 |
| Catch-all crates | 1 ("New Crate") |
| Track file location | `/home/barc/Music/All_Tracks/` |

---

## Known Issues / Open Items

| Priority | Item |
|----------|------|
| Low | 1 genre normalization change pending — run `clean genres --apply` to apply |
| Low | 1,627 tracks missing key — run `analyze key --apply` (slow, requires librosa) |
| Low | 348 small auto crates unused — run `crates hide --apply` or `crates delete --apply` to trim |

---

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| `mixxx_deleted = 0` as active filter | Authoritative soft-delete flag on `library` table |
| Ignore `track_locations.fs_deleted` | Mixxx internal rescan flag — all 2030 rows = 1, not a delete marker |
| Batched `executemany()` for DB writes | Prevents N connections per track during `analyze key --apply` |
| `constants.py` as shared truth | Eliminated duplicate `UNINFORMATIVE_GENRES` between `audit.py` and `clean.py` |
| Three crate tiers | catch-all ("New Crate") excluded from threshold analysis; hand-curated protected from bulk ops by default |
| `summary_only` on `crates audit` | Full crate list was 74KB+ JSON; counts-only mode for agent use |
| No test files | Out of MVP scope; deferred intentionally |
| `organize.py`, `score.py` deleted | Were placeholder stubs; removed to eliminate dead code |
| `explore_mixxx_db.py`, `organize_mixxx.py`, `move_tracks.py` deleted | One-off and legacy scripts; work completed, removed from repo |
| All tracks moved to `~/Music/All_Tracks/` | Consolidated from `~/MusicPool/`; `track_locations` updated in DB (2030 rows) |
| Keeper sort: plays → rating → filesize | Deterministic, no user input needed; preserves most-used copy |

---

## Pending / Future

- [ ] `organize` command — move/rename files by metadata pattern
- [ ] `score` command — rate tracks by play count, rating, recency
- [ ] Test suite (pytest + fixture DB)
- [ ] Rating normalization / export

---

## Usage Quick-Reference

```bash
python -m mixxx_tool scan
python -m mixxx_tool audit genres --top 20
python -m mixxx_tool audit metadata --json
python -m mixxx_tool clean genres              # dry-run
python -m mixxx_tool clean genres --apply
python -m mixxx_tool parse                     # dry-run: propose artist/title from filenames
python -m mixxx_tool parse --apply             # write changes
python -m mixxx_tool enrich language           # report Hebrew tracks
python -m mixxx_tool analyze key --limit 5    # dry-run
python -m mixxx_tool analyze key --apply      # tag all unkeyed tracks
python -m mixxx_tool crates audit --summary
python -m mixxx_tool crates rebuild            # dry-run: preview Genre:/Lang: crates
python -m mixxx_tool crates rebuild --apply   # regenerate all auto-crates
python -m mixxx_tool crates hide --apply
python -m mixxx_tool dedupe
python -m mixxx_tool dedupe --apply
```
