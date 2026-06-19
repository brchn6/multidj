# Auto Hot Cues — Design Spec
**Date:** 2026-06-20
**Status:** Phase 1 planned, not yet implemented
**Author:** brainstorming session

---

## Goal

Run a single CLI command and get accurate structural hot cues written into Mixxx for any track — no GUI, no manual work. The result: 8 hot cue slots (Mixxx slots 1–8) populated with intro/verse/build/drop/breakdown/outro markers, ready to play.

---

## Decisions Made

| Decision | Choice | Reason |
|---|---|---|
| Hot cue slots | 8 (Mixxx's full default set, slots 0–7) | Matches Mixxx UI exactly |
| Manual cue protection | Read-before-write: skip any occupied slot | Never destroy manual work |
| Scope | Batch — uncued tracks only | `cues push --apply` finds tracks with no cues yet |
| Pipeline integration | Yes — automatic step | New imports get cues without thinking about it |
| Architecture | Extend in-place (cues.py + mixxx adapter) | MultiDJ DB stays as intermediary, preserves audit trail |

---

## Slot Mapping

| Mixxx slot (UI) | Internal slot | Label | Color | allin1 source |
|---|---|---|---|---|
| 1 | 0 | Intro | Blue | `intro` |
| 2 | 1 | Verse | Yellow | `verse` (first) |
| 3 | 2 | Build | Cyan | `pre-chorus` (first) |
| 4 | 3 | Drop | Red | `chorus` / `instrumental` (first) |
| 5 | 4 | Breakdown | Orange | `breakdown` / `bridge` (first) |
| 6 | 5 | Drop 2 | Red | `chorus` / `instrumental` (second) |
| 7 | 6 | Build 2 | Cyan | `pre-chorus` (second) |
| 8 | 7 | Outro | Green | `outro` |

Missing segments (e.g. no breakdown in a track) leave that slot empty. Occupied slots are never overwritten.

---

## Two-Phase Plan

### Phase 1 — Evaluate (build first)

**Objective:** Determine whether allin1's structural detection is accurate enough on this DJ library before committing to full automation.

**What to build:**
- `multidj cues inspect TRACK` — prints detected cue candidates (type, position mm:ss, confidence) without touching Mixxx. Shows what *would* be written.
- Run `analyze cues` on ~20 representative tracks
- Push a handful to Mixxx via `sync mixxx --apply`, open in Mixxx, listen and evaluate on the waveform

**Success criteria for Phase 1:**
- allin1 correctly identifies intro/drop/outro on >70% of tested tracks
- The "first drop" heuristic (first `chorus` or `instrumental` segment) lands on the actual energy peak
- Breakdowns and verses are roughly where expected

**If Phase 1 fails:** Revisit detection strategy — may need librosa-only energy-based segmentation as fallback, or manual confidence tuning.

### Phase 2 — Automate (build after Phase 1 passes)

**What to build:**

1. **`cues.py`** — extend `detect_cues()` to return all 8 slot types. Add `_pick_segments()` helper: given allin1 segments, extract first/second instance of each type per the slot mapping above.

2. **`multidj/adapters/mixxx.py`** — extend `_push_cues_to_mixxx()`:
   - Expand `_CUE_HOTCUE_SLOTS` and `_CUE_COLORS` from 3 entries to 8
   - Add read-before-write: `SELECT hotcue FROM cues WHERE track_id=?` before each write — skip occupied slots
   - Remove the unconditional `DELETE FROM cues WHERE hotcue IN (0,1,2)` that currently precedes the push

3. **`multidj/cli.py`** — add `cues push` subcommand:
   ```
   multidj cues push [--apply] [--limit N]
   ```
   Finds all active tracks with no rows in `cue_points`, runs `detect_cues()`, stores in DB, then pushes to Mixxx.

4. **`multidj/pipeline.py`** — add as step 11 (after existing cue detection, before crates rebuild). Respects existing `--skip-cues` flag pattern.

---

## Current State (what exists today)

- `analyze cues --apply` — detects intro/drop/outro only (3 types) via allin1 + librosa, stores in `cue_points` with `source='auto'`
- `_push_cues_to_mixxx()` — pushes 3 slots (0=intro blue, 1=drop red, 2=outro green). Unconditionally deletes slots 0–2 before writing — no protection yet.
- `cue_points` schema: `(id, track_id, type, position, label, confidence, source)` — `source='manual'` rows are already protected in the DB layer from being overwritten by `analyze cues`. Mixxx-side slot protection is what Phase 2 adds.
- `sync mixxx --apply` already calls `_push_cues_to_mixxx()` — the push path exists, just needs expansion.

---

## Open Questions (resolve during Phase 1 evaluation)

- What confidence threshold separates useful cues from noise on electronic/DJ music?
- Does allin1 reliably find a "second drop" on tracks with a clear B-section?
- Should Build 2 (slot 6) be omitted if no second pre-chorus is detected, or filled with nearest equivalent?
- Should Drop 2 and Drop share the same color, or differentiate (e.g. Drop 2 = darker red)?

---

## Implementation Notes

- `detect_cues()` already returns all allin1 segment types — Phase 2 extension is a labeling/picking step, not a re-detection step.
- allin1 requires `uv sync --extra embeddings`. Both phases must degrade gracefully if not installed (same pattern as existing embed/cluster steps).
- Dry-run is the default: `cues push` without `--apply` prints what would be written, touches nothing.
