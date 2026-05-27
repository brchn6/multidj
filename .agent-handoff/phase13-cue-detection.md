# Phase 13 Handoff — Automatic Cue Detection

**Status: PLANNED — ready for implementation**
**Date: 2026-05-28**
**Branch to create: `feat/cue-detection` from `dev`**

---

## What This Phase Builds

Automatically detect musical structure (intro, verse, chorus, drop, outro, etc.) for every track and store as cue points in the `cue_points` table. Push the three DJ-critical cues — intro, drop, outro — to Mixxx as hot cues (slots 0/1/2). This runs as pipeline step 8 (after `energy`, before `embed`).

---

## Current State (what's already done)

- **Spec written:** `docs/superpowers/specs/2026-05-28-cue-detection-design.md` on `feat/embeddings-clustering` branch
  - Access via: `git show feat/embeddings-clustering:docs/superpowers/specs/2026-05-28-cue-detection-design.md`
- **Implementation plan written:** `docs/superpowers/plans/2026-05-28-cue-detection.md` (on `feat/triage-player`)
- **Schema stub already exists:** `cue_points` table is in `migrations/002_cue_points.sql`. Migration 005 (adding `confidence` + `source` columns) still needs to be created.
- **Stub test file exists:** `tests/test_analyze_cues.py` exists but uses OLD design (imports from `multidj.analyze`, wrong mock format). It must be **completely replaced** by the plan.
- **No `multidj/cues.py` exists yet** — this is the main new module to create.

---

## Algorithm Summary

1. **allin1** (primary) — transformer model; outputs labeled segments (`intro`, `verse`, `chorus`, `outro`, etc.) + bar grid (downbeats)
2. **librosa** (secondary) — energy envelope + spectral flux + chroma novelty; produces transition timestamps
3. **Cross-validation** — for each allin1 boundary, check if librosa agrees within ±1 bar
   - Both agree → `confidence='high'` → synced to Mixxx
   - Only allin1 → `confidence='low'` → stored in MultiDJ only
4. **Derived `drop` cue** — first `chorus` or `instrumental` segment becomes an additional `drop` cue
5. **Bar-snap** — all positions snapped to nearest allin1 downbeat

---

## Files to Create/Modify

| Action | File | What |
|---|---|---|
| Create | `multidj/migrations/005_cue_points_v2.sql` | ADD `confidence` + `source` columns to `cue_points` |
| Create | `multidj/cues.py` | `detect_cues()`, `analyze_cues()`, `clear_cues()` |
| Modify | `pyproject.toml` | Add `allin1`, `torchaudio` to `embeddings` extra |
| Modify | `multidj/cli.py` | Add `analyze cues` + `cues clear` subcommands |
| Modify | `multidj/pipeline.py` | Insert `cues` step 8 (after energy, before embed) |
| Modify | `multidj/adapters/mixxx.py` | Add `_push_cues_to_mixxx()`, call from `full_sync` |
| Modify | `tests/fixtures/mixxx_factory.py` | Add `cues` table to Mixxx DDL |
| Replace | `tests/test_analyze_cues.py` | Full test suite (replace the existing stub entirely) |
| Create | `tests/test_mixxx_cue_sync.py` | Mixxx cue push tests |
| Modify | `tests/test_pipeline.py` | Assert cues step present + --skip-cues |
| Modify | `CLAUDE.md` | Commands table + pipeline step list |

---

## Key Design Constraints

- `source='auto'` on all machine-written cues; `source='manual'` on user-set cues (never overwritten)
- Only `confidence='high'` AND `type IN ('intro','drop','outro')` cues are pushed to Mixxx
- Mixxx hot cue slots: intro=0 (blue), drop=1 (red), outro=2 (green)
- Mixxx cue `position` = seconds × 44100 (sample frames at 44100 Hz)
- Reconcile: `DELETE FROM cues WHERE hotcue IN (0,1,2)` before re-inserting — MultiDJ is source of truth
- `tracks.intro_end` = position of first non-intro cue; `tracks.outro_start` = position of `outro` cue (denormalized shortcuts)
- Escape hatch: `multidj cues clear --apply` removes all `source='auto'` cues and NULLs `intro_end`/`outro_start`
- allin1 is added to the `embeddings` extra (already has torch); pipeline step raises RuntimeError with install hint if missing

---

## CLI Shape

```bash
multidj analyze cues --apply              # tracks missing cues only
multidj analyze cues --apply --force      # re-analyze all
multidj analyze cues --apply --limit 20   # test subset
multidj cues clear --apply               # wipe all auto cues
```

---

## Pipeline Position

```
import(1) → fix_mismatches(2) → parse(3) → dedupe(4) →
bpm(5) → key(6) → energy(7) → cues(8) →
embed(9) → cluster(10) → genres(11) → clean_text(12) →
crates(13) → sync(14) → report(15)
```

---

## Test Mocking Pattern

`detect_cues` must be mocked in all `analyze_cues` tests — do NOT call real allin1/librosa in tests:

```python
_MOCK_DETECT_RETURN = [
    {"type": "intro",  "position": 0.0,   "confidence": "high", "label": "intro"},
    {"type": "verse",  "position": 30.0,  "confidence": "high", "label": "verse"},
    {"type": "chorus", "position": 90.0,  "confidence": "high", "label": "chorus"},
    {"type": "drop",   "position": 90.0,  "confidence": "high", "label": "Drop (chorus)"},
    {"type": "outro",  "position": 180.0, "confidence": "high", "label": "outro"},
]

with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
    result = analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))
```

For `detect_cues` unit tests, mock both `multidj.cues.allin1` and `multidj.cues.librosa` at the module level.

---

## Next Agent Instructions

1. Read the full plan: `docs/superpowers/plans/2026-05-28-cue-detection.md`
2. Use `superpowers:subagent-driven-development` skill
3. Branch from `dev`: `git checkout dev && git pull && git checkout -b feat/cue-detection`
4. Execute tasks 1–9 in order
5. After all tests pass, open PR to `dev`
