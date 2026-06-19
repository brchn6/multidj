# MultiDJ — Architectural Decisions

Every significant design choice, with the date it was made and the reasoning behind it.
These are locked decisions — changing them has downstream consequences.

---

## 2026-03-21 — MultiDJ owns the DB, not Mixxx

**Decision:** `~/.multidj/library.sqlite` is the single source of truth.
Mixxx's `mixxxdb.sqlite` is a downstream sync target only.

**Why:** Mixxx's schema is undocumented, changes between versions, and is tightly
coupled to Mixxx's internal concepts. Owning our own DB means we can add fields,
run analyses, and track state without risking Mixxx breakage.

**Consequence:** All write commands target MultiDJ DB. `sync mixxx` is the only
command that writes to Mixxx's DB, and only when explicitly invoked with `--apply`.

---

## 2026-03-21 — `--apply` required for all mutations; dry-run by default

**Decision:** Every command that modifies state requires an explicit `--apply` flag.
Without it, the command runs in dry-run mode and prints what it would do.

**Why:** DJ library is irreplaceable data. A bug in an auto-run command should not
silently corrupt thousands of tracks. The user must opt-in to every write.

**Consequence:** Pipeline, enrich, analyze, sync, import — all are safe to run
without `--apply` to preview changes.

---

## 2026-03-21 — Auto-backup before every write

**Decision:** `backup.py` creates a timestamped copy of `library.sqlite` before
every write operation. `pipeline` takes one backup at the very start; individual
steps get `backup_dir=False` to suppress per-step backups.

**Why:** Defense against bugs and bad data. A corrupt DB run can be rolled back by
swapping in the backup.

---

## 2026-03-21 — Adapter pattern for DJ software sync

**Decision:** Each DJ software target (Mixxx, Rekordbox, Serato) is an isolated
adapter module implementing `SyncAdapter` ABC (`import_all`, `push_track`,
`full_sync`). Core logic never imports from adapters directly.

**Why:** Prevents core code from being coupled to Mixxx internals. When Rekordbox
support is added, zero changes needed to core modules.

**Current adapters:** `MixxxAdapter`, `DirectoryAdapter`

---

## 2026-03-21 — `--json` global flag for machine-consumable output

**Decision:** `emit(data, json_mode)` in `utils.py` is the only output path.
All commands produce structured data first, then format for human or JSON output.

**Why:** Enables LLM-driven usage — an agent can call `multidj scan --json` and
parse the result without screen-scraping.

**Consequence:** Every command module returns Python dicts/lists, never prints
directly.

---

## 2026-03-21 — Schema versioned; migrations auto-applied on write connection

**Decision:** SQL files in `multidj/migrations/NNN_name.sql` are auto-applied in
numeric order when `connect(readonly=False)` is called. `schema_version` table
tracks the applied version.

**Critical invariant:** `connect(readonly=True)` skips migrations. Commands that
need to read columns added by recent migrations must open a write connection first
(even if they won't write).

---

## 2026-04-01 — Three-tier crate protection model

**Decision:** Crates are classified into three tiers with different protection levels:

1. **Catch-all** ("New Crate") — never touched by any command
2. **Auto-generated** (`Genre:`, `BPM:`, `Key:`, `Energy:`, `Lang:`, `Vibe/` prefixes)
   — rebuilt from scratch on each `crates rebuild --apply` run
3. **Hand-curated** (everything else) — protected by default; only modified with
   `--include-hand-curated`

**Why:** DJs spend hours building personal crates. An automated command must never
silently delete or modify a hand-curated crate. The prefix convention makes
auto-generated crates unambiguous.

---

## 2026-04-06 — Pipeline takes ONE backup at start; steps suppress per-step backups

**Decision:** `pipeline.py` calls `backup()` once at the top. All step functions
receive `backup_dir=False` as a sentinel to skip their own backups.

**Why:** 17 steps × backup = 17 DB copies per run. Wasteful. One backup at the
start is sufficient since the pipeline is transactional in effect.

---

## 2026-04-06 — Pipeline is idempotent and incremental

**Decision:** Every analyze step checks whether the field is already populated
(`WHERE field IS NULL` or `LEFT JOIN` check on the embeddings table). Already-
processed tracks are skipped.

**Why:** The pipeline is meant to run daily. It should only process new or changed
tracks. Re-running on a fully analyzed library should be nearly instant.

**Consequence:** `--force` flag available on all analyze commands to bypass the
skip check and re-process everything.

---

## 2026-04-22 — Config file (not DB) controls crate dimension toggles

**Decision:** `~/.multidj/config.toml` has a `[crates]` section with boolean flags
for each dimension (`bpm`, `key`, `genre`, `energy`, `language`). These are read by
`pipeline` and `crates rebuild` to decide which auto-crate sets to generate.

**Why:** The DB is track data. Crate preferences are user preferences — they belong
in config, not in the DB schema. Makes it easy to disable e.g. the BPM crates
without any DB surgery.

---

## 2026-04-22 — Energy normalization is library-relative

**Decision:** `analyze energy` scores tracks on a 0–1 scale where the values are
normalized relative to the library. A single-track batch scores 0.5 (neutral).

**Why:** Absolute RMS values vary by genre, recording era, and mastering. Relative
scoring within a DJ's library is more actionable ("this is a high-energy track
compared to my collection") than absolute values.

---

## 2026-05-27 — CLAP for clustering; CLaMP3 only for text→audio search (2026-06-19)

**Decision:** Two embedding models are supported:

| Model | Alias | Dim | Use for |
|---|---|---|---|
| `laion/larger_clap_music` | `clap` | 512 | Clustering, similar, suggest |
| CLaMP3 (MERT-v1-95M → SAAS) | `clamp3` | 768 | Future text→audio agent search |

CLAP is the production model. CLaMP3 is kept for future cross-modal queries.

**Why:** CLaMP3 optimizes for cross-modal alignment (audio ↔ text ↔ MIDI), which
collapses audio-audio discrimination. On this library, CLaMP3 mean inter-track
cosine sim → 0.96+, making it useless for distinguishing tracks from each other.
CLAP mean ≈ 0.97 but relative ranking is preserved and meaningful.

**Established:** 2026-05-27 (CLAP only); clarified 2026-06-19 (after CLaMP3 integration
confirmed the behavior empirically — 10 tracks, CLaMP3 min sim = 0.000 but mean = 0.863,
while CLAP reliably clusters by genre/feel).

---

## 2026-05-27 — UMAP reduce before HDBSCAN (don't run HDBSCAN on raw embeddings)

**Decision:** `cluster vibe` and `viz_library.py` both use:
UMAP(512d→10d, metric=cosine) → HDBSCAN(metric=euclidean on 10d output)

**Why:** Tried running HDBSCAN directly on normalized 512d CLAP vectors. With mean
cosine sim ≈ 0.97, all pairwise cosine distances ≈ 0.027 — too small and uniform
for HDBSCAN to find any density structure. HDBSCAN labeled every point as noise (-1).

UMAP expands the local structure into a lower-dimensional space where HDBSCAN can
find meaningful density peaks. Result: 15 clusters on 1674 tracks.

**Established:** 2026-05-27 (in production cluster.py); confirmed/documented
2026-06-19 when viz_library.py was initially broken with the same mistake.

---

## 2026-05-31 — No `protobuf` package dependency for Mixxx BLOBs

**Decision:** `mixxx_blobs.py` hand-rolls the protobuf varint and length-delimited
wire format instead of depending on the `protobuf` pip package.

**Why:** `protobuf` is a heavy dependency and its Python API has broken backward
compatibility repeatedly. The Mixxx protobuf schema is simple enough (BeatGrid-2.0:
~3 fields; KeyMap-1.0: 1 field) that hand-rolling is trivial and produces a leaner,
more stable implementation.

**Verification:** Bit-for-bit comparison against real Mixxx-produced BLOBs from
three tracks at 155 BPM, 142 BPM, 140 BPM.

---

## 2026-05-31 — Three-layer metadata enrichment order: file tags → Discogs → MusicBrainz

**Decision:** `enrich_track()` applies layers in this order, stopping when a field is
already populated by a prior layer:

1. File tags (mutagen) — fastest, no network, most reliable for artist/title
2. Discogs API — best for genre, label, release year on DJ music
3. MusicBrainz — fallback; open database, offline-capable

**Why:** File tags are ground truth when they exist (user may have manually tagged).
Discogs has better coverage for electronic/DJ music specifically. MusicBrainz is the
final net for everything else.

**Consequence:** Layer 2 and 3 respect layer 1 — if file tags already have genre,
Discogs won't overwrite it (unless `--force`).

---

## 2026-06-08 — Stop writing BPM/key to Mixxx library table

**Decision:** `sync mixxx` no longer writes `library.bpm` or `library.key` to
Mixxx's DB. It only writes `analysis` table BLOBs (BeatGrid + KeyMap).

**Why:** Mixxx's analysis engine has its own BPM and key detection. Writing values
into `library` caused Mixxx to show MultiDJ's values in the "analyzed" column but
then re-analyze and display different values from its own engine — confusing.
The BLOBs in `analysis` table are what Mixxx actually uses for beatgrid display
and hot cue timing.

---

## 2026-06-19 — DJ suggest score weights: 70/15/15

**Decision:** `suggest_next()` score = 0.70×cosine_sim + 0.15×bpm_compat + 0.15×camelot_key_compat

**Why:** Embedding similarity is the dominant signal for "does this track fit the vibe".
BPM and key compatibility are secondary — important for smooth mixing but a track that's
slightly off-BPM but sonically perfect is usually better than an on-key track that
sounds wrong.

Weights were set by intuition; can be tuned with user feedback. The `--bpm-window`
flag (default ±15 BPM) controls how aggressively BPM compat decays.

---

## 2026-06-19 — suggest filters to same Vibe/ cluster by default

**Decision:** `suggest_next()` restricts candidates to the same `Vibe/` cluster as
the query track. `--any-cluster` overrides to search the full library.

**Why:** Same-cluster tracks share overall sonic character. Cross-cluster suggestions
risk jarring transitions even if embedding similarity is high (cosine sims are
compressed in this library — small differences matter). Cluster filter is the
coarser-grained "vibe check" before fine-grained cosine ranking.

**Fallback:** If the query track has no cluster membership, search the full library
(same behavior as `--any-cluster`).

---

## 2026-06-19 — Precompute neighbor indices for viz; embed as JSON in HTML

**Decision:** `viz_library.py` computes the full N×N pairwise cosine similarity
matrix in Python at generation time, extracts top-5 neighbors per track, and embeds
the indices + sims as JSON inside the self-contained HTML file.

**Why:** Alternatives were (a) recompute on click in JS — too slow for 1674 tracks,
(b) separate JSON file — breaks self-contained portability.
Tradeoff: ~800 KB HTML for 1674 tracks. Acceptable.

---

## Summary of Key Constants

| Constant | Value | Established |
|---|---|---|
| BPM compatibility window | ±15 BPM (default) | 2026-06-19 |
| Suggest score weights | 70/15/15 (cosine/bpm/key) | 2026-06-19 |
| CLAP model | `laion/larger_clap_music` | 2026-05-27 |
| CLAP dimensions | 512 | 2026-05-27 |
| CLaMP3 model | MERT-v1-95M → clamp3_saas | 2026-06-19 |
| CLaMP3 dimensions | 768 | 2026-06-19 |
| UMAP output dims | 10 | 2026-05-27 |
| UMAP metric | cosine | 2026-05-27 |
| HDBSCAN metric | euclidean (on UMAP output) | 2026-05-27 |
| CLAP 3-window sampling | 30s each: start/mid/end | 2026-05-27 |
| CLaMP3 chunk size | 5s non-overlapping | 2026-06-19 |
| Cue slot mapping | intro=0(blue), drop=1(red), outro=2(green) | 2026-05-28 |
| Energy normalization | library-relative RMS×centroid, 0–1 | 2026-04-22 |
