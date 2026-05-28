# MultiDJ Pipeline — Design Spec

**Date:** 2026-04-22
**Status:** Approved, pending implementation

---

## Overview

MultiDJ adds a `pipeline` command that chains all analysis and organization steps into a single operation. The DJ runs `multidj pipeline --apply` after adding new tracks (or any time) and Mixxx opens fully updated — new tracks imported, all metadata detected, crates regenerated, everything synced.

MultiDJ is the **source of truth**. Mixxx is a read target. All library management happens in MultiDJ; Mixxx displays the result.

---

## Pipeline Command

### Invocation

```bash
multidj pipeline              # dry-run: shows what would happen
multidj pipeline --apply      # executes all steps
multidj pipeline --apply --skip-energy   # skip a step
```

### Steps (in order)

| # | Step | Command equivalent | Skips if |
|---|------|--------------------|----------|
| 1 | Import new tracks | `import directory <music_dir>` | No new files found |
| 2 | Parse filenames | `parse` | All tracks have artist+title |
| 3 | Detect BPM | `analyze bpm` | All tracks have bpm |
| 4 | Detect key | `analyze key` | All tracks have key |
| 5 | Detect energy | `analyze energy` | All tracks have energy |
| 6 | Normalize genres | `clean genres` | No genre normalization needed |
| 7 | Rebuild crates | `crates rebuild` | — always runs |
| 8 | Sync to Mixxx | `sync mixxx` | — always runs |

### Behavior

- **Dry-run by default** — `--apply` required to write anything
- **One backup** taken at the start of an apply run, not per step
- **Per-step skip flags** — `--skip-parse`, `--skip-bpm`, `--skip-key`, `--skip-energy`, `--skip-genres`, `--skip-crates`, `--skip-sync`
- Each step prints its own summary line (tracks processed / skipped / errors)
- One step failing does not abort the pipeline — remaining steps still run; errors are collected and printed at the end
- Config controls which crate dimensions are generated (see Config section); pipeline respects this automatically (e.g. skips `analyze energy` if `energy = false` in config)

---

## New Command: `analyze energy`

Detects the energy level of each track using librosa (already a dependency).

### Algorithm

RMS loudness + spectral centroid computed across the full track, each normalized to [0, 1] across the library, then averaged into a single `energy` float (0.0–1.0).

### Storage

Written to the existing `energy` column on the `tracks` table. No schema migration needed.

### Crate labels

| Crate | Range | Meaning |
|-------|-------|---------|
| `Energy:Low` | 0.00–0.33 | Warmup, cooldown |
| `Energy:Mid` | 0.34–0.66 | Building, transitional |
| `Energy:High` | 0.67–1.00 | Peak hour |

Thresholds are configurable via `~/.multidj/config.toml`.

### Command flags

Matches the pattern of `analyze bpm` and `analyze key`:

```bash
multidj analyze energy             # dry-run
multidj analyze energy --apply     # write to DB
multidj analyze energy --force     # reprocess tracks that already have energy
multidj analyze energy --limit 50  # process at most N tracks
```

Per-track error isolation — one bad audio file does not abort the batch.

---

## Config File

`~/.multidj/config.toml` — created with defaults on first run if missing.

```toml
[pipeline]
music_dir = ""  # set on first run — app prompts if empty

[crates]
bpm      = true   # BPM:90-105, BPM:125-130, etc.
key      = true   # Key:1A, Key:2B, etc. (Camelot wheel)
genre    = true   # Genre:House, Genre:Techno, etc.
energy   = true   # Energy:Low, Energy:Mid, Energy:High
language = true   # Lang:Hebrew, etc.

[crates.bpm]
min_tracks = 3    # suppress crates with fewer tracks than this

[crates.energy]
low_max  = 0.33
high_min = 0.67
```

### First-run onboarding

If `music_dir` is empty (i.e. config doesn't exist yet or was just created), any command that needs it will prompt the user:

```
MultiDJ music directory not set.
Enter the path to your main music folder: ~/Music/All_Tracks
Saved to ~/.multidj/config.toml
```

The value is saved immediately and never asked again. It can be changed by editing the config file directly.

### Ad-hoc imports from other directories

Tracks do not need to live in `music_dir` to be managed by MultiDJ. The `import directory` command accepts any path:

```bash
multidj import directory /Volumes/USB/NewPurchases --apply
```

These tracks are added to the DB and automatically included in all subsequent pipeline steps (analysis, crates rebuild, Mixxx sync) — they are first-class library members regardless of where their files live. The pipeline's step 1 only scans `music_dir` for tracks not yet in the DB; it does not affect tracks already imported from other locations.

### Behavior

- `crates rebuild` reads this config and generates only the enabled dimensions
- `pipeline` skips analysis steps for disabled dimensions (no point detecting energy if `energy = false`)
- Config is user-editable; no CLI flags needed to toggle dimensions — just edit the file and re-run pipeline

---

## Mixxx Crate Sync (Phase 7)

`sync mixxx --apply` (and the pipeline's final step) pushes crates to Mixxx in addition to track metadata.

### Sync logic

1. For each active crate in MultiDJ: create or update the matching row in Mixxx's `crates` table
2. Sync membership: reconcile `crate_tracks` in Mixxx to match MultiDJ (add missing, remove stale)
3. Crates deleted from MultiDJ are removed from Mixxx on next sync
4. Both hand-curated and auto-generated crates sync

### Source-of-truth contract

- MultiDJ → Mixxx is **one-way**. Mixxx is a display layer.
- Crates created directly in Mixxx (outside MultiDJ) are not imported and will be overwritten on next sync.
- `import mixxx` is a **one-time bootstrap** only. After that, all library management happens in MultiDJ.
- For new users: run `multidj import mixxx --apply` once, then MultiDJ owns everything going forward.

---

## Future Upgrade Paths (out of scope for this implementation)

These are noted here to confirm the architecture supports them, not to implement them now.

- **Parse step** — today: filename pattern matching. Future: mutagen tag reading, MusicBrainz lookups. CLI interface stays the same.
- **Energy model** — today: RMS + spectral centroid. Future: more sophisticated model (e.g. danceability score). Thresholds already configurable.
- **Watch mode** — background daemon to run pipeline incrementally on new files. Phase 2+.
- **Named set profiles** — `multidj apply-profile festival --apply`. Layered on top of config dimensions later.
- **Additional adapters** — Rekordbox, Serato. MixxxAdapter is the reference implementation; adapter ABC already defined.
- **MCP server** — expose all commands as agent-callable tools for an AI DJ assistant.

---

## Testing

| Test file | Coverage |
|-----------|----------|
| `tests/test_pipeline.py` | dry-run shows steps, apply runs all steps in order, `--skip-*` flags work, single backup created, one-step failure doesn't abort pipeline |
| `tests/test_analyze_energy.py` | energy stored correctly, `--force` reprocesses, per-track error isolation, dry-run doesn't write |
| `tests/test_config.py` | defaults created if missing, disabled dimensions skipped in crates rebuild, pipeline skips analysis for disabled dimensions |
| `tests/test_mixxx_crate_sync.py` | crates created in Mixxx, membership reconciled, deletions propagate, hand-curated crates sync |

All tests use isolated fixture DBs via `tmp_path` — no writes to `~/.multidj/` or `~/.mixxx/`.

## Repository Sync Note (2026-04-30)

- Clean text behavior now strips promotional noise markers from artist/title tails, including free, dl, and download variants.
- BPM analysis now samples start/middle/end windows and reports variable-tempo cases instead of hiding half/double-time ambiguity.
- Directory import now includes artist-title swap mismatch detection for stronger metadata hygiene during ingestion.
- Directory import now soft-deletes (`deleted=1`) tracks whose files no longer exist on disk after a rescan.
- Pipeline expanded to 10 steps: `fix_mismatches` (step 2) auto-corrects artist/title swaps across all active tracks; `clean_text` (step 8) strips promo markers from artist/title/album.
- Added persistent DB path config: `multidj config set-db <path>` stores `[db].path`, and commands now use it when `--db` is omitted.
- Parse now skips junk artist/title proposals (numeric-only and `free`/`dl`/`download` marker values) to reduce bad suggestions in common use.
- Added `multidj report dashboard` for standalone interactive HTML dashboard output with optional `--output` path.
- Pipeline report step now generates the interactive dashboard by default while remaining read-only and non-fatal.
- Added experimental Camelot harmonic transition analysis/visualization in crate views (UI-only interactions, no DB persistence).
