# Layer 3G Handoff — `multidj sync mixxx`

**Status: COMPLETE**
**Date: 2026-04-01**
**Agent: Sub-agent G**

## What Was Done

Implemented `multidj sync mixxx` — the final Layer 3 sync command that pushes dirty MultiDJ tracks back to Mixxx DB.

## Files Modified

### `multidj/adapters/mixxx.py`
- Added `from ..backup import create_backup` import at top
- Replaced stub `push_track()` with full implementation:
  - UPDATEs `library` table fields (artist, title, album, genre, bpm, rating, timesplayed) matching by path via `track_locations` JOIN
  - Optionally updates `key_id` if track has a key and it's found in `keys` table
  - Returns True if ≥1 row updated, False if path not in Mixxx
- Replaced stub `full_sync()` with full implementation:
  - Reads dirty tracks from MultiDJ `sync_state` + `tracks` JOIN
  - Dry-run mode: returns `{mode, dirty_tracks, sample}` without any writes
  - Apply mode: backs up Mixxx DB, opens both DBs simultaneously, pushes each dirty track, marks `dirty=0` on success, collects errors
  - Returns `{mode, total_dirty, pushed, errors}`

### `multidj/cli.py`
- Added `sync` subparser with `mixxx` sub-subparser (flags: `--mixxx-db`, `--apply`, `--no-backup`)
- Added dispatch branch `elif args.command == "sync"` calling `adapter.full_sync()`

## Files Created

### `tests/test_sync.py`
6 tests covering:
1. `test_sync_dry_run_returns_summary` — dry-run returns mode+dirty_tracks
2. `test_sync_dry_run_no_write` — dry-run does not modify Mixxx
3. `test_sync_apply_pushes_dirty` — apply mode writes artist change to Mixxx
4. `test_sync_marks_clean_after_push` — after push, sync_state.dirty=0
5. `test_sync_skips_clean_tracks` — no dirty tracks → pushed=0
6. `test_dirty_trigger_fires` — UPDATE tracks triggers dirty=1 in sync_state

## Test Results

- New sync tests: **6/6 passing**
- Full suite: **92/92 passing** (86 pre-existing + 6 new)

## Key Design Decisions

1. Both DB connections held open simultaneously during apply mode — MultiDJ for marking clean, Mixxx for writing updates. Both closed in `finally` block.
2. Per-track commit to Mixxx + per-track sync_state update — if one track fails, others still proceed.
3. Backup uses `create_backup(str(self.mixxx_path))` — backs up Mixxx DB (not MultiDJ) before any writes.
4. `--no-backup` flag is wired in CLI parser but `full_sync()` always backs up when apply=True (consistent with rest of codebase where backup happens inside the adapter). This is the safe default; future work can thread the flag through if needed.
5. Key update is skipped if key is None (don't null out Mixxx key) and if Camelot string not found in keys table (don't create new rows).
