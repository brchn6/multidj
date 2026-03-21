# Mixxx Multitool

A safe, modular CLI for batch tag management on large Mixxx libraries.
Designed for agent-operated workflows: JSON output, dry-run defaults, per-track error isolation, and automatic backups before every write.

## Commands

```bash
python -m mixxx_tool --help

# Library summary (active tracks only — excludes soft-deleted)
python -m mixxx_tool scan
python -m mixxx_tool scan --json

# Audit
python -m mixxx_tool audit genres                # genre distribution, collisions, suspicious values
python -m mixxx_tool audit genres --top 50
python -m mixxx_tool audit metadata --json       # field coverage percentages

# Backup
python -m mixxx_tool backup
python -m mixxx_tool backup --backup-dir /tmp/backups

# Clean genres (case variants → canonical, uninformative → NULL, whitespace)
python -m mixxx_tool clean genres                # dry-run
python -m mixxx_tool clean genres --apply        # write changes (backup created first)
python -m mixxx_tool clean genres --limit 50     # cap changes
python -m mixxx_tool clean genres --json         # structured output

# Clean text (strip / collapse whitespace in artist, title, album)
python -m mixxx_tool clean text
python -m mixxx_tool clean text --apply

# Parse artist/title from filenames
python -m mixxx_tool parse                           # dry-run: propose changes
python -m mixxx_tool parse --apply                   # write to DB (backup created first)
python -m mixxx_tool parse --min-confidence high     # only high-confidence changes
python -m mixxx_tool parse --force                   # overwrite already-tagged fields
python -m mixxx_tool parse --limit 20 --json         # structured output

# Enrich tracks with metadata from external signals
python -m mixxx_tool enrich language                 # detect Hebrew tracks (Unicode range check)
python -m mixxx_tool enrich language --json

# Detect and tag musical key (requires: pip install librosa mutagen)
python -m mixxx_tool analyze key --limit 5              # dry-run, list candidates
python -m mixxx_tool analyze key --limit 5 --apply      # detect + sync to DB
python -m mixxx_tool analyze key --apply --write-tags   # also write audio file tags
python -m mixxx_tool analyze key --apply --no-sync-db   # tags only, skip DB
python -m mixxx_tool analyze key --apply --force        # overwrite existing keys

# Crate management
# Types: "catch-all" (New Crate), "auto" (Genre: X / BPM: X), "hand-curated" (everything else)
# Hand-curated and catch-all crates are always protected by default.
python -m mixxx_tool crates audit --summary              # counts only
python -m mixxx_tool crates audit                        # full crate lists
python -m mixxx_tool crates audit --min-tracks 10        # custom threshold

python -m mixxx_tool crates hide                         # dry-run: hide auto crates <5 tracks
python -m mixxx_tool crates hide --apply                 # hide them (sets show=0, reversible)
python -m mixxx_tool crates hide --include-hand-curated  # also hide hand-curated small crates

python -m mixxx_tool crates show                         # dry-run: restore all hidden crates
python -m mixxx_tool crates show --apply --min-tracks 5  # restore only crates now at >=5 tracks

python -m mixxx_tool crates delete                       # dry-run: permanently delete auto crates <5 tracks
python -m mixxx_tool crates delete --apply               # delete (removes crate + track assignments)

python -m mixxx_tool crates rebuild                      # dry-run: preview Genre: + Lang: crates to create
python -m mixxx_tool crates rebuild --apply              # delete old auto-crates, create fresh ones
python -m mixxx_tool crates rebuild --min-tracks 10      # only create crates with >=10 tracks

# Find and remove duplicate tracks
# Keeper chosen by: most played → highest rated → largest file
# --apply sets mixxx_deleted=1 (soft delete, reversible)
python -m mixxx_tool dedupe                    # dry-run: full groups with keeper/duplicate detail
python -m mixxx_tool dedupe --json
python -m mixxx_tool dedupe --by artist-title  # match on artist+title only
python -m mixxx_tool dedupe --by filesize      # match on filesize+duration only
python -m mixxx_tool dedupe --apply            # remove duplicates
```

## Global flags

| Flag | Effect |
|------|--------|
| `--json` | Structured JSON output (works before or after subcommand) |
| `--db /path` | Override database path |

## Safety model

- All commands are **dry-run by default** — nothing is written without `--apply`
- Write operations create a timestamped backup in `~/.mixxx/backups/` first (skip with `--no-backup`)
- Scan, audit, and `dedupe` (without `--apply`) are always read-only
- `dedupe --apply` and `crates delete --apply` use soft-delete — data is recoverable
- Per-track error isolation: one bad file does not abort the batch
- `analyze key` dry-run lists candidates without loading audio (no deps needed)
- All stats exclude soft-deleted tracks (`mixxx_deleted = 1`)

## Dependencies

Core commands: **Python 3.9+ stdlib only**

Key analysis (`analyze key --apply`):
```bash
pip install librosa mutagen
```

## Project layout

```
mixxx_tool/
├── constants.py   — shared UNINFORMATIVE_GENRES, regex patterns, crate classifiers
├── db.py          — connect(), resolve_db_path(), table_exists()
├── backup.py      — create_backup()
├── models.py      — LibrarySummary
├── utils.py       — emit() for JSON / human output
├── scan.py        — scan_library()
├── audit.py       — audit_genres(), audit_metadata()
├── clean.py       — clean_genres(), clean_text()
├── analyze.py     — analyze_key(), detect_key(), _write_tag()
├── parse.py       — parse_filename(), parse_library()
├── enrich.py      — is_hebrew(), enrich_language()
├── crates.py      — audit_crates(), hide_crates(), show_crates(), delete_crates(), rebuild_crates()
├── dedupe.py      — dedupe()
└── cli.py         — argument parsing and command routing
```

## Default DB location

`~/.mixxx/mixxxdb.sqlite`

Override with `--db /path/to/mixxxdb.sqlite` or `MIXXX_DB_PATH=/path/to/mixxxdb.sqlite`.
