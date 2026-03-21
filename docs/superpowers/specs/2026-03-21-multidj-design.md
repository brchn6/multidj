# MultiDJ — Design Spec

**Date:** 2026-03-21
**Status:** Draft — awaiting implementation plan

---

## 1. What Is MultiDJ?

MultiDJ is a Python 3.9+ CLI and MCP server for DJ music library management. It maintains its own software-agnostic SQLite database as the single source of truth for track metadata, and syncs that data to DJ software (Mixxx first; Rekordbox and Serato as future adapters).

It is the generalized successor to `mixxx_multitool`. All existing commands are preserved and migrated in place — nothing breaks.

---

## 2. Objectives

1. **Own the metadata** — MultiDJ's DB is the source of truth, not Mixxx's schema or any other app's DB.
2. **DJ-first fields** — BPM, key, crates, energy, language, and track structure (intro/outro) are first-class citizens.
3. **Agent-operable** — every command produces JSON output and has a dry-run mode; an LLM can drive the full pipeline without human input.
4. **MCP server** — expose tools (`scan`, `get_tracks`, `update_track`, `sync_mixxx`, etc.) so Claude can call them natively inside a conversation.
5. **Non-destructive** — auto-backup before every write, soft-delete everywhere, `--apply` required for all mutations.
6. **Sync, don't replace** — MultiDJ never writes to Mixxx's (or any other app's) DB without an explicit `sync` command.

---

## 3. Core Principles

| Principle | Detail |
|---|---|
| **Stdlib-first** | No dependency unless it earns its weight. `mutagen` for audio tags. `librosa` optional for key. `all-in-one` (PyTorch) optional for structure analysis. |
| **Slow and additive** | Migrate `mixxx_multitool` commands in place. Don't rewrite what works. |
| **Schema stability** | MultiDJ's DB schema is versioned (`schema_version` table). Migrations run automatically on startup. |
| **Sync adapter pattern** | Each DJ software target (Mixxx, Rekordbox, Serato) is an isolated adapter module. New targets don't touch core logic. |
| **LLM-friendly output** | JSON output is always flat and predictable. No deeply nested structures. Field names are stable across versions. |

---

## 4. Database Schema

MultiDJ owns `~/.multidj/library.sqlite`. The schema is versioned and migrated on startup.

### `tracks`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `path` | TEXT UNIQUE NOT NULL | Absolute path on disk |
| `artist` | TEXT | |
| `title` | TEXT | |
| `album` | TEXT | |
| `genre` | TEXT | |
| `bpm` | REAL | |
| `key` | TEXT | Camelot notation (e.g. `11B`) or open key |
| `language` | TEXT | e.g. `hebrew`, `english` |
| `duration` | REAL | Seconds |
| `filesize` | INTEGER | Bytes |
| `rating` | INTEGER | 0–5 |
| `play_count` | INTEGER | |
| `remixer` | TEXT | |
| `energy` | REAL | 0.0–1.0; reserved for future Spotify API or audio-analysis integration — NULL until a source exists |
| `intro_end` | REAL | Seconds — from structure analysis |
| `outro_start` | REAL | Seconds — from structure analysis |
| `deleted` | INTEGER | 0/1 soft delete |
| `created_at` | TEXT | ISO8601 |
| `updated_at` | TEXT | ISO8601, updated on every write |

### `track_tags`
Freeform extensible key/value pairs. `PRIMARY KEY (track_id, key)` enforces uniqueness — upserts use `INSERT OR REPLACE`.

| Column | Type |
|---|---|
| `track_id` | INTEGER FK → tracks.id |
| `key` | TEXT |
| `value` | TEXT |

### `crates`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT UNIQUE NOT NULL | |
| `type` | TEXT | `auto` / `hand-curated` / `catch-all` |
| `show` | INTEGER | 0/1 visibility flag |

### `crate_tracks`
`PRIMARY KEY (crate_id, track_id)` — prevents duplicate assignments on repeated import/rebuild runs.

| Column | Type |
|---|---|
| `crate_id` | INTEGER FK → crates.id |
| `track_id` | INTEGER FK → tracks.id |

### `sync_state`
`PRIMARY KEY (track_id, adapter)` — one row per (track, adapter) pair. Upserts use `INSERT OR REPLACE`.

| Column | Type | Notes |
|---|---|---|
| `track_id` | INTEGER FK → tracks.id | |
| `adapter` | TEXT | e.g. `mixxx`, `rekordbox` |
| `last_synced_at` | TEXT | ISO8601 |
| `dirty` | INTEGER | 1 = track has changed since last sync |

**Dirty flag rules:**
- Any `UPDATE` on `tracks` fires an `AFTER UPDATE` trigger that sets `dirty=1` on all existing `sync_state` rows for that `track_id`.
- `multidj import mixxx` upserts `sync_state(track_id, 'mixxx', dirty=0)` — just imported, already in sync.
- `multidj import directory` inserts `sync_state(track_id, adapter, dirty=1)` for every known adapter (adapters listed in a config/constant) — new tracks need pushing.
- `multidj sync <adapter>` sets `dirty=0` on success.
- Tracks with no `sync_state` row for a given adapter are treated as dirty by `sync` (LEFT JOIN, NULL dirty treated as 1).

### `schema_version`

| Column | Type |
|---|---|
| `version` | INTEGER |

---

## 5. Schema Versioning and Migrations

Migration scripts live in `multidj/migrations/` numbered as `001_initial.sql`, `002_add_energy.sql`, etc.

On every startup, `db.py`'s `connect()` runs `_apply_migrations(conn)`:
1. Read `schema_version.version` (default 0 if table doesn't exist yet).
2. Find all `migrations/NNN_*.sql` files with `NNN > current_version`, sorted ascending.
3. Execute each in a transaction. On success, update `schema_version.version`.
4. On failure, roll back and raise — do not leave the DB in a partial state.

The migration runner lives entirely in `db.py`. No external migration tool.

---

## 6. Sync Adapter Pattern

Each DJ software target is a self-contained module at `multidj/adapters/<name>.py`.

```
multidj/adapters/
├── base.py       — SyncAdapter ABC: push_track(), full_sync(), import_all()
├── mixxx.py      — reads/writes ~/.mixxx/mixxxdb.sqlite
├── rekordbox.py  — future (XML export)
└── serato.py     — future (tag-based)
```

### Mixxx field mapping (`import mixxx`)

| Mixxx `library` column | MultiDJ `tracks` column | Notes |
|---|---|---|
| `artist` | `artist` | direct |
| `title` | `title` | direct |
| `album` | `album` | direct |
| `genre` | `genre` | direct |
| `bpm` | `bpm` | direct |
| `key_id` | `key` | FK lookup in Mixxx's `keys` table → Camelot string |
| `duration` | `duration` | direct (seconds) |
| `filesize` | `filesize` | direct (bytes) |
| `rating` | `rating` | direct (0–5) |
| `timesplayed` | `play_count` | column rename |
| `track_locations.location` | `path` | JOIN required |
| `remixer` | `remixer` | direct |

Mixxx `key_id` → Camelot: JOIN `library LEFT JOIN keys ON library.key_id = keys.id`. Mixxx's `keys` table uses `key_text` for the human-readable Camelot string (e.g. `"11B"`). Implementer must verify the column name via `PRAGMA table_info(keys)` on the live DB before coding the importer.

Fields in MultiDJ with no Mixxx equivalent (`language`, `energy`, `intro_end`, `outro_start`) are left NULL on import.

### Sync flow (`sync mixxx`)
1. Read `sync_state WHERE adapter='mixxx' AND dirty=1`.
2. For each dirty `track_id`, call `mixxx.push_track(track)` — `UPDATE library SET ... WHERE location = track.path` (Mixxx JOIN via `track_locations`).
3. Requires `--apply` to write. Dry-run prints the rows that would be pushed.
4. On success, set `sync_state.dirty=0`, update `last_synced_at`.
5. `backup.py` backs up `~/.mixxx/mixxxdb.sqlite` before any writes (same as today).

---

## 7. Backup Policy

After migration, `backup.py` backs up **MultiDJ's DB** (`~/.multidj/library.sqlite`) before every write operation. It does NOT back up Mixxx's DB automatically, except when `sync mixxx --apply` is called (which triggers a backup of `~/.mixxx/mixxxdb.sqlite` as it does today).

Backup target is resolved by the same `resolve_db_path()` function — no hardcoded path.

---

## 8. Analysis Tiers

| Tier | Command | Dependency | Output |
|---|---|---|---|
| **Light** | `multidj analyze key` | `librosa` | Camelot key → `tracks.key` |
| **Heavy** | `multidj analyze structure` | `all-in-one` (PyTorch) | `intro_end`, `outro_start`, segment JSON → `track_tags` |

Heavy tier is opt-in: `pip install multidj[deep-analysis]`. One bad file never aborts the batch (per-track error isolation).

---

## 9. LLM / MCP Integration

### Phase 1 — Agent-friendly CLI (done)
- `--json` on every command
- Dry-run by default, `--apply` to write
- Stable field names

### Phase 2 — MCP Server
`multidj mcp` starts an MCP server (stdio transport). Write tools accept a `dry_run: bool` parameter (default `true`) — same semantics as `--apply` on the CLI.

| Tool | Writable | Description |
|---|---|---|
| `scan_library` | no | Library health summary |
| `search_tracks` | no | Full-text + field filter search |
| `get_track` | no | Single track by id or path |
| `update_track` | yes | Update metadata fields; `dry_run=false` to commit |
| `list_crates` | no | All crates with track counts |
| `sync_adapter` | yes | Push dirty tracks to named adapter; `dry_run=false` to commit |
| `analyze_key` | yes | Queue key analysis for a track or batch; `dry_run=false` to run |

MCP server is built after core DB and sync adapter are stable.

---

## 10. Command Surface

### New commands
```
multidj scan                    # library health
multidj import mixxx            # one-time pull from ~/.mixxx/mixxxdb.sqlite
multidj import directory <path> # scan directory, add/update tracks in DB
multidj sync mixxx              # push dirty tracks to Mixxx (requires --apply)
multidj mcp                     # start MCP server (Phase 2)
```

### Preserved commands (from mixxx_multitool)
`parse`, `enrich`, `audit`, `clean`, `analyze key`, `crates *`, `dedupe`, `backup` — all preserved. Operate on MultiDJ's DB after migration.

### `import directory` upsert policy
- If `path` does not exist in `tracks`: insert.
- If `path` exists and `filesize` or `updated_at` has changed: update metadata fields from audio tags, set `dirty=1` for all adapters.
- If `path` exists and nothing has changed: skip (no write, no dirty flag).

---

## 11. Migration Path from mixxx_multitool

Migration is **sequential** — each phase must be complete before the next begins.

| Phase | Action | State after |
|---|---|---|
| **0 — Rename** | Source dir `mixxx_tool/` → `multidj/`. `pyproject.toml` updated: package `multidj`, entry point `multidj`. Old `mixxx-tool` entry point kept as a real setuptools alias pointing to the same `cli:main`. | Both `multidj` and `mixxx-tool` commands work. |
| **1 — New DB layer** | `db.py` updated: default DB path → `~/.multidj/library.sqlite`, schema init + migration runner added. The `--db` flag and `MULTIJ_DB_PATH` env var override the path. During Phases 1–2, command modules that have not yet been ported still pass an explicit `db_path=mixxx_db_path` at their call sites — they do not go through the new default. No dual-path logic in `db.py` itself. | `multidj` opens MultiDJ DB for ported modules. Unported modules still use Mixxx DB explicitly. |
| **2 — Import** | `multidj import mixxx` implemented. User runs it once to populate MultiDJ DB from Mixxx. | MultiDJ DB has all tracks. |
| **3 — Port commands** | Command modules updated one by one to query `tracks` instead of Mixxx's `library`. Each module is independently switchable. | Commands operate on MultiDJ DB. |
| **4 — Sync adapter** | `mixxx.py` adapter implemented. `multidj sync mixxx --apply` pushes dirty tracks. | Full round-trip: import → edit in MultiDJ → sync to Mixxx. |
| **5 — Remove alias** | `mixxx-tool` entry point removed after user confirms transition complete. | Only `multidj` remains. |

During Phases 1–2, any command that reads from or writes to MultiDJ's `tracks` table checks `SELECT COUNT(*) FROM tracks` and raises `"MultiDJ DB is empty. Run 'multidj import mixxx' first."` if the table is empty. Exempt from this guard: `backup`, `import mixxx`, `import directory` (these are the bootstrap commands).

---

## 12. Future Sync Targets

| Target | Priority | Notes |
|---|---|---|
| Rekordbox | Medium | XML export well-documented; DB format reverse-engineered by community |
| Serato | Low | Tag-based (writes to audio file tags + hidden `_Serato_` folders) |
| Traktor | Low | NML XML format |

All follow the same `SyncAdapter` ABC — adding a new one doesn't touch core logic.

---

## 13. Download → Organize Workflow

MultiDJ is designed to be the **second step** in a download-then-organize pipeline. A separate download CLI (currently in early development) fetches tracks from the web (YouTube, SoundCloud, Beatport, etc.) and drops raw files into a staging directory — the "dump dir" (e.g. `~/Music/Downloads/`). MultiDJ then takes ownership:

```
[download-cli fetch <url>]
        │
        ▼
~/Music/Downloads/          ← dump dir: raw, unorganized files
        │
        ▼
multidj import directory ~/Music/Downloads/
        │  parse filenames → artist/title/remixer
        │  detect language
        │  add to tracks DB (dirty=1 for all adapters)
        ▼
multidj analyze key --apply
        │  Camelot key detection
        ▼
multidj crates rebuild --apply
        │  regenerate Genre:/Lang: crates
        ▼
multidj organize --apply    ← (future) move files to ~/Music/All_Tracks/
        │
        ▼
multidj sync mixxx --apply  ← push to Mixxx
```

**Key design decisions for this workflow:**
- The dump dir is treated as **input-only** — MultiDJ reads from it but does not mutate files there
- `multidj organize` (future command) moves files from dump dir to the canonical music dir (`~/Music/All_Tracks/`) and updates `tracks.path`
- The download CLI and MultiDJ are **decoupled** — the download CLI does not need to know about MultiDJ's DB; it just writes files to a directory

---

## 14. What We Are NOT Building

- Real-time playback or mixing
- A GUI
- A replacement for Mixxx/Rekordbox as performance software
- Beets integration (beets ↔ Mixxx sync is lossy; we stay independent)
