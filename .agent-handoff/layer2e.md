# Layer 2E Handoff — crates.py Port + Tests

**Status: COMPLETE**
**Date: 2026-04-01**

## What Was Done

### 1. Merged `dev` into worktree branch
The worktree started at the original `mixxx_tool` commit (5269f5d). Merged `dev` (09b57b3) via fast-forward to get all prior agent work including the `multidj` package, `multidj/db.py`, test fixtures, and scaffold.

### 2. Ported `multidj/crates.py` to MultiDJ schema

All changes made to `/home/barc/dev/multidj/.claude/worktrees/agent-aba5b069/multidj/crates.py`:

**`_fetch_crates(conn)`**
- Removed `c.locked` and `c.autodj_source` from SELECT
- Added `c.type` to SELECT (stored in DB, not computed)
- Removed `locked` and `autodj_source` keys from returned dict
- `type` now comes from the DB column directly (not via `_classify()`)

**`hide_crates()`**
- Removed `not c["locked"]` filter condition
- Added wrong-DB guard + `ensure_not_empty` at top of function

**`delete_crates()`**
- Removed `not c["locked"]` filter condition
- Added wrong-DB guard + `ensure_not_empty` at top of function

**`show_crates()`**
- Added wrong-DB guard + `ensure_not_empty` at top of function

**`audit_crates()`**
- Added wrong-DB guard + `ensure_not_empty` at top of function (inside the `with connect()` block)

**`rebuild_crates()`**
- Added wrong-DB guard + `ensure_not_empty` at top of function
- Changed all `FROM library WHERE mixxx_deleted = 0` → `FROM tracks WHERE deleted = 0`
- Changed INSERT: `INSERT INTO crates (name, show, locked, autodj_source) VALUES (?, 1, 0, 0)` → `INSERT INTO crates (name, type, show) VALUES (?, 'auto', 1)`

**Unchanged:**
- `_classify()` function — untouched (still used for guard logic, though type is now DB-stored)
- Import of `is_hebrew` from `enrich` — unchanged
- All return dict shapes (minus `locked`/`autodj_source` which were removed)

### 3. Created `tests/test_crates.py`

13 tests covering:
- `audit_crates`: classification by type, catch-all count, total crate count
- `hide_crates`: hides auto crates below threshold, protects hand-curated, dry-run no-op
- `show_crates`: restores previously hidden crates
- `rebuild_crates`: creates Genre: crate, creates Lang: Hebrew crate, deletes/re-creates old auto crates, idempotent
- `delete_crates`: deletes auto crates below threshold, protects hand-curated

## Test Results

- `pytest tests/test_crates.py -v`: **13 passed**
- `pytest tests/ -v`: **36 passed** (23 pre-existing + 13 new, zero regressions)

## Key Implementation Notes

- The `ensure_not_empty` guard is placed inside the first `with connect(readonly=True)` block in each public function — this avoids opening two connections unnecessarily.
- In `rebuild_crates`, the second `readonly=True` block (for skipped genres) does not repeat the guard since the first block already validated the DB.
- The `connect(readonly=False)` path goes through `_apply_migrations` which uses `CREATE TABLE IF NOT EXISTS` — fully idempotent against fixture DBs.
- `_classify()` is preserved as-is; it's used in the wrong-DB guard logic and remains useful for computing types on the fly if needed.

## Files Modified

- `/home/barc/dev/multidj/.claude/worktrees/agent-aba5b069/multidj/crates.py` — ported to MultiDJ schema
- `/home/barc/dev/multidj/.claude/worktrees/agent-aba5b069/tests/test_crates.py` — created (new)
- `/home/barc/dev/multidj/.claude/worktrees/agent-aba5b069/.agent-handoff/layer2e.md` — this file
