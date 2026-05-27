# MultiDJ — Automatic Cue Detection Design Spec

**Date:** 2026-05-28
**Status:** Approved — ready for implementation plan
**Scope:** Phase 13 — full structural segmentation of tracks into labeled cue points, synced to Mixxx

---

## 1. Goal

Automatically parse every track into its musical sections (intro, verse, chorus, breakdown, drop, outro) and store the boundaries as cue points in MultiDJ. Sync the three DJ-critical cues — intro end, drop, outro start — to Mixxx as hot cues, so tracks are performance-ready with zero manual setup.

The feature runs as a pipeline step. The primary workflow is: drop files in, run `multidj pipeline --apply`, open Mixxx, and gig.

---

## 2. Detection Algorithm

Two independent signals. Agreement between them drives confidence scoring.

### 2a. Primary: `allin1` (neural structural segmentation)

`allin1` is a 2023 transformer-based model purpose-built for music structure analysis. It outputs:
- **Segment labels** with start/end times: `intro`, `verse`, `pre-chorus`, `chorus`, `bridge`, `breakdown`, `outro`, `instrumental`
- **Downbeat times** — a bar-level grid snapped to musical beats

Each segment boundary becomes a candidate cue point, positioned at the start of that section, snapped to the nearest downbeat.

`allin1` requires PyTorch and torchaudio, which are already present in the `embeddings` extra. It is added to the same extra — no new install group needed.

### 2b. Secondary: librosa (energy + spectral cross-validation)

librosa independently computes, over the same track:
- RMS energy envelope
- Spectral flux (rate of spectrum change)
- Chroma novelty (harmonic transitions)

Peak-picking on these three signals produces a list of raw transition timestamps.

### 2c. Cross-validation

For each `allin1` segment boundary, check whether librosa detected a transition within ±1 bar (calculated from the track's BPM, which is already stored).

- Both signals agree → `confidence = 'high'`
- Only allin1 detects the boundary → `confidence = 'low'`

High-confidence cues are trusted and synced to Mixxx. Low-confidence cues are stored in MultiDJ but not pushed to Mixxx unless the user explicitly inspects them.

---

## 3. Schema Changes

**New migration: `005_cue_points_v2.sql`**

```sql
ALTER TABLE cue_points ADD COLUMN confidence TEXT NOT NULL DEFAULT 'high';
-- 'high' = allin1 + librosa agree | 'low' = allin1 only

ALTER TABLE cue_points ADD COLUMN source TEXT NOT NULL DEFAULT 'auto';
-- 'auto' = written by analyze cues | 'manual' = user-set, never overwritten
```

**Expanded `type` enum** (was: `'intro_end' | 'drop' | 'outro_start' | 'hot_cue'`):
```
'intro' | 'verse' | 'pre-chorus' | 'chorus' | 'bridge' |
'breakdown' | 'drop' | 'outro' | 'instrumental' | 'hot_cue'
```

Each cue marks the **start** of its section. `position` is seconds from track start, snapped to the nearest allin1 downbeat.

**Denormalized shortcuts on `tracks`** (already exist as columns):
- `tracks.intro_end` — set from the position of the first non-intro section boundary
- `tracks.outro_start` — set from the position of the `outro` cue

These exist for Mixxx sync compatibility and fast querying without a join.

---

## 4. New Module: `multidj/cues.py`

Responsible for all cue detection and cue management logic.

**Public functions:**

```python
def detect_cues(filepath: str, bpm: float) -> list[dict]:
    """Run allin1 + librosa, return cross-validated cue candidates.

    Each item: {type, position, confidence, label}
    """

def analyze_cues(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    backup_dir=False,
) -> dict:
    """Detect and store structural cues for tracks missing them (or all if force=True)."""

def clear_cues(
    db_path: str | None = None,
    apply: bool = False,
) -> dict:
    """Remove all source='auto' cue points and reset tracks.intro_end / outro_start."""
```

Follows the same error-isolation pattern as `analyze_bpm` / `analyze_energy`: per-track exceptions are caught and reported without aborting the batch.

---

## 5. CLI

```bash
# Analyze (manual / testing)
multidj analyze cues --apply              # tracks missing cues only
multidj analyze cues --apply --force      # re-analyze all tracks
multidj analyze cues --apply --limit 20   # test on 20 tracks first

# Escape hatch
multidj cues clear --apply               # wipe all auto-detected cues
```

`--apply` is required to write (same safety pattern as all write commands). Dry-run (omitting `--apply`) reports how many tracks would be analyzed and exits.

**`cues` is registered as a top-level subcommand group in `cli.py`**, alongside `analyze`, `crates`, `audit`, etc.

---

## 6. Pipeline Integration

Inserted as **step 8**, after `energy` and before `embed`:

```
import(1) → fix_mismatches(2) → parse(3) → dedupe(4) →
bpm(5) → key(6) → energy(7) → cues(8) →
embed(9) → cluster(10) → genres(11) → clean_text(12) →
crates(13) → sync(14) → report(15)
```

`cues` is auto-skipped if disabled in config (`pipeline.cues = false`). The `--skip-cues` flag also skips the step.

`allin1` requires the `embeddings` extra. If the extra is not installed, the step raises `RuntimeError` with an install hint (same pattern as `embed` / `cluster`).

---

## 7. Mixxx Sync

Only **three cues** are pushed to Mixxx as hot cues — the ones that matter for live performance:

| MultiDJ type | Mixxx label | Color |
|---|---|---|
| `intro` boundary | "Intro" | Blue |
| `drop` | "Drop" | Red |
| `outro` | "Outro" | Green |

All other structural cues (verse, chorus, breakdown, etc.) are stored in MultiDJ only — they do not appear in Mixxx. This keeps the Mixxx view clean.

**Only `confidence = 'high'` cues are synced.** Low-confidence cues stay in MultiDJ but are not pushed until the user re-runs with `--force` or manually marks them.

`sync mixxx --apply` is extended in `adapters/mixxx.py` to:
1. Read `cue_points` rows for each dirty track where `source='auto'` and `confidence='high'` and `type IN ('intro', 'drop', 'outro')`
2. Write them to Mixxx `cues` table with `type=1` (hot cue), position in milliseconds, label and color
3. Reconcile: remove stale Mixxx cues (labels that no longer exist in MultiDJ) on each sync

---

## 8. Testing

| File | Coverage |
|---|---|
| `tests/test_analyze_cues.py` | dry-run does not write; apply stores cues; force overwrites; per-track failure isolation; confidence assigned correctly |
| `tests/test_cues_clear.py` | apply removes all `source='auto'` cues; manual cues untouched; `tracks.intro_end` / `outro_start` reset to NULL |
| `tests/test_pipeline.py` (extended) | `cues` step present in pipeline output; `--skip-cues` skips it |
| `tests/test_mixxx_cue_sync.py` | intro/drop/outro written to Mixxx; low-confidence cues not pushed; stale Mixxx cues removed |

Audio analysis calls (`allin1.analyze`, `librosa.load`) are mocked in tests — no real audio or model download required.

---

## 9. Dependencies

`allin1` is added to the `embeddings` extra in `pyproject.toml`. It requires `torch` and `torchaudio`, which are already present in that extra.

```toml
[project.optional-dependencies]
embeddings = [
    ...,
    "allin1",
]
```

First run downloads the allin1 model weights (~200 MB) to `~/.cache/allin1/`. Subsequent runs use the cached model.

---

## 10. Branching and Merge Strategy

**Branch from `dev`** (not from `feat/embeddings-clustering`):

```bash
git checkout dev
git pull
git checkout -b feat/cue-detection
```

`feat/embeddings-clustering` must be merged into `dev` first if its changes are needed. The cue detection branch touches: `cues.py` (new), `cli.py`, `pipeline.py`, `adapters/mixxx.py`, `migrations/005_cue_points_v2.sql` — minimal overlap with the embeddings branch.

**Merge back via PR to `dev`** when all tests pass.

---

## 11. Documentation Requirements

On completion of implementation:

1. **`CLAUDE.md`** — add `analyze cues` and `cues clear` to the Commands table; update pipeline step count and step list; add `allin1` to the embeddings extra description
2. **`.agent-handoff/`** — update the relevant layer files to describe the new `cues.py` module, the expanded `cue_points` schema, and the Mixxx sync extension
3. **Module docstrings** — `cues.py` public functions documented with parameter types and return shape

Documentation is a required final step in the implementation plan, not optional.
