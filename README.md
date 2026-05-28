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

The `analysis` extra enables BPM, key, and energy detection in analyze commands.

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

# 2. (Optional) Store your Mixxx DB path in config so --mixxx-db is automatic
multidj config set-db ~/.multidj/library.sqlite   # only needed if you want a non-default path
```

If you don't use Mixxx, import directly from your music folder:

```bash
multidj import directory ~/Music/All_Tracks --apply
```

## Daily workflow

```bash
# Full pipeline: import new tracks → clean → analyze → rebuild crates → sync to Mixxx
multidj pipeline --apply \
  --music-dir ~/Music/All_Tracks \
  --mixxx-db ~/.mixxx/mixxxdb.sqlite

# Dry-run first to preview what would change
multidj pipeline \
  --music-dir ~/Music/All_Tracks \
  --mixxx-db ~/.mixxx/mixxxdb.sqlite
```

`--music-dir` and `--mixxx-db` are both optional:
- Omit `--music-dir` to skip the directory import step
- Omit `--mixxx-db` to skip the Mixxx sync step

## The pipeline

`multidj pipeline` chains all steps in order:

```
import directory → fix mismatches → parse → dedupe
→ analyze bpm → analyze key → analyze energy
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

`crates rebuild` generates crates for all enabled dimensions in config: Genre:, BPM:, Key: (Camelot wheel), Energy: (Low/Mid/High), Lang:.

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
├── adapters/
│   ├── base.py         — SyncAdapter ABC
│   ├── mixxx.py        — MixxxAdapter: import_all(), push_track(), full_sync() + crate sync
│   └── directory.py    — DirectoryAdapter: import tracks from filesystem paths
└── migrations/
    ├── 001_initial.sql — MultiDJ schema v1
    └── 002_cue_points.sql — cue_points table

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
| `~/.multidj/library.sqlite` | MultiDJ DB (source of truth) |
| `~/.multidj/config.toml` | User configuration (crate dimensions, music dir) |
| `~/.multidj/backups/` | Timestamped backups |
| `~/.mixxx/mixxxdb.sqlite` | Mixxx DB (read on import, written on sync) |

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
