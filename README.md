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

Optional semantic embeddings, clustering, and similarity search:
```bash
uv sync --extra embeddings
```

The `embeddings` extra adds CLAP audio encoding, UMAP+HDBSCAN clustering, and the `multidj similar` command. It includes `librosa`, `torch`, `transformers`, `umap-learn`, `hdbscan`, and `openai`. The CLAP model weights (~1.5 GB) are downloaded on first use to `~/.cache/huggingface/hub/`.

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

```bash
# Full pipeline: import new tracks → clean → analyze → rebuild crates → sync to Mixxx
# (--music-dir and --mixxx-db come from ~/.multidj/config.toml automatically)
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

## The pipeline

`multidj pipeline` chains all steps in order:

```
import directory → fix mismatches → parse → dedupe
→ analyze bpm → analyze key → analyze energy → analyze embed → cluster vibe
→ clean genres → clean text → crates rebuild → sync mixxx → report
```

```bash
multidj pipeline --apply      # execute everything
multidj pipeline              # dry-run: preview without writing

# Skip individual steps
multidj pipeline --apply --skip-bpm --skip-key
multidj pipeline --apply --skip-sync   # rebuild crates without touching Mixxx

# Write report to custom path
multidj pipeline --apply --report-output /path/to/report.html

# Disable report generation
multidj pipeline --skip-report



# Basic pipeline run (directory import + BPM/key/energy + crates + Mixxx sync, no embeddings/clustering)
multidj pipeline --apply \
  --music-dir '/home/barc/Weizmann Institute Dropbox/Bar Cohen/Music/' \
  --mixxx-db '/home/barc/Weizmann Institute Dropbox/Bar Cohen/Music/mixxx/mixxxdb.sqlite' \
  --skip-embed \
  --skip-cluster


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
```

Tracks imported from any path are first-class library members — they flow through all pipeline steps regardless of where their files live.

### Sync to Mixxx

```bash
multidj sync mixxx [--mixxx-db PATH] [--apply] [--no-backup]
```

Pushes dirty tracks **and** crates to Mixxx. MultiDJ is the source of truth — Mixxx is a display layer.

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

### Semantic embeddings (requires `uv sync --extra embeddings`)

```bash
multidj analyze embed            # dry-run: show how many tracks need embedding
multidj analyze embed --apply    # encode all un-embedded tracks with CLAP (laion/larger_clap_music)
multidj analyze embed --force    # re-encode all tracks (overwrite existing embeddings)
multidj analyze embed --limit 50 # encode at most 50 tracks this run
```

### Clustering (requires `uv sync --extra embeddings`)

```bash
multidj cluster vibe             # dry-run: show cluster count that would be found
multidj cluster vibe --apply     # UMAP+HDBSCAN → write Vibe/ auto-crates to DB
multidj cluster vibe --apply --min-cluster-size 10
```

Produces crates named `Vibe/Dark-Techno` (if LLM naming configured) or `Vibe/Cluster-01` (fallback). Noise tracks go to `Vibe/Unclassified`. LLM naming is configured via `~/.multidj/config.toml`:

```toml
[llm]
base_url = "https://opencode.ai/api/v1"
api_key  = "your-api-key"
model    = "deepseek/deepseek-chat"
```

### Similarity search (requires `uv sync --extra embeddings`)

```bash
multidj similar "AT NIGHT DUB"     # find the 10 most similar tracks by cosine distance
multidj similar "Artist - Title"   # partial match on artist+title
multidj similar /path/to/file.mp3  # exact path match
multidj similar "AT NIGHT DUB" --top 20
```

Returns tracks ranked by cosine distance in CLAP embedding space (0 = identical, 1 = maximally different). Track must have been embedded first with `analyze embed --apply`.

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

```
multidj/
├── cli.py              — argparse entry point, global flag hoisting, subcommand dispatch
├── config.py           — load/save ~/.multidj/config.toml, first-run prompt
├── pipeline.py         — run_pipeline(): chains 11 steps including HTML report, single backup, step isolation
├── report.py           — write_html_report(): static HTML dashboard from active track metrics
├── db.py               — connect(), resolve_db_path(), migration runner
├── backup.py           — create_backup() — timestamped copies to ~/.multidj/backups/
├── models.py           — LibrarySummary dataclass
├── utils.py            — emit() for JSON / human output
├── constants.py        — genres, prefixes, regex patterns, CAMELOT_KEY_MAP, BPM_RANGES
├── scan.py             — scan_library()
├── audit.py            — audit_genres(), audit_metadata()
├── clean.py            — clean_genres(), clean_text()
├── analyze.py          — analyze_bpm(), analyze_key(), analyze_energy(), detect_*()
├── parse.py            — parse_filename(), parse_library()
├── enrich.py           — is_hebrew(), enrich_language()
├── crates.py           — audit/hide/show/delete/rebuild_crates() with config-driven dimensions
├── dedupe.py           — dedupe()
├── embed.py            — CLAP audio encoding: analyze_embed(), find_similar(), store_embedding(), load_embeddings_from_db()
├── cluster.py          — UMAP+HDBSCAN clustering: cluster_vibe(), cluster_embeddings(), name_cluster()
├── adapters/
│   ├── base.py         — SyncAdapter ABC
│   ├── mixxx.py        — MixxxAdapter: import_all(), push_track(), full_sync() + crate sync
│   └── directory.py    — DirectoryAdapter: import tracks from filesystem paths
└── migrations/
    ├── 001_initial.sql — MultiDJ schema v1
    ├── 002_cue_points.sql — cue_points table
    ├── 003_*.sql       — additional schema updates
    └── 004_embeddings.sql — embeddings table (track_id PK, model_name, vector BLOB, created_at)

tests/
├── conftest.py
├── fixtures/
│   ├── data.py              — canonical TRACKS/CRATES test data (10 tracks)
│   ├── mixxx_factory.py
│   └── multidj_factory.py
├── test_import.py
├── test_import_directory.py
├── test_sync.py
├── test_scan.py
├── test_audit.py
├── test_clean.py
├── test_parse.py
├── test_enrich.py
├── test_crates.py
├── test_dedupe.py
├── test_analyze.py
├── test_analyze_energy.py
├── test_config.py
├── test_mixxx_crate_sync.py
├── test_pipeline.py
└── test_safety.py
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
