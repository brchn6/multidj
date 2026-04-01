# Layer 2D Handoff — parse.py + clean.py MultiDJ Schema Port

## Status: COMPLETE

All 67 tests pass (`pytest tests/ -v`).

## Changes Made

### multidj/parse.py
- Added `table_exists, ensure_not_empty` to import from `.db`
- Changed `_ACTIVE` from `"mixxx_deleted = 0"` to `"deleted = 0"`
- Replaced Mixxx JOIN query with MultiDJ `FROM tracks` query:
  - Removed `JOIN track_locations tl ON l.location = tl.id`
  - `l.id, l.artist, l.title, tl.location AS filepath` → `id, artist, title, path AS filepath`
  - `FROM library l WHERE l.{_ACTIVE}` → `FROM tracks WHERE {_ACTIVE}`
- Added wrong-DB guard + `ensure_not_empty(conn)` at top of `parse_library()`
- `UPDATE library SET artist/title` → `UPDATE tracks SET artist/title`

### multidj/clean.py
- Added `table_exists, ensure_not_empty` to import from `.db`
- Changed `_ACTIVE` from `"mixxx_deleted = 0"` to `"deleted = 0"`
- `clean_genres()`:
  - Added `backup_dir: str | None = None` parameter (needed for testable backup)
  - `FROM library WHERE {_ACTIVE}` → `FROM tracks WHERE {_ACTIVE}`
  - `UPDATE library SET genre` → `UPDATE tracks SET genre`
  - Added wrong-DB guard + `ensure_not_empty(conn)`
  - Now returns `backup_path` in result dict when backup is created
- `clean_text()`:
  - `FROM library WHERE {_ACTIVE}` / `UPDATE library SET {field}` → tracks equivalents
  - Added wrong-DB guard + `ensure_not_empty(conn)`

### tests/test_parse.py (new)
8 tests covering: pure `parse_filename()`, skip-already-tagged, propose-untagged,
dry-run immutability, apply-writes, force-overwrites, mode field.

Key insight: All fixture track filenames use underscores (e.g.,
`03_DJ_Tiesto_-_Red_Lights_Remix.mp3`), which `parse_filename()` classifies as
`confidence="low"` (underscore format). Tests that need to include track 3 or
force-include track 1 must pass `min_confidence="low"`.

### tests/test_clean.py (new)
11 tests covering: uninformative/case_variant/whitespace detection, dry-run
immutability, apply effects on tracks 4/6/7, idempotency, clean_text dry-run,
mode field.

### tests/test_safety.py
Removed `@pytest.mark.skip` from all three `clean_genres` safety tests:
- `test_clean_genres_dry_run_does_not_write`
- `test_clean_genres_apply_writes`
- `test_backup_created_before_write` — also updated to pass `backup_dir=str(tmp_path)`
  so backup writes to tmp dir, not `~/.multidj/backups/`

## Test Results
- `pytest tests/test_parse.py tests/test_clean.py tests/test_safety.py -v`: 32 passed
- `pytest tests/ -v`: 67 passed, 0 failed, 0 skipped
