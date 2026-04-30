# MultiDJ Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `mixxx_multitool` into `MultiDJ` — a software-agnostic DJ library manager with its own SQLite DB, sync adapter pattern, and `import` / `sync` commands — while keeping all existing commands functional at every phase boundary.

**Architecture:** Incremental 5-phase migration: rename package → new DB layer with schema init + migration runner → `import mixxx` + `import directory` commands → port existing commands to MultiDJ's `tracks` schema → Mixxx sync adapter. MCP server is deferred to a later plan.

**Tech Stack:** Python 3.9+ stdlib only. `mutagen` for audio tag reads in `import directory`. No new core deps.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `mixxx_tool/` → `multidj/` | **Rename** | Package directory (git mv) |
| `multidj/db.py` | **Rewrite** | New default path, schema init, migration runner, `ensure_not_empty()` |
| `multidj/migrations/001_initial.sql` | **Create** | Full DDL: all tables + dirty-flag trigger |
| `multidj/backup.py` | **Modify** | Update DEFAULT_BACKUP_DIR to `~/.multidj/backups` |
| `multidj/constants.py` | **Modify** | Add `KNOWN_ADAPTERS` (`MIXXX_DB_PATH` lives in `db.py`) |
| `multidj/adapters/__init__.py` | **Create** | Package init |
| `multidj/adapters/base.py` | **Create** | `SyncAdapter` ABC |
| `multidj/adapters/mixxx.py` | **Create** | `import_all()` (read Mixxx → write MultiDJ) + `push_track()` (write Mixxx ← MultiDJ) |
| `multidj/importer.py` | **Create** | `scan_directory()` — mutagen tag read + upsert into `tracks` |
| `multidj/cli.py` | **Modify** | prog name, `import` + `sync` subcommands, remove hardcoded Mixxx table checks |
| `multidj/scan.py` | **Modify** | `library` → `tracks`, `mixxx_deleted` → `deleted` |
| `multidj/audit.py` | **Modify** | `library` → `tracks`, `mixxx_deleted` → `deleted`, remove `track_locations` JOIN |
| `multidj/enrich.py` | **Modify** | `library` → `tracks`, `mixxx_deleted` → `deleted` |
| `multidj/parse.py` | **Modify** | `library` → `tracks`, `mixxx_deleted` → `deleted`, `track_locations` JOIN removed |
| `multidj/clean.py` | **Modify** | `library` → `tracks`, `mixxx_deleted` → `deleted` |
| `multidj/crates.py` | **Modify** | `library` → `tracks`, `mixxx_deleted` → `deleted` |
| `multidj/dedupe.py` | **Modify** | `library` → `tracks`, `timesplayed` → `play_count`, remove `track_locations` JOIN |
| `multidj/analyze.py` | **Modify** | `library` → `tracks`, `mixxx_deleted` → `deleted` |
| `pyproject.toml` | **Modify** | Package name `multidj`, entry points, packages.find |

**Parallelism note:** Tasks 5, 6, 7 (port read-only / write / complex commands) are parallel-safe — they touch different module files and non-overlapping sections of `cli.py`.

---

## Task 1: Phase 0 — Package rename

**Files:**
- Rename: `mixxx_tool/` → `multidj/`
- Modify: `pyproject.toml`
- Modify: `multidj/cli.py` (prog name only)

- [ ] **Rename the package directory**

```bash
git mv mixxx_tool multidj
```

- [ ] **Update `pyproject.toml`**

Replace the entire file content:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "multidj"
version = "0.2.0"
description = "MultiDJ — software-agnostic DJ music library manager with SQLite, LLM integration, and multi-app sync"
readme = "README.md"
requires-python = ">=3.9"

[project.scripts]
multidj = "multidj.cli:main"
mixxx-tool = "multidj.cli:main"

[tool.setuptools]
package-dir = {"" = "."}

[tool.setuptools.packages.find]
where = ["."]
include = ["multidj*"]
```

- [ ] **Update `prog` name in `multidj/cli.py`**

In `build_parser()`, change:
```python
# Before
prog="mixxx-tool",
description="Mixxx database multitool — batch tag management for large libraries.",

# After
prog="multidj",
description="MultiDJ — DJ music library manager with SQLite, LLM integration, and multi-app sync.",
```

- [ ] **Reinstall the package**

```bash
pip install -e .
```

- [ ] **Verify both entry points work**

```bash
multidj --version
mixxx-tool --version
```

Expected: both print `multidj 0.2.0`

- [ ] **Commit**

```bash
git add -A
git commit -m "feat: rename package mixxx_tool → multidj, add multidj entry point"
```

---

## Task 2: Phase 1 — New DB layer

**Files:**
- Rewrite: `multidj/db.py`
- Create: `multidj/migrations/001_initial.sql`
- Modify: `multidj/backup.py`
- Modify: `multidj/constants.py`

- [ ] **Create migrations directory**

```bash
mkdir -p multidj/migrations
touch multidj/migrations/__init__.py
```

- [ ] **Write `multidj/migrations/001_initial.sql`**

```sql
-- 001_initial.sql — MultiDJ full schema

CREATE TABLE IF NOT EXISTS tracks (
    id          INTEGER PRIMARY KEY,
    path        TEXT    UNIQUE NOT NULL,
    artist      TEXT,
    title       TEXT,
    album       TEXT,
    genre       TEXT,
    bpm         REAL,
    key         TEXT,
    language    TEXT,
    duration    REAL,
    filesize    INTEGER,
    rating      INTEGER,
    play_count  INTEGER,
    remixer     TEXT,
    energy      REAL,
    intro_end   REAL,
    outro_start REAL,
    deleted     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS track_tags (
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    key         TEXT    NOT NULL,
    value       TEXT,
    PRIMARY KEY (track_id, key)
);

CREATE TABLE IF NOT EXISTS crates (
    id      INTEGER PRIMARY KEY,
    name    TEXT    UNIQUE NOT NULL,
    type    TEXT    NOT NULL DEFAULT 'hand-curated',
    show    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS crate_tracks (
    crate_id    INTEGER NOT NULL REFERENCES crates(id)  ON DELETE CASCADE,
    track_id    INTEGER NOT NULL REFERENCES tracks(id)  ON DELETE CASCADE,
    PRIMARY KEY (crate_id, track_id)
);

CREATE TABLE IF NOT EXISTS sync_state (
    track_id        INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    adapter         TEXT    NOT NULL,
    last_synced_at  TEXT,
    dirty           INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (track_id, adapter)
);

-- Dirty flag trigger: any UPDATE to tracks marks all sync_state rows dirty
CREATE TRIGGER IF NOT EXISTS tracks_set_dirty
AFTER UPDATE ON tracks
FOR EACH ROW
BEGIN
    UPDATE sync_state SET dirty = 1 WHERE track_id = OLD.id;
END;
```

- [ ] **Rewrite `multidj/db.py`**

```python
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path("~/.multidj/library.sqlite").expanduser()
MIXXX_DB_PATH   = Path("~/.mixxx/mixxxdb.sqlite").expanduser()


def resolve_db_path(db_path: str | None = None) -> Path:
    candidate = db_path or os.environ.get("MULTIDJ_DB_PATH")
    return Path(candidate).expanduser() if candidate else DEFAULT_DB_PATH


def ensure_db_exists(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")


def ensure_not_empty(conn: sqlite3.Connection) -> None:
    """Raise if MultiDJ tracks table is empty (user hasn't run import yet)."""
    if not table_exists(conn, "tracks"):
        raise RuntimeError("MultiDJ DB is empty. Run 'multidj import mixxx' first.")
    row = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()
    if int(row[0]) == 0:
        raise RuntimeError("MultiDJ DB is empty. Run 'multidj import mixxx' first.")


def _apply_migrations(conn: sqlite3.Connection) -> None:
    migrations_dir = Path(__file__).parent / "migrations"
    # Bootstrap schema_version
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version VALUES (0)")
        conn.commit()
        current = 0
    else:
        current = int(row[0])

    for script in sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.sql")):
        n = int(script.name[:3])
        if n <= current:
            continue
        sql = script.read_text()
        try:
            # executescript() handles multi-statement SQL including triggers
            # with semicolons inside BEGIN...END bodies. It auto-commits, but
            # SQLite DDL cannot be rolled back anyway. Protection: schema_version
            # is only updated if the script succeeds without raising.
            conn.executescript(sql)
            conn.execute("UPDATE schema_version SET version = ?", (n,))
            conn.commit()
        except Exception as exc:
            raise RuntimeError(f"Migration {script.name} failed: {exc}") from exc


@contextmanager
def connect(db_path: str | None = None, readonly: bool = True) -> Iterator[sqlite3.Connection]:
    path = resolve_db_path(db_path)

    if readonly:
        ensure_db_exists(path)
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        _apply_migrations(conn)

    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None
```

- [ ] **Update `multidj/backup.py`** — change default backup dir

```python
# Before
DEFAULT_BACKUP_DIR = Path("~/.mixxx/backups").expanduser()

# After
DEFAULT_BACKUP_DIR = Path("~/.multidj/backups").expanduser()
```

- [ ] **Add `KNOWN_ADAPTERS` to `multidj/constants.py`**

Append to the bottom of constants.py:

```python
# Adapters registered in the sync_state table.
# import directory inserts dirty=1 rows for every adapter in this list.
KNOWN_ADAPTERS: tuple[str, ...] = ("mixxx",)
```

Note: `MIXXX_DB_PATH` is defined in `db.py` (not here). Adapters import it via `from ..db import MIXXX_DB_PATH`.

- [ ] **Verify schema creation**

```bash
python -c "
from multidj.db import connect
with connect(readonly=False) as conn:
    tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
    print('Tables:', tables)
"
```

Expected output includes: `tracks`, `track_tags`, `crates`, `crate_tracks`, `sync_state`, `schema_version`

- [ ] **Commit**

```bash
git add multidj/db.py multidj/migrations/ multidj/backup.py multidj/constants.py
git commit -m "feat: new MultiDJ DB layer — own sqlite path, migration runner, schema v1"
```

---

## Task 3: Phase 2a — SyncAdapter base + `import mixxx`

**Files:**
- Create: `multidj/adapters/__init__.py`
- Create: `multidj/adapters/base.py`
- Create: `multidj/adapters/mixxx.py` (import_all only — push_track added in Task 8)
- Modify: `multidj/cli.py`

- [ ] **Inspect the Mixxx `keys` table column name**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('$HOME/.mixxx/mixxxdb.sqlite')
print([r[1] for r in conn.execute('PRAGMA table_info(keys)').fetchall()])
conn.close()
"
```

Record the actual column name for the Camelot string. It is expected to be `key_text` — if it differs, adjust the SQL in the next step accordingly.

- [ ] **Create `multidj/adapters/__init__.py`**

```python
from .base import SyncAdapter

__all__ = ["SyncAdapter"]
```

- [ ] **Create `multidj/adapters/base.py`**

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SyncAdapter(ABC):
    """Base class for all DJ software sync adapters."""

    name: str  # e.g. "mixxx"

    @abstractmethod
    def import_all(self, multidj_db_path: str | None = None) -> dict[str, Any]:
        """
        Pull all tracks from the external app into MultiDJ's tracks table.
        Returns a summary dict: {imported, skipped, errors}.
        Sets sync_state dirty=0 for all imported tracks.
        """

    @abstractmethod
    def push_track(self, track: dict[str, Any], conn: Any) -> None:
        """
        Write a single MultiDJ track row into the external app's DB.
        conn is a writable sqlite3.Connection to the external DB.
        """

    @abstractmethod
    def full_sync(self, multidj_db_path: str | None = None) -> dict[str, Any]:
        """
        Push all dirty tracks to the external app.
        Returns a summary dict: {pushed, skipped, errors}.
        """
```

- [ ] **Create `multidj/adapters/mixxx.py`** (import side only)

```python
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..constants import KNOWN_ADAPTERS
from ..db import connect as multidj_connect, table_exists, MIXXX_DB_PATH
from .base import SyncAdapter


class MixxxAdapter(SyncAdapter):
    name = "mixxx"

    def import_all(self, multidj_db_path: str | None = None) -> dict[str, Any]:
        mixxx_path = MIXXX_DB_PATH
        if not mixxx_path.exists():
            raise FileNotFoundError(f"Mixxx database not found: {mixxx_path}")

        mixxx_conn = sqlite3.connect(str(mixxx_path))
        mixxx_conn.row_factory = sqlite3.Row

        # Verify key column name (expected: key_text)
        key_cols = [r[1] for r in mixxx_conn.execute("PRAGMA table_info(keys)").fetchall()]
        key_col = "key_text" if "key_text" in key_cols else (key_cols[1] if len(key_cols) > 1 else None)

        rows = mixxx_conn.execute(f"""
            SELECT
                l.artist, l.title, l.album, l.genre, l.bpm,
                {f"k.{key_col}" if key_col else "NULL"} AS camelot_key,
                l.duration, l.remixer, l.rating,
                l.timesplayed AS play_count,
                tl.filesize,
                tl.location AS path
            FROM library l
            LEFT JOIN track_locations tl ON l.location = tl.id
            {"LEFT JOIN keys k ON l.key_id = k.id" if key_col else ""}
            WHERE l.mixxx_deleted = 0
              AND tl.location IS NOT NULL
        """).fetchall()
        mixxx_conn.close()

        imported = skipped = errors = 0
        now = datetime.now().isoformat(timespec="seconds")

        with multidj_connect(multidj_db_path, readonly=False) as conn:
            for row in rows:
                try:
                    conn.execute("""
                        INSERT INTO tracks
                            (path, artist, title, album, genre, bpm, key, duration,
                             filesize, rating, play_count, remixer, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(path) DO UPDATE SET
                            artist=excluded.artist, title=excluded.title,
                            album=excluded.album, genre=excluded.genre,
                            bpm=excluded.bpm, key=excluded.key,
                            duration=excluded.duration, filesize=excluded.filesize,
                            rating=excluded.rating, play_count=excluded.play_count,
                            remixer=excluded.remixer, updated_at=excluded.updated_at
                    """, (
                        row["path"], row["artist"], row["title"], row["album"],
                        row["genre"], row["bpm"], row["camelot_key"], row["duration"],
                        row["filesize"], row["rating"], row["play_count"], row["remixer"],
                        now, now,
                    ))
                    track_id = conn.execute(
                        "SELECT id FROM tracks WHERE path = ?", (row["path"],)
                    ).fetchone()["id"]
                    # Mark as clean for mixxx (just imported = in sync)
                    conn.execute("""
                        INSERT OR REPLACE INTO sync_state (track_id, adapter, last_synced_at, dirty)
                        VALUES (?, 'mixxx', ?, 0)
                    """, (track_id, now))
                    imported += 1
                except Exception:
                    errors += 1
            conn.commit()

        return {"imported": imported, "skipped": skipped, "errors": errors}

    def push_track(self, track: dict[str, Any], conn: Any) -> None:
        raise NotImplementedError("push_track implemented in Task 8")

    def full_sync(self, multidj_db_path: str | None = None) -> dict[str, Any]:
        raise NotImplementedError("full_sync implemented in Task 8")
```

- [ ] **Add `import` subcommand to `multidj/cli.py`**

After the existing imports at the top, add:
```python
from .adapters.mixxx import MixxxAdapter
```

In `build_parser()`, after the `scan` subparser block, add:
```python
# ── import ────────────────────────────────────────────────────────────────
import_p = sub.add_parser("import", help="Import tracks into MultiDJ DB")
import_sub = import_p.add_subparsers(dest="import_target", required=True)

import_sub.add_parser("mixxx", help="One-time import from ~/.mixxx/mixxxdb.sqlite")

p = import_sub.add_parser("directory", help="Scan a directory and add/update tracks")
p.add_argument("path", help="Directory to scan")
p.add_argument("--no-backup", action="store_true", help="Skip backup before write")
```

In `main()`, add the dispatch after the `scan` branch:
```python
elif args.command == "import":
    if args.import_target == "mixxx":
        result = MixxxAdapter().import_all(args.db)
    elif args.import_target == "directory":
        from .importer import scan_directory
        result = scan_directory(args.path, db_path=args.db)
```

- [ ] **Verify `import mixxx` dry-run (actually runs — no --apply needed for import)**

```bash
multidj import mixxx
```

Expected: `{"imported": 1844, "skipped": 0, "errors": 0}` (or similar counts from your library)

```bash
multidj scan
```

Expected: shows track counts from MultiDJ's own DB (same counts as before)

- [ ] **Commit**

```bash
git add multidj/adapters/ multidj/cli.py
git commit -m "feat: SyncAdapter ABC + import mixxx command (Phase 2a)"
```

---

## Task 4: Phase 2b — `import directory` command

**Files:**
- Create: `multidj/importer.py`

**Prerequisite:** Task 3 complete (`import` subcommand wired in cli.py)

- [ ] **Verify mutagen is available**

```bash
python -c "import mutagen; print(mutagen.version_string)"
```

If not installed: `pip install mutagen`

- [ ] **Create `multidj/importer.py`**

```python
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import KNOWN_ADAPTERS
from .db import connect

AUDIO_EXTENSIONS = frozenset({".mp3", ".flac", ".opus", ".ogg", ".wav", ".aiff", ".m4a", ".aac"})


def _read_tags(path: Path) -> dict[str, Any]:
    """Read audio file tags via mutagen. Returns empty dict on failure."""
    try:
        from mutagen import File as MutagenFile
        f = MutagenFile(str(path), easy=True)
        if f is None:
            return {}
        def first(key: str) -> str | None:
            vals = f.get(key)
            return str(vals[0]) if vals else None
        return {
            "artist":    first("artist"),
            "title":     first("title"),
            "album":     first("album"),
            "genre":     first("genre"),
            "bpm":       float(first("bpm")) if first("bpm") else None,
            "duration":  getattr(f.info, "length", None),
            "filesize":  path.stat().st_size,
        }
    except Exception:
        return {"filesize": path.stat().st_size if path.exists() else None}


def scan_directory(
    directory: str,
    db_path: str | None = None,
) -> dict[str, Any]:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    audio_files = [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    ]

    inserted = updated = skipped = errors = 0
    now = datetime.now().isoformat(timespec="seconds")

    with connect(db_path, readonly=False) as conn:
        for path in audio_files:
            path_str = str(path)
            try:
                existing = conn.execute(
                    "SELECT id, filesize, updated_at FROM tracks WHERE path = ?",
                    (path_str,),
                ).fetchone()

                current_filesize = path.stat().st_size

                if existing is None:
                    # New track — insert
                    tags = _read_tags(path)
                    conn.execute("""
                        INSERT INTO tracks
                            (path, artist, title, album, genre, bpm, duration,
                             filesize, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (
                        path_str,
                        tags.get("artist"), tags.get("title"), tags.get("album"),
                        tags.get("genre"), tags.get("bpm"), tags.get("duration"),
                        tags.get("filesize"), now, now,
                    ))
                    track_id = conn.execute(
                        "SELECT id FROM tracks WHERE path = ?", (path_str,)
                    ).fetchone()["id"]
                    # New tracks are dirty for all known adapters
                    for adapter in KNOWN_ADAPTERS:
                        conn.execute("""
                            INSERT OR REPLACE INTO sync_state (track_id, adapter, dirty)
                            VALUES (?, ?, 1)
                        """, (track_id, adapter))
                    inserted += 1

                elif int(existing["filesize"] or 0) != current_filesize:
                    # Changed — update
                    tags = _read_tags(path)
                    conn.execute("""
                        UPDATE tracks SET
                            artist=?, title=?, album=?, genre=?, bpm=?,
                            duration=?, filesize=?, updated_at=?
                        WHERE id=?
                    """, (
                        tags.get("artist"), tags.get("title"), tags.get("album"),
                        tags.get("genre"), tags.get("bpm"), tags.get("duration"),
                        tags.get("filesize"), now, existing["id"],
                    ))
                    # Trigger handles dirty=1 in sync_state
                    updated += 1

                else:
                    skipped += 1

            except Exception:
                errors += 1

        conn.commit()

    return {
        "directory": str(root),
        "audio_files_found": len(audio_files),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }
```

- [ ] **Verify `import directory`**

```bash
multidj import directory ~/Music/All_Tracks --json | python -m json.tool | head -20
```

Expected: JSON with `inserted`, `updated`, `skipped` counts. `skipped` should be high if tracks were already imported via `import mixxx`.

- [ ] **Commit**

```bash
git add multidj/importer.py
git commit -m "feat: import directory command — mutagen tag read, upsert + dirty flag"
```

---

## Task 5: Phase 3a — Port read-only commands (scan, audit, enrich)

**Parallel-safe with Tasks 6 and 7.**

**Files:**
- Modify: `multidj/scan.py`
- Modify: `multidj/audit.py`
- Modify: `multidj/enrich.py`
- Modify: `multidj/cli.py` (remove `table_exists(conn, "library")` guards in these commands)

**The mechanical change in every module (applies to Tasks 5, 6, and 7):**
- `"mixxx_deleted = 0"` → `"deleted = 0"` (update the `_ACTIVE` constant)
- `FROM library` → `FROM tracks`
- Remove `LEFT JOIN track_locations tl ON l.location = tl.id` — path is on `tracks` directly
- `if not table_exists(conn, "library"):` → call `ensure_not_empty(conn)` instead
- For modules that have NO existing `table_exists` guard (e.g. `parse.py`, `clean.py`, `crates.py`, `dedupe.py`, `analyze.py`): add `ensure_not_empty(conn)` as the first call inside the `with connect(...)` block
- Add `ensure_not_empty` to the import: `from .db import connect, table_exists, ensure_not_empty`

- [ ] **Update `multidj/scan.py`**

```python
# Change at top:
from .db import connect, table_exists, ensure_not_empty

# Change _ACTIVE:
_ACTIVE = "deleted = 0"

# In scan_library():
# Change:  if not table_exists(conn, "library"):
# To:      ensure_not_empty(conn)

# Change every:  FROM library  →  FROM tracks
```

- [ ] **Update `multidj/audit.py`**

```python
# Change at top:
from .db import connect, table_exists, ensure_not_empty

# Change _ACTIVE:
_ACTIVE = "deleted = 0"

# In audit_genres():
# Change:  if not table_exists(conn, "library"):
# To:      ensure_not_empty(conn)

# Change every:  FROM library  →  FROM tracks

# In audit_metadata(): same pattern
```

- [ ] **Update `multidj/enrich.py`**

```python
# At top, add ensure_not_empty to import:
from .db import connect, ensure_not_empty

# Change _ACTIVE (or equivalent filter):
# "mixxx_deleted = 0"  →  "deleted = 0"

# Change every:  FROM library  →  FROM tracks
# Remove any track_locations JOIN if present

# Inside with connect(...) as conn: block, add as first call:
ensure_not_empty(conn)
```

- [ ] **Verify scan works against MultiDJ DB**

```bash
multidj scan
```

Expected: same track counts as before, now reading from `~/.multidj/library.sqlite`

- [ ] **Verify audit works**

```bash
multidj audit genres --top 5
multidj audit metadata --json
```

Expected: same genre distribution as before

- [ ] **Verify enrich works**

```bash
multidj enrich language
```

Expected: same Hebrew track count as before (~271 tracks)

- [ ] **Commit**

```bash
git add multidj/scan.py multidj/audit.py multidj/enrich.py
git commit -m "feat: port scan, audit, enrich to MultiDJ tracks schema (Phase 3a)"
```

---

## Task 6: Phase 3b — Port write commands (parse, clean)

**Parallel-safe with Tasks 5 and 7.**

**Files:**
- Modify: `multidj/parse.py`
- Modify: `multidj/clean.py`

Same mechanical changes as Task 5, plus column differences:

- `timesplayed` does not appear in parse/clean — no column rename needed
- `track_locations.location` — parse.py uses file paths; change query to use `tracks.path` directly (no JOIN needed)

- [ ] **Update `multidj/parse.py`**

```python
# At top, add ensure_not_empty to import:
from .db import connect, ensure_not_empty

# _ACTIVE:
_ACTIVE = "deleted = 0"

# In parse_library() read phase: replace
#   FROM library l LEFT JOIN track_locations tl ON l.location = tl.id
# with:
#   FROM tracks
# And replace tl.location with t.path (or just path)

# Columns to select: id, artist, title, path (replaces filepath from tl.location)
# All UPDATE statements: UPDATE tracks SET ... WHERE id = ?

# Inside the with connect(...) as conn: read block, add as first call:
ensure_not_empty(conn)
```

- [ ] **Update `multidj/clean.py`**

```python
# At top, add ensure_not_empty to import:
from .db import connect, ensure_not_empty

# _ACTIVE:
_ACTIVE = "deleted = 0"

# FROM library → FROM tracks
# UPDATE library → UPDATE tracks
# No track_locations JOIN in clean.py

# Inside with connect(...) as conn: block in both clean_genres() and clean_text(),
# add as first call:
ensure_not_empty(conn)
```

- [ ] **Verify parse works**

```bash
multidj parse --limit 5
```

Expected: shows proposed changes with confidence levels, filenames from `tracks.path`

- [ ] **Verify clean genres works**

```bash
multidj clean genres
```

Expected: shows genre changes (dry-run), same count as before

- [ ] **Commit**

```bash
git add multidj/parse.py multidj/clean.py
git commit -m "feat: port parse, clean to MultiDJ tracks schema (Phase 3b)"
```

---

## Task 7: Phase 3c — Port complex commands (crates, dedupe, analyze key)

**Parallel-safe with Tasks 5 and 6.**

**Files:**
- Modify: `multidj/crates.py`
- Modify: `multidj/dedupe.py`
- Modify: `multidj/analyze.py`

- [ ] **Update `multidj/crates.py`**

```python
# At top, add ensure_not_empty to import:
from .db import connect, ensure_not_empty

# _ACTIVE (if present):
_ACTIVE = "deleted = 0"

# FROM library → FROM tracks
# UPDATE library → UPDATE tracks (soft delete: mixxx_deleted=1 → deleted=1)
# No track_locations JOIN needed

# In rebuild_crates(): table crates and crate_tracks are the same name —
# no change needed for those. Only the library→tracks reference changes.

# Inside every with connect(...) as conn: block, add as first call:
ensure_not_empty(conn)
```

- [ ] **Update `multidj/dedupe.py`**

Key changes:
```python
# At top, add ensure_not_empty to import:
from .db import connect, ensure_not_empty

# _keeper_sort_key: timesplayed → play_count
def _keeper_sort_key(track: dict) -> tuple:
    return (
        -(track["play_count"] or 0),   # was timesplayed
        -(track["rating"] or 0),
        -(track["filesize"] or 0),
    )

# In _find_groups():
# Replace:
#   FROM library l LEFT JOIN track_locations tl ON l.location = tl.id
#   l.timesplayed, tl.location AS filepath, tl.filesize
# With:
#   FROM tracks
#   play_count, path AS filepath, filesize
# WHERE mixxx_deleted = 0 → WHERE deleted = 0

# Soft-delete: UPDATE library SET mixxx_deleted=1 → UPDATE tracks SET deleted=1

# Inside the with connect(...) as conn: blocks, add as first call:
ensure_not_empty(conn)
```

- [ ] **Update `multidj/analyze.py`**

```python
# At top, add ensure_not_empty to import:
from .db import connect, ensure_not_empty

# _ACTIVE or equivalent filter:
# "mixxx_deleted = 0" → "deleted = 0"

# FROM library → FROM tracks
# track_locations JOIN → tracks.path directly

# UPDATE library SET key → UPDATE tracks SET key
# (key column name is the same in both schemas)

# Inside the with connect(...) as conn: blocks, add as first call:
ensure_not_empty(conn)
```

- [ ] **Verify crates works**

```bash
multidj crates audit --summary
multidj crates rebuild
```

Expected: crate counts from MultiDJ's own crates table (populated during `import mixxx` — note: import does NOT import crates currently, they start empty; rebuild creates them from track genres)

- [ ] **Verify dedupe works**

```bash
multidj dedupe
```

Expected: same duplicate groups as before

- [ ] **Verify analyze key dry-run works**

```bash
multidj analyze key --limit 3
```

Expected: lists 3 candidate tracks with paths from `tracks.path`

- [ ] **Commit**

```bash
git add multidj/crates.py multidj/dedupe.py multidj/analyze.py
git commit -m "feat: port crates, dedupe, analyze key to MultiDJ tracks schema (Phase 3c)"
```

---

## Task 8: Phase 4 — Sync adapter: push to Mixxx

**Files:**
- Modify: `multidj/adapters/mixxx.py` (add `push_track`, `full_sync`)
- Modify: `multidj/cli.py` (add `sync` subcommand)

- [ ] **Add `push_track` and `full_sync` to `multidj/adapters/mixxx.py`**

Add these methods to the `MixxxAdapter` class (replacing the `raise NotImplementedError` stubs):

```python
def push_track(self, track: dict[str, Any], mixxx_conn: sqlite3.Connection) -> None:
    """Write one MultiDJ track back to Mixxx's library table."""
    mixxx_conn.execute("""
        UPDATE library SET
            artist    = ?,
            title     = ?,
            album     = ?,
            genre     = ?,
            bpm       = ?,
            rating    = ?,
            timesplayed = ?
        WHERE id IN (
            SELECT l.id FROM library l
            JOIN track_locations tl ON l.location = tl.id
            WHERE tl.location = ?
        )
    """, (
        track["artist"], track["title"], track["album"], track["genre"],
        track["bpm"], track["rating"], track["play_count"], track["path"],
    ))
    # Note: key is NOT pushed back — Mixxx uses key_id (FK), pushing text key
    # would require a reverse lookup. Omitted for now; add in a future task.

def full_sync(
    self,
    multidj_db_path: str | None = None,
    apply: bool = False,
    backup_fn: Any = None,
) -> dict[str, Any]:
    from ..db import connect as multidj_connect

    with multidj_connect(multidj_db_path, readonly=True) as conn:
        dirty = conn.execute("""
            SELECT t.* FROM tracks t
            LEFT JOIN sync_state ss ON t.id = ss.track_id AND ss.adapter = 'mixxx'
            WHERE t.deleted = 0
              AND (ss.dirty = 1 OR ss.dirty IS NULL)
        """).fetchall()

    dirty_list = [dict(row) for row in dirty]

    if not apply:
        return {
            "mode": "dry-run",
            "dirty_tracks": len(dirty_list),
            "tracks": [{"id": t["id"], "path": t["path"], "artist": t["artist"], "title": t["title"]}
                       for t in dirty_list[:50]],
        }

    # Apply: backup Mixxx DB first, then push
    if backup_fn:
        backup_fn(str(MIXXX_DB_PATH))

    mixxx_conn = sqlite3.connect(str(MIXXX_DB_PATH))
    now = datetime.now().isoformat(timespec="seconds")
    pushed = errors = 0

    try:
        for track in dirty_list:
            try:
                self.push_track(track, mixxx_conn)
                pushed += 1
            except Exception:
                errors += 1
        mixxx_conn.commit()
    finally:
        mixxx_conn.close()

    # Update sync_state
    with multidj_connect(multidj_db_path, readonly=False) as conn:
        conn.executemany("""
            INSERT OR REPLACE INTO sync_state (track_id, adapter, last_synced_at, dirty)
            VALUES (?, 'mixxx', ?, 0)
        """, [(t["id"], now) for t in dirty_list])
        conn.commit()

    return {"mode": "apply", "pushed": pushed, "errors": errors}
```

- [ ] **Add `sync` subcommand to `multidj/cli.py`**

In `build_parser()`, add after the `import` subparser block:
```python
# ── sync ──────────────────────────────────────────────────────────────────
sync_p = sub.add_parser("sync", help="Push dirty tracks to a DJ software adapter")
sync_sub = sync_p.add_subparsers(dest="sync_target", required=True)

p = sync_sub.add_parser("mixxx", help="Push changes to Mixxx (~/.mixxx/mixxxdb.sqlite)")
p.add_argument("--apply",     action="store_true", help="Write to Mixxx DB (default: dry-run)")
p.add_argument("--no-backup", action="store_true", help="Skip Mixxx DB backup before apply")
```

In `main()`, add dispatch:
```python
elif args.command == "sync":
    if args.sync_target == "mixxx":
        backup_fn = None
        if args.apply and not args.no_backup:
            from .backup import create_backup
            from .db import MIXXX_DB_PATH
            backup_fn = lambda path: create_backup(path)
        result = MixxxAdapter().full_sync(
            multidj_db_path=args.db,
            apply=args.apply,
            backup_fn=backup_fn,
        )
```

- [ ] **Verify sync dry-run shows dirty tracks**

```bash
multidj sync mixxx
```

Expected: `{"mode": "dry-run", "dirty_tracks": 0, ...}` — should be 0 if you ran `import mixxx` (all tracks clean)

- [ ] **Make a test change and verify it marks dirty**

```bash
python -c "
from multidj.db import connect
with connect(readonly=False) as conn:
    conn.execute(\"UPDATE tracks SET genre = genre WHERE id = 1\")
    conn.commit()
"
multidj sync mixxx
```

Expected: `dirty_tracks: 1`

- [ ] **Commit**

```bash
git add multidj/adapters/mixxx.py multidj/cli.py
git commit -m "feat: Mixxx sync adapter push + sync mixxx command (Phase 4)"
```

---

## Task 9: Phase 5 — Final cleanup

**Files:**
- Modify: `multidj/cli.py` (remove any remaining `mixxx_deleted` / `library` references in error messages)
- Modify: `CLAUDE.md` (reflect current state)
- Modify: `docs/superpowers/specs/2026-03-21-multidj-design.md` (mark Status: Implemented)

- [ ] **Verify no Mixxx-schema references remain in command modules**

```bash
grep -rn "mixxx_deleted\|FROM library\|track_locations\|timesplayed" multidj/ --include="*.py"
```

Expected: zero matches (only `adapters/mixxx.py` should reference Mixxx schema)

- [ ] **Run the full command smoke-test**

```bash
multidj scan
multidj audit genres --top 5
multidj audit metadata
multidj enrich language
multidj parse --limit 3
multidj clean genres
multidj crates audit --summary
multidj dedupe --by artist-title
multidj sync mixxx
multidj backup
```

All commands should complete without errors and show data from `~/.multidj/library.sqlite`.

- [ ] **Update spec status**

In `docs/superpowers/specs/2026-03-21-multidj-design.md`, change:
```
**Status:** Draft — awaiting implementation plan
```
to:
```
**Status:** Implemented — Phase 0–4 complete. MCP server deferred to next plan.
```

- [ ] **Update `CLAUDE.md`**

Update the project overview to remove the "(Currently still running against the Mixxx DB)" note and replace with the new state.

- [ ] **Final commit**

```bash
git add -A
git commit -m "feat: MultiDJ migration complete — Phases 0-4, own DB, Mixxx sync adapter"
```

---

## Crate Import Note

`import mixxx` does **not** currently import crates from Mixxx. Crates in MultiDJ start empty after import. Run `multidj crates rebuild --apply` after import to regenerate Genre:/Lang: crates from track metadata. Hand-curated Mixxx crates are not migrated — this is intentional (they will diverge as metadata improves). A future `import mixxx --with-crates` flag can be added.

## Not In This Plan

- MCP server (`multidj mcp`) — deferred to a separate plan
- `multidj analyze structure` (all-in-one/PyTorch heavy tier) — deferred
- `multidj organize` (move files from dump dir to All_Tracks) — deferred
- Rekordbox / Serato adapters — deferred
- Removing the `mixxx-tool` alias — do this only after confirming daily workflow runs on `multidj`

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
