# MultiDJ Feature Brainstorm

**Date:** 2026-04-22
**Branch:** `brainstorm/new-feature`

## Current product gap

The strongest near-term feature is one that extends the current architecture instead of bypassing it.

Two facts stand out:

- The schema already includes `tracks.intro_end`, `tracks.outro_start`, and a `cue_points` table.
- No CLI command, pipeline step, or Mixxx sync path currently uses those fields.

That makes cue-point workflow support the most natural next feature area.

## Ranked feature candidates

### 1. Cue point analysis + Mixxx sync

**Why it fits now**

- Uses schema that already exists.
- Directly improves DJ workflow, not just library hygiene.
- Fits the current product shape: analyze -> persist -> crate/sync style commands.
- Opens a path to better transitions, set prep, and future auto-mixing helpers.

**User value**

- Detect intro/outro boundaries automatically.
- Store hot cues in MultiDJ as source of truth.
- Push those markers into Mixxx so tracks are performance-ready.

**Possible CLI shape**

```bash
multidj analyze cues
multidj analyze cues --apply
multidj analyze cues --force
multidj analyze cues --limit 50
multidj sync mixxx --apply
```

**MVP scope**

- Detect `intro_end` and `outro_start` only.
- Store both on `tracks` and mirror them into `cue_points` rows.
- Extend Mixxx sync to write those markers.
- Add dry-run summaries and per-track error isolation.

**Phase 2**

- Detect `drop` markers.
- Support named `hot_cue` slots and colors.
- Add crate generation such as `Needs Cues` or `Has Intro/Outro`.

**Key risks**

- Audio heuristics can be noisy on ambient or non-club tracks.
- Mixxx cue storage details may require adapter-specific translation logic.

### 2. Watch mode for incremental pipeline runs

**Why it fits now**

- Already called out in design docs as a future path.
- Builds directly on `pipeline` and `import directory`.
- Good operational win for a frequently updated library.

**User value**

- Drop files into a watched directory and let MultiDJ import/analyze/sync automatically.

**MVP scope**

- New command: `multidj watch`.
- Poll or filesystem-watch one configured directory.
- Trigger a narrowed pipeline run when new files appear.

**Why it ranks second**

- High convenience, but lower musical value than cue support.
- More operational complexity around long-running processes and failure recovery.

### 3. Set-prep crates and transition helpers

**Why it fits now**

- MultiDJ already has BPM, key, energy, language, and crates.
- Cue support would make this even stronger later.

**User value**

- Auto-generate crates like `Warmup`, `Peak Hour`, `Closing`, `Same Key`, `Energy Lift`, or `Bridge Tracks`.

**MVP scope**

- Configurable rule-based crates built from existing metadata.
- No machine learning required.

**Why it ranks third**

- Valuable, but partly overlaps with current crate generation.
- Better once cue data exists and track quality is higher.

## Recommendation

Start with **Cue point analysis + Mixxx sync**.

It is the best combination of:

- existing schema support,
- clear DJ-facing user value,
- minimal product sprawl,
- strong follow-on potential.

This is also the most defensible feature story: MultiDJ already wants to be the source of truth for performance metadata, and cue points are core DJ performance metadata.

## Suggested MVP spec

### Command

```bash
multidj analyze cues             # dry-run candidates
multidj analyze cues --apply     # detect and store intro/outro markers
multidj analyze cues --force     # overwrite existing markers
multidj analyze cues --limit 50
```

### Behavior

- Candidate selection matches other analyze commands.
- Dry-run reports how many tracks would be analyzed.
- Apply mode computes intro/outro markers from audio structure.
- Writes:
  - `tracks.intro_end`
  - `tracks.outro_start`
  - matching rows in `cue_points`
- Per-track failures are isolated and reported.
- Optional future flag: `--write-tags` once file-tag support is defined.

### Pipeline integration

Insert a cue step after key/energy analysis:

```text
import -> parse -> bpm -> key -> energy -> cues -> genres -> crates -> sync
```

### Mixxx sync expectations

- MultiDJ remains the source of truth.
- `sync mixxx --apply` creates or updates Mixxx cue markers from MultiDJ state.
- Removed cue points in MultiDJ should be reconciled on sync.

### Test plan

- `tests/test_analyze_cues.py`
  - dry-run does not write
  - apply stores intro/outro markers
  - `--force` overwrites existing markers
  - per-track failure isolation works
- `tests/test_sync.py` or new `tests/test_mixxx_cue_sync.py`
  - cue markers are created in Mixxx
  - updates reconcile correctly
  - deletions propagate correctly
- `tests/test_pipeline.py`
  - cue step appears in pipeline
  - `--skip-cues` works

## Good next decision

Before implementation, decide one product boundary:

**Should MVP only support `intro_end` and `outro_start`, or also create one `drop` cue?**

Recommendation: keep MVP to intro/outro only. It is easier to explain, easier to validate, and enough to prove the end-to-end design.