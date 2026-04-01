# MultiDJ

A software-agnostic DJ music library manager. Maintains its own SQLite database as the source of truth for track metadata, syncs to DJ software (Mixxx first; Rekordbox and Serato as future adapters).

Designed for agent-operated workflows: JSON output, dry-run defaults, per-track error isolation, and automatic backups before every write. Eventually exposed as an MCP server so AI agents can drive your full library pipeline natively.

## Install

```bash
pip install -e .
multidj --help
```

## Quick start

```bash
# One-time: import your Mixxx library into MultiDJ
multidj import mixxx            # dry-run: preview what would be imported
multidj import mixxx --apply    # write to ~/.multidj/library.sqlite

# Library health
multidj scan
multidj scan --json

# Push changes back to Mixxx after editing metadata in MultiDJ
multidj sync mixxx              # dry-run: show dirty tracks
multidj sync mixxx --apply      # push to Mixxx
```

## Commands

### Import & sync

```bash
multidj import mixxx [--mixxx-db PATH] [--apply] [--no-backup]
multidj sync mixxx   [--mixxx-db PATH] [--apply] [--no-backup]
```

### Library operations

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

multidj clean text               # dry-run: strip/collapse whitespace in artist/title/album
multidj clean text --apply
```

### Enrich & analyze

```bash
multidj enrich language          # detect Hebrew tracks (Unicode range check)

# Key detection requires: pip install librosa mutagen
multidj analyze key --limit 5   # dry-run: list candidates
multidj analyze key --apply      # detect + write to DB
multidj analyze key --apply --write-tags   # also write audio file tags
multidj analyze key --apply --force        # overwrite existing keys
```

### Parse filenames

```bash
multidj parse                    # dry-run: propose artist/title/remixer from filenames
multidj parse --apply
multidj parse --min-confidence high
multidj parse --force            # overwrite already-tagged fields
```

### Crates

```bash
# Crate tiers: "catch-all" (New Crate) | "auto" (Genre:/Lang: prefix) | "hand-curated"
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

multidj crates rebuild           # dry-run: preview Genre:/Lang: crates
multidj crates rebuild --apply   # delete old auto-crates, recreate from current data
multidj crates rebuild --min-tracks 10
```

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
- `dedupe --apply` and `crates delete --apply` use **soft-delete** (`deleted=1`) — recoverable
- Per-track error isolation in `analyze key` — one bad audio file never aborts the batch
- All stats exclude soft-deleted tracks
- `sync mixxx --apply` backs up the Mixxx DB before writing

## Dependencies

Core: **Python 3.9+ stdlib only**

Key analysis (`analyze key --apply`):
```bash
pip install librosa mutagen
```

## Project layout

```
multidj/
├── cli.py              — argparse entry point, global flag hoisting, subcommand dispatch
├── db.py               — connect(), resolve_db_path(), migration runner
├── backup.py           — create_backup() — timestamped copies to ~/.multidj/backups/
├── models.py           — LibrarySummary dataclass
├── utils.py            — emit() for JSON / human output
├── constants.py        — UNINFORMATIVE_GENRES, AUTO_CRATE_PREFIXES, regex patterns, KNOWN_ADAPTERS
├── scan.py             — scan_library()
├── audit.py            — audit_genres(), audit_metadata()
├── clean.py            — clean_genres(), clean_text()
├── analyze.py          — analyze_key(), detect_key()
├── parse.py            — parse_filename(), parse_library()
├── enrich.py           — is_hebrew(), enrich_language()
├── crates.py           — audit/hide/show/delete/rebuild_crates()
├── dedupe.py           — dedupe()
├── adapters/
│   ├── base.py         — SyncAdapter ABC
│   └── mixxx.py        — MixxxAdapter: import_all(), push_track(), full_sync()
└── migrations/
    └── 001_initial.sql — MultiDJ schema v1

tests/
├── conftest.py         — shared pytest fixtures
├── fixtures/
│   ├── data.py         — canonical TRACKS/CRATES test data (10 tracks)
│   ├── mixxx_factory.py
│   └── multidj_factory.py
├── test_import.py
├── test_sync.py
├── test_scan.py
├── test_audit.py
├── test_clean.py
├── test_parse.py
├── test_enrich.py
├── test_crates.py
├── test_dedupe.py
├── test_analyze.py
└── test_safety.py      — cross-cutting dry-run/apply/backup invariants
```

## Run tests

```bash
pytest tests/ -v
pytest tests/test_import.py -v   # single module
```

## DB locations

| Path | Purpose |
|------|---------|
| `~/.multidj/library.sqlite` | MultiDJ DB (source of truth) |
| `~/.mixxx/mixxxdb.sqlite` | Mixxx DB (read on import, written on sync) |
| `~/.multidj/backups/` | Timestamped backups |
