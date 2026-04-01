# Handoff: layer2f
**Status:** completed
**Timestamp:** 2026-04-01T00:30:00Z

## Completed

### multidj/dedupe.py
- Added `table_exists, ensure_not_empty` to imports
- `_keeper_sort_key`: `timesplayed` → `play_count`
- `_find_groups` (artist-title): removed JOIN, `FROM tracks`, `deleted = 0`, `path AS filepath`, `play_count`
- `_find_groups` (filesize): removed JOIN, `FROM tracks`, `deleted = 0`, `path AS filepath`, `play_count`
- Track dicts in both branches: `timesplayed` key → `play_count`
- `groups_output` keeper/duplicate dicts: `timesplayed` → `play_count`
- `dedupe()`: added wrong-DB guard + `ensure_not_empty` at entry
- `UPDATE library SET mixxx_deleted=1` → `UPDATE tracks SET deleted=1`

### multidj/analyze.py
- Added `table_exists, ensure_not_empty` to imports
- `analyze_key()`: added wrong-DB guard + `ensure_not_empty` at entry
- `candidate_sql`: `FROM tracks`, `path AS filepath`, `deleted = 0`, removed `l.` prefix, removed JOIN
- `count_sql`: `FROM tracks`, `deleted = 0`, removed JOIN
- `where_clause`: removed `l.` prefix from column references
- `UPDATE library SET key=?` → `UPDATE tracks SET key=?`
- `detect_key` and `_write_tag` untouched (pure audio, no schema refs)

### tests/test_dedupe.py (6 tests, all pass)
- `test_dedupe_finds_duplicate_group` — tracks 1 and 4 in same group
- `test_dedupe_keeper_selection` — track 1 wins (play_count=12 vs 1)
- `test_dedupe_dry_run_no_write` — no deleted=1 rows after dry-run
- `test_dedupe_apply_marks_duplicate` — track 4 deleted=1, track 1 deleted=0
- `test_dedupe_no_false_positives` — tracks 2 and 8 not in any group
- `test_dedupe_mode_field` — dry_run / apply

### tests/test_analyze.py (6 tests, all pass)
- `test_analyze_dry_run_returns_candidates` — 5 tracks without key
- `test_analyze_dry_run_no_write` — no keys written
- `test_analyze_force_includes_keyed` — all 9 active tracks when force=True
- `test_analyze_apply_writes_key` — mocked detect_key("11B") written to DB
- `test_analyze_limit` — asserts `result["processed"] == 2` (not total_candidates, which is unaffected by LIMIT)
- `test_analyze_mode_field` — dry_run

## Decisions Made
- `test_analyze_limit` asserts `result["processed"]` not `result["total_candidates"]`: LIMIT only caps the fetched rows, total_candidates is always the full unfiltered count
- `librosa` is lazily imported inside `detect_key()`, so `from multidj.analyze import analyze_key` is safe without librosa; the try/except import guard in test_analyze.py is a belt-and-suspenders precaution
- Wrong-DB guard in `dedupe()` opens a separate guard connection before calling `_find_groups()` (which opens its own connection)

## Test Results
- New tests: 12/12 pass
- Full suite: 73/73 pass (no regressions)

## Remaining
Nothing — task complete.
