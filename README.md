# MultiDJ

A software-agnostic DJ music library manager. Maintains its own SQLite database as the source of truth for track metadata, syncs to DJ software (Mixxx first; Rekordbox and Serato as future adapters).

Designed for agent-operated workflows: JSON output, dry-run defaults, per-track error isolation, and automatic backups before every write. Eventually exposed as an MCP server so AI agents can drive your full library pipeline natively.

## Install

```bash
uv sync
multidj --help
```

Optional audio analysis (BPM, key, energy detection):
```bash
uv sync --extra analysis
```

Optional semantic embeddings, clustering, similarity search, and DJ suggestions:
```bash
uv sync --extra embeddings
```

The `embeddings` extra adds CLAP audio encoding (`laion/larger_clap_music`, 512-dim), UMAP+HDBSCAN clustering, and the `multidj similar` / `multidj suggest` commands. Model weights (~1.5 GB) are downloaded on first use.

Optional CLaMP3 backend (for future text→audio agent vibe search):
```bash
uv sync --extra clamp3
git submodule update --init vendor/clamp3
```

Optional metadata enrichment (Discogs + MusicBrainz):
```bash
uv sync --extra enrich
```

## How MultiDJ uses two databases

MultiDJ keeps **its own database** separate from Mixxx:

| Database | Default path | Purpose |
|---|---|---|
| **MultiDJ DB** | `~/.multidj/library.sqlite` | Source of truth — all metadata, crates, BPM/key/energy |
| **Mixxx DB** | `~/.mixxx/mixxxdb.sqlite` | Read on import; written to on sync |

You never pass the Mixxx DB as `--db`. The `--db` flag (or `multidj config set-db`) always refers to the MultiDJ DB.

## First-time setup

```bash
# 1. Import your Mixxx library into the MultiDJ DB (reads Mixxx, writes to ~/.multidj/library.sqlite)
multidj import mixxx --apply

# 2. (Optional) Store your MultiDJ DB path so --db is automatic
multidj config set-db ~/.multidj/library.sqlite   # only needed if you want a non-default path

# 3. (Optional) Set [mixxx].path in ~/.multidj/config.toml so --mixxx-db is automatic:
#    [mixxx]
#    path = "~/.mixxx/mixxxdb.sqlite"
```

If you don't use Mixxx, import directly from your music folder:

```bash
multidj import directory ~/Music/All_Tracks --apply
```

## Daily workflow

### Full pipeline (with BPM/key analysis)

```bash
# Reads music_dir and mixxx path from ~/.multidj/config.toml automatically
multidj pipeline --apply

# Dry-run first to preview what would change
multidj pipeline

# Override paths for one-off runs
multidj pipeline --apply \
  --music-dir ~/Music/Other_Collection \
  --mixxx-db ~/backups/mixxxdb.sqlite
```

`--music-dir` and `--mixxx-db` are optional — omit to use `[pipeline].music_dir` and `[mixxx].path` from config:
- No `--music-dir` and no `[pipeline].music_dir` → skip directory import step
- No `--mixxx-db` and no `[mixxx].path` → skip Mixxx sync step

### Quick scan + sync (no analysis)

Pick up new files and push to Mixxx without running BPM/key/energy analysis:

```bash
multidj import directory --apply   # reads music_dir from config; imports new files + dedupes
multidj sync mixxx --apply         # pushes all dirty tracks + crates to Mixxx
```

> Sync pushes artist, title, album, genre, rating, and play count, and repopulates crate membership from MultiDJ. It also fills **BPM only when Mixxx has none** (NULL/0) and never overwrites an existing Mixxx BPM. Key is never written by sync.
>
> ⚠️ Writing a raw `library.bpm` value without a matching BeatGrid BLOB is a **known-unstable stopgap** — Mixxx may display it inconsistently. For BPM that Mixxx recognizes reliably, use `analyze bpm` → `analyze mixxx-blobs` (writes a real BeatGrid-2.0 BLOB).

### BPM + key analysis for tracks Mixxx doesn't have

The full pipeline handles this automatically — it imports Mixxx's existing analysis first, then only analyzes tracks with missing values. To run just these steps:

```bash
multidj import mixxx-analysis --apply   # pull Mixxx's existing BPM/key into MultiDJ
multidj analyze bpm --apply             # detect BPM for tracks still missing it
multidj analyze key --apply             # detect key for tracks still missing it
multidj analyze mixxx-blobs --apply     # write BeatGrid + KeyMap BLOBs back to Mixxx
```

`analyze bpm` and `analyze key` skip tracks that already have values — use `--force` to reanalyze everything.

## The pipeline

`multidj pipeline` chains **19 steps across 4 phases**, in order:

```
Phase 1 INGEST   import → dedupe → fix_mismatches → parse
Phase 2 ANALYZE  mixxx_import → bpm → key → mixxx_blobs → energy → embed → cues
Phase 3 ENRICH   clean_text → enrich_meta → enrich_genre → clean_genres
Phase 4 SYNC     cluster → crates → sync → report
```

```bash
multidj pipeline --apply      # execute everything
multidj pipeline              # dry-run: preview without writing
```

The pipeline is idempotent and incremental — every analyze step skips tracks it has
already processed, so it's safe to re-run daily. It takes a single backup at the start.

### Run a single phase

```bash
multidj pipeline --apply --phase ingest    # only import/dedupe/fix_mismatches/parse
multidj pipeline --apply --phase analyze   # only mixxx_import/bpm/key/mixxx_blobs/energy/embed/cues
multidj pipeline --apply --phase enrich    # only clean_text/enrich_meta/enrich_genre/clean_genres
multidj pipeline --apply --phase sync      # only cluster/crates/sync/report
```

### Other flags

```bash
multidj pipeline --apply --skip-sync   # rebuild crates without touching Mixxx

# Skip any individual step: --skip-import, --skip-bpm, --skip-key, --skip-cues,
# --skip-embed, --skip-cluster, --skip-enrich, --skip-enrich-genre, --skip-genres, etc.

# Write report to custom path
multidj pipeline --apply --report-output /path/to/report.html

# Disable report generation
multidj pipeline --skip-report
```

### Interactive Dashboard Report (auto-generated)

The pipeline now generates a standalone interactive dashboard report:

```bash
multidj pipeline --apply
```

Default output:

```text
./multidj_report.html
```

Options:

```bash
multidj pipeline --report-output /path/to/report.html
multidj pipeline --skip-report
```

The dashboard includes track counts, metadata coverage, top genres, crate exploration,
and crate track harmonic transition indicators.

Generate dashboard directly (read-only, no --apply required):

```bash
multidj report dashboard
multidj report dashboard --output /path/to/report.html
```

### Report-only run

If you want to generate only the HTML report (without running import/analyze/clean/crates/sync),
skip all pipeline processing steps and keep the report step enabled:

```bash
multidj pipeline \
    --skip-import \
    --skip-fix-mismatches \
    --skip-parse \
    --skip-bpm \
    --skip-key \
    --skip-energy \
    --skip-genres \
    --skip-clean-text \
    --skip-crates \
    --skip-sync
```

Custom output path:

```bash
multidj pipeline \
    --skip-import \
    --skip-fix-mismatches \
    --skip-parse \
    --skip-bpm \
    --skip-key \
    --skip-energy \
    --skip-genres \
    --skip-clean-text \
    --skip-crates \
    --skip-sync \
    --report-output /path/to/report.html
```

On first run, MultiDJ will ask for your music directory and save it to `~/.multidj/config.toml`.

## Configuration

`~/.multidj/config.toml` controls which crate dimensions are generated:

```toml
[pipeline]
music_dir = "~/Music/All_Tracks"   # scanned by pipeline step 1

[crates]
bpm      = true   # BPM:90-105, BPM:125-130, etc.
key      = true   # Key:1A, Key:8B, etc. (Camelot wheel)
genre    = true   # Genre:House, Genre:Techno, etc.
energy   = true   # Energy:Low, Energy:Mid, Energy:High
language = true   # Lang:Hebrew, etc.

[bpm]
min_tracks = 3    # suppress crates with fewer tracks than this

[energy]
low_max  = 0.33
high_min = 0.67

[mixxx]
path = "~/.mixxx/mixxxdb.sqlite"   # used by pipeline/sync/import when --mixxx-db is omitted
```

## Commands

### Import

```bash
multidj import mixxx [--mixxx-db PATH] [--apply] [--no-backup]
multidj import directory PATH [--apply] [--no-backup]
multidj import mixxx-analysis [--mixxx-db PATH] [--apply] [--force] [--limit N]
```

Tracks imported from any path are first-class library members — they flow through all pipeline steps regardless of where their files live.

`import mixxx-analysis` is a one-way pull of Mixxx's **own** BPM/key analysis into MultiDJ (matched by path). The pipeline runs this first in the analyze phase, so tracks Mixxx already analyzed skip MultiDJ's own BPM/key detection.

### Sync to Mixxx

```bash
multidj sync mixxx [--mixxx-db PATH] [--apply] [--no-backup]
```

Pushes dirty tracks **and** crates to Mixxx. MultiDJ is the source of truth — Mixxx is a display layer.

**Cues in Mixxx.** On sync, high-confidence intro/drop/outro cues are self-annotated into
Mixxx hot-cue slots — Intro → slot 0 (blue), Drop → slot 1 (red), Outro → slot 2 (green).
MultiDJ replaces only the slots it manages and **never deletes hot cues from Mixxx** —
running `cues clear` removes cues from the MultiDJ DB only; cues already written into Mixxx
(and any you set by hand) are preserved.

### Library health

```bash
multidj scan                     # track counts, metadata coverage, crate count
multidj scan --verbose           # also list all DB tables

multidj audit genres             # distribution, case collisions, uninformative
multidj audit genres --top 50
multidj audit metadata           # field coverage percentages

multidj backup
multidj backup --backup-dir /tmp/backups
```

### Clean & normalize

```bash
multidj clean genres             # dry-run: case variants, uninformative → NULL, whitespace
multidj clean genres --apply
multidj clean genres --limit 50

multidj clean text               # dry-run: clean artist/title/album text + remove mapped title/artist suffix garbage
multidj clean text --apply
```

### Analyze (requires `uv sync --extra analysis`)

```bash
multidj analyze bpm              # dry-run: list tracks needing BPM
multidj analyze bpm --apply      # detect BPM from start/middle/end windows; report variable-BPM tracks
multidj analyze bpm --force      # reprocess tracks that already have BPM

multidj analyze key              # dry-run: list candidates
multidj analyze key --apply      # detect key (Camelot) and write to DB
multidj analyze key --write-tags # also write audio file tags
multidj analyze key --force      # overwrite existing keys

multidj analyze energy           # dry-run: list tracks needing energy score
multidj analyze energy --apply   # detect energy (0.0–1.0) and write to DB
multidj analyze energy --force   # reprocess all tracks
```

### Mixxx pre-analysis BLOBs

Write proper BeatGrid-2.0 + KeyMap-1.0 protobuf BLOBs into the Mixxx DB so Mixxx
recognizes BPM/key reliably (the correct path for stable BPM — see the sync note above):

```bash
multidj analyze mixxx-blobs              # dry-run
multidj analyze mixxx-blobs --apply      # write BeatGrid + KeyMap BLOBs to Mixxx
multidj analyze mixxx-blobs --apply --force   # rewrite even where Mixxx already has analysis
multidj analyze mixxx-blobs --apply --lock-bpm
```

Logs `SKIPPED` (Mixxx already owns the BPM) or `WROTE` per track so the BPM/key
protection is observable.

### Cue detection (requires `uv sync --extra embeddings`)

Structural segmentation (intro/verse/chorus/drop/outro) via allin1 + librosa
cross-validation. On `sync mixxx`, high-confidence intro/drop/outro are pushed to Mixxx
hot-cue slots 0/1/2 (see "Cues in Mixxx" below).

```bash
multidj analyze cues             # dry-run
multidj analyze cues --apply     # detect and store cue_points
multidj analyze cues --force     # re-detect (manual cues are never overwritten)

multidj cues clear --apply       # remove all auto-detected cues from the MultiDJ DB
```

### Semantic embeddings (requires `uv sync --extra embeddings`)

```bash
multidj analyze embed                       # dry-run: show how many tracks need embedding
multidj analyze embed --apply               # encode with CLAP (laion/larger_clap_music, 512-dim)
multidj analyze embed --apply --model clamp3 # encode with CLaMP3 (768-dim, cross-modal)
multidj analyze embed --force               # re-encode all tracks
multidj analyze embed --limit 50            # encode at most 50 tracks this run
```

CLAP and CLaMP3 embeddings coexist per track in the `embeddings` table (composite key on `(track_id, model_name)`).

### Clustering (requires `uv sync --extra embeddings`)

```bash
multidj cluster vibe             # dry-run: show cluster count that would be found
multidj cluster vibe --apply     # UMAP+HDBSCAN → write Vibe/ auto-crates to DB
multidj cluster vibe --apply --min-cluster-size 10
```

Produces crates named `Vibe/Dark-Techno` (if LLM naming configured) or `Vibe/Cluster-01` (fallback). Noise tracks go to `Vibe/Unclassified`. LLM naming via `~/.multidj/config.toml`:

```toml
[llm]
base_url = "https://opencode.ai/api/v1"
api_key  = "your-api-key"
model    = "deepseek/deepseek-chat"
```

### Similarity search (requires `uv sync --extra embeddings`)

```bash
multidj similar "AT NIGHT DUB"     # 10 most similar tracks by cosine distance
multidj similar "Artist - Title"   # partial match on artist+title
multidj similar /path/to/file.mp3  # exact path match
multidj similar "AT NIGHT DUB" --top 20
```

### DJ next-track suggestion (requires `uv sync --extra embeddings`)

```bash
multidj suggest "Artist - Title"               # top 10 next-track candidates
multidj suggest "Artist - Title" --top 5       # show 5
multidj suggest "Artist - Title" --bpm-window 10  # tighter BPM tolerance (default: 15)
multidj suggest "Artist - Title" --any-cluster    # search whole library, not just same Vibe/ cluster
```

Ranks candidates by a composite DJ-mixing score:

```
score = 0.70 × cosine_similarity
      + 0.15 × BPM_compatibility   (linear decay to 0 at ±bpm-window BPM)
      + 0.15 × Camelot_key_compat  (1.0 = same key, 0.75 = adjacent/relative, 0.0 = clash)
```

By default results come from the same `Vibe/` cluster as the query track (run `cluster vibe --apply` first).

### Parse filenames

```bash
multidj parse                    # dry-run: propose artist/title/remixer from filenames
multidj parse --apply
multidj parse --min-confidence high
multidj parse --force            # overwrite already-tagged fields
```

### Enrich

```bash
multidj enrich language          # detect Hebrew tracks (Unicode range check)

# Metadata enrichment — requires uv sync --extra enrich
multidj enrich metadata          # dry-run: file tags → Discogs → MusicBrainz
multidj enrich metadata --apply  # write release_year, label, album, genre to DB
multidj enrich metadata --apply --write-tags  # also update audio file tags
multidj enrich metadata --force  # re-enrich already-enriched tracks
```

Configure Discogs and MusicBrainz tokens in `~/.multidj/config.toml` under `[discogs]` and `[musicbrainz]`.

**Genre hardening (pipeline `enrich_genre` step).** A layered decision tree fills each
track's genre with provenance — `file → Discogs → MusicBrainz → CLAP zero-shot` — and records
`genre_source` (`file`/`discogs`/`musicbrainz`/`clap`/`manual`) and `genre_confidence` on the
track (migration 008). It runs in the enrich phase of `multidj pipeline`. Tracks with
`genre_source = 'manual'` are never overwritten; the step is incremental (skips tracks that
already have a `genre_source` unless `--force`). Skip it with `--skip-enrich-genre`.

### Standalone scripts

These tools run outside the CLI and produce self-contained HTML files:

```bash
# Interactive UMAP scatter plot — click a track to see next-track suggestions in sidebar
python scripts/viz_library.py
python scripts/viz_library.py --out /tmp/viz.html --neighbors 10

# Data science diagnostics — 6-panel dashboard: coverage, genre, BPM, key, similarity, clusters
python scripts/diagnostics.py
python scripts/diagnostics.py --out /tmp/diag.html --sample 600

# Zero-shot genre detection using CLAP + folder heuristics
python scripts/genre_detect.py --limit 50           # dry-run
python scripts/genre_detect.py --apply              # write genres to DB
```

### Report

```bash
multidj report dashboard                 # generate interactive standalone dashboard
multidj report dashboard --output report.html
```

This command is read-only and respects `--db`.

### Harmonic Validation (experimental)

Dashboard crate views include Camelot-based transition analysis between adjacent tracks:

- ✅ compatible: same key or +/- 1 step on same A/B ring
- ⚠️ risky: relative major/minor (same number, A/B swap)
- ❌ incompatible: all other transitions

Current phase is analysis + visualization only:

- No DB schema changes
- No automatic crate mutation
- No pipeline enforcement of harmonic rules
- UI reordering/flags are local to the dashboard session (not persisted yet)

### Crates

```bash
# Auto-crate dimensions: Genre: | BPM: | Key: | Energy: | Lang:
# Crate tiers: "catch-all" (New Crate) | "auto" (prefixed) | "hand-curated"
# Hand-curated and catch-all are always protected by default.

multidj crates audit             # inventory and classification
multidj crates audit --summary  # counts only
multidj crates audit --min-tracks 10

multidj crates hide              # dry-run: hide auto crates below threshold
multidj crates hide --apply      # sets show=0 (reversible)
multidj crates hide --include-hand-curated

multidj crates show              # dry-run: restore hidden crates
multidj crates show --apply --min-tracks 5

multidj crates delete            # dry-run: permanently delete auto crates
multidj crates delete --apply

multidj crates rebuild           # dry-run: preview all auto-crates
multidj crates rebuild --apply   # delete old auto-crates, recreate from current data
multidj crates rebuild --min-tracks 10
```

`crates rebuild` generates crates for all enabled dimensions in config: Genre:, BPM:, Key: (Camelot wheel), Energy: (Low/Mid/High), Lang:. `Vibe/` crates are managed separately by `cluster vibe`.

### Deduplicate

```bash
# Keeper: most played → highest rated → largest file
# --apply uses soft-delete (deleted=1, reversible)

multidj dedupe                   # dry-run: show groups with keeper/duplicate detail
multidj dedupe --by artist-title
multidj dedupe --by filesize
multidj dedupe --apply
```

### Triage (requires `mpv`)

Fast keyboard-driven track audition via an `mpv` subprocess:

```bash
multidj triage                   # audition the whole library
multidj triage --crate "Genre:Techno"
multidj triage --limit 50
```

Keys: KP0 = soft-delete, Shift+KP0 = hard-delete, KP1–5 = rating, n = skip, ←/→ = ±30s.
Install mpv first (Fedora/RHEL: `sudo dnf install mpv`).

## Global flags

| Flag | Effect |
|------|--------|
| `--json` | Structured JSON output (accepted anywhere in the command line) |
| `--db PATH` | Override MultiDJ DB path (default: `~/.multidj/library.sqlite`) |
| `--version` | Show version |

Override DB path with env var: `MULTIDJ_DB_PATH=/path/to/library.sqlite`

## Safety model

- All commands are **dry-run by default** — nothing written without `--apply`
- Automatic timestamped backup before every write (skip with `--no-backup`)
- `pipeline --apply` creates **one backup** at the start — not once per step
- `dedupe --apply` and `crates delete --apply` use **soft-delete** (`deleted=1`) — recoverable
- Per-track error isolation in all analyze commands — one bad audio file never aborts the batch
- All stats exclude soft-deleted tracks
- `sync mixxx --apply` backs up the Mixxx DB before writing

## Source of truth

MultiDJ owns `~/.multidj/library.sqlite`. Mixxx is a sync target, not a source.

- `import mixxx` is a **one-time bootstrap** — after that, manage everything in MultiDJ
- `sync mixxx --apply` is a **one-way push** — MultiDJ → Mixxx always
- Crates created directly in Mixxx are not imported and will be overwritten on next sync
- The `sync_state` table tracks dirty flags per-track per-adapter; an AFTER UPDATE trigger on `tracks` sets `dirty=1` automatically whenever a track row changes
- `full_sync` only pushes tracks where `dirty=1 AND deleted=0` — incremental by design

## Project layout

> **`CLAUDE.md` is the authoritative architecture reference** (layered module map, schema,
> and design invariants). High-level summary:

```
multidj/
├── cli.py                    — argparse entry point, global flag hoisting, subcommand dispatch
├── pipeline.py               — run_pipeline(): 19 steps / 4 phases, single backup, step isolation
├── config.py / db.py         — config.toml load/save; connect() + migration runner
├── backup.py / utils.py      — timestamped backups; emit() JSON/human output
├── constants.py / models.py  — genre lists, prefixes, CAMELOT_KEY_MAP; LibrarySummary
├── scan.py / audit.py        — library stats; genre/metadata audits
├── clean.py / parse.py       — text+genre normalization; filename parsing
├── analyze.py                — analyze_bpm(), analyze_key(), analyze_energy()
├── embed.py / embed_clamp3.py — CLAP + CLaMP3 audio embedding backends
├── cluster.py / suggest.py   — UMAP+HDBSCAN Vibe/ crates; DJ next-track ranking
├── cues.py                   — structural cue detection (intro/drop/outro)
├── enrich.py / enrich_genre.py — file→Discogs→MusicBrainz metadata; layered genre hardening
├── mixxx_blobs.py            — BeatGrid-2.0 + KeyMap-1.0 protobuf BLOB writer
├── import_mixxx_analysis.py  — one-way pull of Mixxx's own BPM/key into MultiDJ
├── crates.py / dedupe.py     — auto-crate rebuild; duplicate detection
├── triage.py / report.py     — mpv audition; HTML dashboard
├── adapters/                 — base.py (SyncAdapter ABC), mixxx.py, directory.py
└── migrations/               — 001…008 SQL, auto-applied in order on write connections

scripts/                      — standalone HTML tools: viz_library.py, diagnostics.py, genre_detect.py
tests/                        — full suite (386 tests); fixtures build fresh SQLite DBs per test
```

## Run tests

```bash
pytest tests/ -v
pytest tests/test_pipeline.py -v   # single module
```

## DB & config locations

| Path | Purpose |
|------|---------|
| `~/.multidj/library.sqlite` | MultiDJ DB (source of truth); configurable via `multidj config set-db` |
| `~/.multidj/config.toml` | User configuration (crate dimensions, music dir, LLM API) |
| `~/.multidj/backups/` | Timestamped backups |
| `~/.mixxx/mixxxdb.sqlite` | Mixxx DB (read on import, written on sync) |
| `~/.cache/huggingface/hub/models--laion--larger_clap_music/` | CLAP model weights (~1.5 GB, downloaded once on first `analyze embed --apply`) |

## Repository Sync Note (2026-06-25)

- **Pipeline restructured into 4 phases / 19 steps** (ingest → analyze → enrich → sync), each
  independently runnable via `--phase`. `--skip-<step>` flags are available for every step.
  (Supersedes the "10 steps" / "14 steps" counts in the older notes below.)
- **Genre hardening:** new `enrich_genre` step + migration 008 (`genre_source`,
  `genre_confidence`) — layered file→Discogs→MusicBrainz→CLAP with provenance.
- **Cue → Mixxx sync:** intro/drop/outro pushed to hot-cue slots 0/1/2; MultiDJ never deletes
  Mixxx hot cues (`cues clear` clears the MultiDJ DB only).
- **BPM into Mixxx:** sync now fills `library.bpm` when Mixxx has none (a known-unstable
  stopgap). The reliable path remains `analyze bpm` → `analyze mixxx-blobs` (BeatGrid BLOB);
  `mixxx_blobs` logs per-track SKIPPED/WROTE for observability.
- **Branches consolidated:** all work lives on `dev` (mirrored to `master`).

## Repository Sync Note (2026-06-03)

- **`[mixxx]` config section added:** `~/.multidj/config.toml` now supports `[mixxx]` with a `path` key for the Mixxx DB location. `get_mixxx_db_path()` reads it. All Mixxx commands fall back to this when `--mixxx-db` is omitted.
- **CLI fallback:** `pipeline`, `sync mixxx`, `import mixxx`, and `analyze mixxx-blobs` use `args.mixxx_db or get_mixxx_db_path(cfg)` — no need to pass `--mixxx-db` if `[mixxx].path` is configured.
- **Idempotent pipeline:** All analyze steps skip already-processed tracks (WHERE field IS NULL / LEFT JOIN check). Safe to re-run daily.
- **Source-of-truth enforced via trigger:** The `sync_state` table's AFTER UPDATE trigger on `tracks` sets `dirty=1` automatically. `full_sync` pushes only `dirty=1 AND deleted=0` tracks.

## Repository Sync Note (2026-05-27)

- **Semantic embeddings:** `multidj analyze embed --apply` encodes tracks with CLAP (`laion/larger_clap_music`, 512-dim). Requires `uv sync --extra embeddings`. Embeddings stored as BLOBs in the `embeddings` table (migration 004).
- **Auto-clustering:** `multidj cluster vibe --apply` runs UMAP+HDBSCAN on the embedding matrix and writes `Vibe/` auto-crates. LLM naming via `[llm]` config; falls back to `Vibe/Cluster-NN`.
- **Similarity search:** `multidj similar <track> [--top N]` — KNN cosine-distance search in embedding space, read-only.
- **Pipeline now 14 steps:** `analyze embed` (step 8) and `cluster vibe` (step 9) added. Both skip gracefully if `[embeddings]` extra not installed. `--skip-embed` and `--skip-cluster` flags available.

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
