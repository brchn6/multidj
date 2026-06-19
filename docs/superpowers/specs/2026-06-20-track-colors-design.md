# Track Color Sync & Classification — Design Spec
**Date:** 2026-06-20
**Status:** Planned, not yet implemented
**Author:** brainstorming session

---

## Goal

Preserve and extend the manual track coloring done in Mixxx. Colors represent personal **playability classes** — a DJ taxonomy of which tracks to play, when, and in what context. The system must:

1. Import colors from Mixxx into MultiDJ (so they're not locked in Mixxx)
2. Never overwrite a manually-set color — Mixxx manual colors are source of truth
3. Visualize the color classes in CLAP embedding space to evaluate whether they cluster
4. Eventually: automate color prediction for uncolored tracks using KNN on CLAP embeddings
5. Push predicted colors back to Mixxx (only to tracks with no existing color)

---

## Current State

- Mixxx `library` table has a `color INTEGER` column (ARGB packed integer)
- **342 tracks already manually colored** across 8 distinct colors as of 2026-06-20
- MultiDJ `tracks` table has **no color field** — all that work is stranded in Mixxx
- `sync mixxx --apply` does not read or write track colors today

---

## What the Colors Mean

Colors are the user's personal playability taxonomy — not genre metadata. Examples established so far:
- **Yellow** — "request only" / pop / tracks to play if asked but not by choice
- **Light yellow** — "maybe" / slightly less certain / still borderline
- **Green variants** — heavier party tracks; darker green = more peak energy
- **Pink** — disco / specific vibe category

Full color-to-label mapping TBD — user needs to review all 8 colors against the actual Mixxx palette and write down what each means. This mapping must be established before Phase 3 (automation) can be implemented.

---

## Four-Phase Plan

### Phase 1 — Import: Mixxx → MultiDJ

**Migration:** Add `color INTEGER DEFAULT NULL` to `tracks` table.

**Import logic:**
- Extend `MixxxAdapter.import_all()` (and the path-matching in `import_mixxx_analysis.py`) to also pull `color` from Mixxx's `library` table
- Add a standalone `import mixxx-colors --apply` subcommand for on-demand re-sync
- **Protection rule:** only write if MultiDJ `color IS NULL` — never overwrite an existing value
- Also extend `scan` output to report color coverage: how many tracks have a color set

**Result:** All 342 manually-colored tracks are now mirrored in MultiDJ DB. Safe to re-run — idempotent.

---

### Phase 2 — Visualize: Color classes in embedding space

**Objective:** Evaluate whether the manual color classes actually cluster in CLAP embedding space. If they do, KNN prediction will work well. If they don't, automation will produce noise.

**What to build:**
- Add "Mixxx Color" as a color option in `scripts/viz_library.py` alongside Genre / Cluster / BPM / Key
- Render each track point using its actual Mixxx color (convert ARGB int → CSS hex)
- Uncolored tracks render as grey

**How to evaluate:**
- Run the viz, look at the UMAP scatter with "Mixxx Color" selected
- Do yellow tracks cluster together? Do the greens? Do pinks form an island?
- If yes: KNN will work, proceed to Phase 3
- If scattered: the color taxonomy may not be acoustically coherent — reconsider automation

---

### Phase 3 — Automate: KNN color prediction

**Only build this after Phase 2 confirms the classes cluster.**

**Approach:** Use color-labeled tracks as a labeled training set. For each uncolored track:
1. Find its K nearest CLAP embedding neighbors (cosine similarity)
2. Look at the colors of those neighbors
3. If >60% agree on a color → assign that color with `source='predicted'`
4. Otherwise → leave uncolored (don't guess on ambiguous cases)

**New command:** `multidj classify colors [--apply] [--k N] [--confidence F] [--limit N]`
- Dry-run by default: prints what would be assigned
- `--apply` writes to MultiDJ DB only (not Mixxx yet)
- `--k` controls neighbor count (default: 10)
- `--confidence` controls agreement threshold (default: 0.6)

**Requires:** `color` column populated (Phase 1), CLAP embeddings present (existing `analyze embed`)

---

### Phase 4 — Push back: MultiDJ → Mixxx

**Extend `sync mixxx --apply`** to include color sync:
- For each track with a color in MultiDJ: check Mixxx's current color
- If Mixxx color is NULL or 0: write MultiDJ color to Mixxx
- If Mixxx color is already set: **skip** — never overwrite manual Mixxx colors

This means manually-set Mixxx colors always win, but predicted/imported colors flow back into Mixxx for tracks the user hasn't manually colored yet.

---

## Data Model Change

```sql
-- Migration: add color to tracks
ALTER TABLE tracks ADD COLUMN color INTEGER DEFAULT NULL;
```

No `source` field needed on the color itself — the protection rule is simpler than cue points: if Mixxx has a color, it wins. If MultiDJ has a predicted color and Mixxx doesn't, push it.

For tracking prediction provenance, use a `track_tags` entry:
- `key = 'color_source'`, `value = 'manual'|'predicted'`
- Allows future audit of which colors were human vs machine

---

## Open Questions (resolve before Phase 3)

1. **Full color map:** What do all 8 colors mean? Write this down in a comment, config entry, or constants file before building the classifier — labels matter for evaluation.
2. **ARGB vs RGB:** Mixxx stores colors as packed ARGB integers (e.g. `16776960` = `0xFFFFFF00` = yellow). Need to verify byte order and whether alpha is always `0xFF`.
3. **Pipeline integration:** Should `import mixxx-colors` run as a pipeline step? Probably yes (step 2, after import), but confirm once Phase 1 is built.
4. **Reclassification:** What happens when the user manually re-colors a track in Mixxx after MultiDJ has pushed a predicted color? Answer: next `import mixxx-colors` run will overwrite MultiDJ's predicted value with the new manual one (manual always wins on import).

---

## Implementation Notes

- `color INTEGER` in Mixxx is ARGB packed: `(alpha << 24) | (red << 16) | (green << 8) | blue`. For CSS/display: strip alpha, format as `#RRGGBB`.
- KNN can reuse `load_embeddings_from_db()` and cosine similarity infrastructure already in `embed.py` — no new embedding machinery needed.
- Phase 2 viz change is a small addition to `scripts/viz_library.py` — add color lookup by track path, render point color from the integer.
- The `track_tags` table (arbitrary key/value per track) already exists — use it for `color_source` rather than adding another column.
