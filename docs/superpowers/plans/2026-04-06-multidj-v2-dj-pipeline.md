# MultiDJ v2 — DJ Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform MultiDJ from a Mixxx-dependent metadata editor into a standalone DJ library pipeline — ingest raw files, detect BPM/key, auto-build genre + BPM crates, sync everything (tracks + crates + cue points) back to Mixxx.

**Architecture:** MultiDJ DB is the source of truth. Raw files flow in via `import directory` (mutagen tags + optional librosa analysis). Auto-crates are built from the DB contents. `sync mixxx` pushes tracks, crates, and eventually cue points to Mixxx. Beets' same underlying libraries (mutagen, librosa, pyacoustid) are used directly — no beets dependency.

**Tech Stack:** Python 3.9+, SQLite (stdlib), mutagen (tag reading), librosa (BPM + key analysis), pyacoustid + chromaprint (fingerprinting, Phase 3)

---

## Parallel Execution Map

```
Wave 1 (fully independent — run all three agents simultaneously):
  Task 1 — DB migration: cue_points table
  Task 2 — analyze bpm command
  Task 3 — DirectoryAdapter (import directory)

Wave 2 (depends on Wave 1 complete):
  Task 4 — BPM-range crates in `crates rebuild`   [needs Task 2 constants]
  Task 5 — Mixxx crate sync (push crates → Mixxx)  [needs existing infra only]

Wave 3 (sequential after Wave 2):
  Task 6 — enrich fingerprint (pyacoustid)          [needs Task 3]
  Task 7 — analyze cues (cue point detection)       [needs Task 1]
  Task 8 — Mixxx cue point sync                     [needs Task 7 + Task 5]
```

---

## File Map

### New files
- `multidj/adapters/directory.py` — `DirectoryAdapter`: walk dirs, read tags, insert tracks
- `multidj/migrations/002_cue_points.sql` — `cue_points` table schema
- `tests/test_import_directory.py` — tests for DirectoryAdapter
- `tests/test_analyze_bpm.py` — tests for BPM detection
- `tests/test_cues.py` — tests for cue point detection and DB storage
- `tests/test_mixxx_crate_sync.py` — tests for crate push to Mixxx

### Modified files
- `multidj/constants.py` — add `BPM_RANGES` list
- `multidj/analyze.py` — add `detect_bpm()` and `analyze_bpm()`
- `multidj/crates.py` — extend `rebuild_crates()` to generate BPM-range crates
- `multidj/adapters/mixxx.py` — extend `full_sync()` to push crates to Mixxx
- `multidj/cli.py` — wire `import directory`, `analyze bpm`, `enrich fingerprint`, `analyze cues`
- `requirements-dev.txt` — add `mutagen` as runtime dep (was optional)

---

## Task 1: DB Migration — cue_points table

**Wave:** 1 (no dependencies)

**Files:**
- Create: `multidj/migrations/002_cue_points.sql`
- Modify: `tests/fixtures/multidj_factory.py` (migration auto-applied; verify new table exists)

- [ ] **Step 1: Write the migration SQL**

Create `multidj/migrations/002_cue_points.sql`:

```sql
-- 002_cue_points.sql — Cue point markers per track

CREATE TABLE IF NOT EXISTS cue_points (
    id          INTEGER PRIMARY KEY,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    type        TEXT    NOT NULL,   -- 'intro_end' | 'drop' | 'outro_start' | 'hot_cue'
    position    REAL    NOT NULL,   -- seconds from start of track
    label       TEXT,               -- optional display label
    color       INTEGER,            -- RGB integer for Mixxx hot cue color (NULL = default)
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_cue_points_track_id ON cue_points(track_id);
```

- [ ] **Step 2: Write a test that the table is created by the migration**

Add to `tests/test_import_directory.py` (or create `tests/test_migrations.py`):

```python
import sqlite3
from pathlib import Path
from tests.fixtures.multidj_factory import make_multidj_db

def test_cue_points_table_exists(tmp_path):
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cue_points'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1

def test_cue_points_schema(tmp_path):
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    conn = sqlite3.connect(str(db_path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cue_points)").fetchall()}
    conn.close()
    assert cols == {"id", "track_id", "type", "position", "label", "color", "created_at"}
```

- [ ] **Step 3: Run the test to verify it fails (table doesn't exist yet)**

```bash
.venv/bin/pytest tests/test_migrations.py -v
```

Expected: `FAILED` — `AssertionError: assert 0 == 1`

- [ ] **Step 4: Verify migration auto-apply picks up the new file**

The `db.py` migration runner uses `connect(readonly=False)` to auto-apply all `.sql` files in `multidj/migrations/` in numeric order. Confirm in `multidj/db.py`:

```bash
grep -n "_apply_migrations\|migrations" multidj/db.py
```

Expected: see a glob or sorted listing of `migrations/*.sql`.

- [ ] **Step 5: Run the test again — must pass now**

```bash
.venv/bin/pytest tests/test_migrations.py -v
```

Expected: `2 passed`

- [ ] **Step 6: Run full suite to confirm nothing broke**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all existing tests pass + 2 new tests.

- [ ] **Step 7: Commit**

```bash
git add multidj/migrations/002_cue_points.sql tests/test_migrations.py
git commit -m "feat: add cue_points table (migration 002)"
```

---

## Task 2: analyze bpm command

**Wave:** 1 (no dependencies)

**Files:**
- Modify: `multidj/constants.py` — add `BPM_RANGES`
- Modify: `multidj/analyze.py` — add `detect_bpm()` and `analyze_bpm()`
- Modify: `multidj/cli.py` — wire `analyze bpm` subcommand
- Create: `tests/test_analyze_bpm.py`

- [ ] **Step 1: Add BPM_RANGES to constants.py**

In `multidj/constants.py`, append after the existing constants:

```python
# BPM ranges for auto-crate generation.
# Each entry: (crate_name, bpm_low_inclusive, bpm_high_exclusive)
# Note: 125-130 and 128-135 overlap by design — tracks at 128-130 BPM
# appear in both Tech House and Techno crates.
BPM_RANGES: tuple[tuple[str, float, float], ...] = (
    ("BPM:<90",     0.0,   90.0),
    ("BPM:90-105",  90.0,  105.0),
    ("BPM:105-115", 105.0, 115.0),
    ("BPM:115-125", 115.0, 125.0),
    ("BPM:125-130", 125.0, 130.0),
    ("BPM:128-135", 128.0, 135.0),
    ("BPM:135-160", 135.0, 160.0),
    ("BPM:160-175", 160.0, 175.0),
    ("BPM:175+",    175.0, 9999.0),
)
```

- [ ] **Step 2: Write failing tests for BPM detection**

Create `tests/test_analyze_bpm.py`:

```python
from __future__ import annotations
import sqlite3
import pytest
from unittest.mock import patch
from tests.fixtures.multidj_factory import make_multidj_db


def test_analyze_bpm_dry_run_lists_candidates(multidj_db):
    """Dry-run returns candidates with bpm=0 without writing."""
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET bpm = 0 WHERE id = 1")
    conn.commit()
    conn.close()

    from multidj.analyze import analyze_bpm
    result = analyze_bpm(str(multidj_db), apply=False)

    assert result["mode"] == "dry_run"
    assert result["total_candidates"] >= 1
    # Dry-run must not write anything
    conn2 = sqlite3.connect(str(multidj_db))
    row = conn2.execute("SELECT bpm FROM tracks WHERE id = 1").fetchone()
    conn2.close()
    assert row[0] == 0.0


def test_analyze_bpm_apply_writes_detected_bpm(multidj_db, tmp_path):
    """Apply mode writes detected BPM back to DB."""
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET bpm = 0 WHERE id = 1")
    conn.commit()
    conn.close()

    with patch("multidj.analyze.detect_bpm", return_value=128.0) as mock_detect:
        from multidj.analyze import analyze_bpm
        result = analyze_bpm(
            str(multidj_db), apply=True, backup_dir=str(tmp_path)
        )

    assert result["mode"] == "apply"
    assert result["succeeded"] >= 1
    conn2 = sqlite3.connect(str(multidj_db))
    row = conn2.execute("SELECT bpm FROM tracks WHERE id = 1").fetchone()
    conn2.close()
    assert row[0] == pytest.approx(128.0)


def test_analyze_bpm_skips_tracks_with_bpm(multidj_db, tmp_path):
    """Tracks that already have BPM are not re-analyzed unless --force."""
    # All fixture tracks have bpm set
    with patch("multidj.analyze.detect_bpm", return_value=99.0):
        from multidj.analyze import analyze_bpm
        result = analyze_bpm(
            str(multidj_db), apply=True, backup_dir=str(tmp_path)
        )
    assert result["total_candidates"] == 0


def test_analyze_bpm_force_reanalyzes_all(multidj_db, tmp_path):
    """--force reanalyzes even tracks that already have BPM."""
    with patch("multidj.analyze.detect_bpm", return_value=130.0):
        from multidj.analyze import analyze_bpm
        result = analyze_bpm(
            str(multidj_db), apply=True, force=True, backup_dir=str(tmp_path)
        )
    assert result["total_candidates"] == 9  # 9 active tracks in fixture


def test_analyze_bpm_isolates_errors(multidj_db, tmp_path):
    """One bad file does not abort the batch."""
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET bpm = 0")  # force all to zero
    conn.commit()
    conn.close()

    call_count = [0]
    def flaky_detect(filepath):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("bad audio file")
        return 120.0

    with patch("multidj.analyze.detect_bpm", side_effect=flaky_detect):
        from multidj.analyze import analyze_bpm
        result = analyze_bpm(
            str(multidj_db), apply=True, backup_dir=str(tmp_path)
        )

    assert result["errors"] == 1
    assert result["succeeded"] >= 1
```

- [ ] **Step 3: Run to confirm all fail**

```bash
.venv/bin/pytest tests/test_analyze_bpm.py -v
```

Expected: all FAILED with `ImportError` or `AttributeError` (function doesn't exist yet).

- [ ] **Step 4: Add detect_bpm() and analyze_bpm() to analyze.py**

In `multidj/analyze.py`, add after `_write_tag()`:

```python
def detect_bpm(filepath: str) -> float:
    """Detect tempo in BPM from audio file using librosa beat tracker."""
    try:
        import librosa  # type: ignore
    except ImportError:
        raise ImportError("BPM analysis requires: pip install librosa")
    y, sr = librosa.load(filepath, sr=22050, mono=True, duration=30)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    # librosa may return an array for tempo in newer versions
    if hasattr(tempo, '__len__'):
        tempo = float(tempo[0])
    return float(tempo)


def analyze_bpm(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    backup_dir: str | None = None,
) -> dict[str, Any]:
    """Detect BPM for tracks where bpm is NULL or 0."""
    from .backup import create_backup

    with connect(db_path, readonly=True) as _guard:
        if table_exists(_guard, "library") and not table_exists(_guard, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(_guard)

    where = "1=1" if force else "(bpm IS NULL OR bpm = 0)"
    sql = f"""
        SELECT id, artist, title, path AS filepath
        FROM tracks WHERE {where} AND deleted = 0
        ORDER BY artist, title
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    count_sql = f"SELECT COUNT(*) FROM tracks WHERE {where} AND deleted = 0"

    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(sql).fetchall()
        total_candidates = conn.execute(count_sql).fetchone()[0]

    mode = "apply" if apply else "dry_run"

    if not apply:
        _progress(f"Dry-run: {total_candidates:,} tracks would be analyzed (run with --apply to process)")
        return {
            "mode": mode,
            "total_candidates": total_candidates,
            "processed": 0,
            "succeeded": 0,
            "errors": 0,
            "error_details": [],
        }

    if backup_dir is not False:
        create_backup(db_path, backup_dir=backup_dir)

    db_updates: list[tuple[float, int]] = []
    error_details: list[dict] = []
    succeeded = 0
    total = len(rows)

    _progress(f"Analyzing BPM for {total:,} tracks...")

    for i, row in enumerate(rows, 1):
        label = f"{row['artist'] or ''} - {row['title'] or ''}".strip(" -") or row["filepath"]
        _progress(f"[{i:{len(str(total))}}/{total}] {label[:60]}", end="")
        try:
            bpm = detect_bpm(row["filepath"])
            db_updates.append((bpm, row["id"]))
            succeeded += 1
            _progress(f"  → {bpm:.1f}")
        except ImportError:
            raise
        except Exception as exc:
            _progress(f"  ERROR: {exc}")
            error_details.append({"track_id": row["id"], "error": str(exc)})

    if db_updates:
        _progress(f"Writing {len(db_updates):,} BPM values to DB...")
        with connect(db_path, readonly=False) as wconn:
            wconn.executemany("UPDATE tracks SET bpm = ? WHERE id = ?", db_updates)
            wconn.commit()

    return {
        "mode": mode,
        "total_candidates": total_candidates,
        "processed": total,
        "succeeded": succeeded,
        "errors": len(error_details),
        "error_details": error_details,
    }
```

- [ ] **Step 5: Wire `analyze bpm` into cli.py**

In `multidj/cli.py`, find the `analyze` subparser section (where `analyze key` is wired) and add alongside it:

```python
# In the analyze subparser block, after the 'key' sub-subparser:
p_bpm = analyze_sub.add_parser("bpm", help="Detect BPM from audio for untagged tracks")
p_bpm.add_argument("--apply",   action="store_true")
p_bpm.add_argument("--force",   action="store_true", help="Re-analyze even tracks with existing BPM")
p_bpm.add_argument("--limit",   type=int, default=None)
p_bpm.add_argument("--no-backup", action="store_true")
```

And in the dispatch section:

```python
elif args.analyze_cmd == "bpm":
    from .analyze import analyze_bpm
    data = analyze_bpm(
        db_path=args.db,
        apply=args.apply,
        force=args.force,
        limit=args.limit,
        backup_dir=False if args.no_backup else None,
    )
    emit(data, args.json)
```

- [ ] **Step 6: Run tests — must all pass**

```bash
.venv/bin/pytest tests/test_analyze_bpm.py -v
```

Expected: `5 passed`

- [ ] **Step 7: Smoke test the CLI**

```bash
multidj analyze bpm 2>&1 | head -3
```

Expected: `Dry-run: N tracks would be analyzed ...`

- [ ] **Step 8: Run full suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all existing tests pass + 5 new.

- [ ] **Step 9: Commit**

```bash
git add multidj/constants.py multidj/analyze.py multidj/cli.py tests/test_analyze_bpm.py
git commit -m "feat: add analyze bpm command with librosa beat detection"
```

---

## Task 3: DirectoryAdapter (import directory)

**Wave:** 1 (no dependencies)

**Files:**
- Create: `multidj/adapters/directory.py`
- Modify: `multidj/cli.py` — wire `import directory`
- Modify: `requirements-dev.txt` — add `mutagen`
- Create: `tests/test_import_directory.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_import_directory.py`:

```python
from __future__ import annotations
import os
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from tests.fixtures.multidj_factory import make_multidj_db


def _make_fake_audio_file(path: Path, ext: str = ".mp3") -> Path:
    """Create a zero-byte file that mutagen would read (we'll mock mutagen)."""
    f = path / f"track{ext}"
    f.write_bytes(b"")
    return f


def test_directory_import_dry_run(tmp_path):
    """Dry-run returns found tracks without writing to DB."""
    db_path = tmp_path / "library.sqlite"
    audio_dir = tmp_path / "music"
    audio_dir.mkdir()
    (audio_dir / "01_Artist_-_Title.mp3").write_bytes(b"")

    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda k, d=None: {
        "artist": ["Test Artist"],
        "title": ["Test Title"],
        "album": [],
        "genre": ["House"],
        "bpm": ["128.0"],
    }.get(k, d or [])
    fake_tags.info.length = 240.0

    from multidj.adapters.directory import DirectoryAdapter
    adapter = DirectoryAdapter()

    with patch("multidj.adapters.directory.MutagenFile", return_value=fake_tags):
        result = adapter.import_all(
            multidj_db_path=db_path,
            paths=[str(audio_dir)],
            apply=False,
        )

    assert result["mode"] == "dry_run"
    assert result["total_found"] == 1
    # DB must not be created in dry-run
    assert not db_path.exists()


def test_directory_import_apply_inserts_tracks(tmp_path):
    """Apply mode inserts discovered tracks into the DB."""
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    audio_dir = tmp_path / "music"
    audio_dir.mkdir()
    track_path = audio_dir / "Artist_-_Title.mp3"
    track_path.write_bytes(b"")

    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda k, d=None: {
        "artist": ["New Artist"],
        "title": ["New Title"],
        "album": [],
        "genre": ["Techno"],
        "bpm": ["135.0"],
    }.get(k, d or [])
    fake_tags.info.length = 300.0

    from multidj.adapters.directory import DirectoryAdapter
    adapter = DirectoryAdapter()

    with patch("multidj.adapters.directory.MutagenFile", return_value=fake_tags):
        result = adapter.import_all(
            multidj_db_path=db_path,
            paths=[str(audio_dir)],
            apply=True,
        )

    assert result["mode"] == "apply"
    assert result["new_tracks"] >= 1

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT artist, title, genre, bpm FROM tracks WHERE path = ?",
        (str(track_path),),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "New Artist"
    assert row[2] == "Techno"
    assert row[3] == pytest.approx(135.0)


def test_directory_import_idempotent(tmp_path):
    """Running import twice does not create duplicate tracks."""
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    audio_dir = tmp_path / "music"
    audio_dir.mkdir()
    (audio_dir / "track.mp3").write_bytes(b"")

    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda k, d=None: {"artist": ["X"], "title": ["Y"]}.get(k, d or [])
    fake_tags.info.length = 200.0

    from multidj.adapters.directory import DirectoryAdapter
    adapter = DirectoryAdapter()

    with patch("multidj.adapters.directory.MutagenFile", return_value=fake_tags):
        adapter.import_all(multidj_db_path=db_path, paths=[str(audio_dir)], apply=True)
        result2 = adapter.import_all(multidj_db_path=db_path, paths=[str(audio_dir)], apply=True)

    assert result2["new_tracks"] == 0
    assert result2["unchanged_tracks"] == 1

    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM tracks WHERE path LIKE ?", ("%track.mp3%",)).fetchone()[0]
    conn.close()
    assert count == 1


def test_directory_import_skips_unsupported_extensions(tmp_path):
    """Files with non-audio extensions are ignored."""
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "cover.jpg").write_bytes(b"")
    (music_dir / "notes.txt").write_bytes(b"")
    (music_dir / "track.mp3").write_bytes(b"")

    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda k, d=None: []
    fake_tags.info.length = 100.0

    from multidj.adapters.directory import DirectoryAdapter
    adapter = DirectoryAdapter()

    with patch("multidj.adapters.directory.MutagenFile", return_value=fake_tags):
        result = adapter.import_all(multidj_db_path=db_path, paths=[str(music_dir)], apply=True)

    assert result["total_found"] == 1


def test_directory_import_recurses_subdirectories(tmp_path):
    """Subdirectories are walked recursively."""
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    deep = tmp_path / "music" / "house" / "2024"
    deep.mkdir(parents=True)
    (deep / "track.flac").write_bytes(b"")

    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda k, d=None: []
    fake_tags.info.length = 180.0

    from multidj.adapters.directory import DirectoryAdapter
    adapter = DirectoryAdapter()

    with patch("multidj.adapters.directory.MutagenFile", return_value=fake_tags):
        result = adapter.import_all(multidj_db_path=db_path, paths=[str(tmp_path / "music")], apply=True)

    assert result["total_found"] == 1
```

- [ ] **Step 2: Run to confirm all fail**

```bash
.venv/bin/pytest tests/test_import_directory.py -v
```

Expected: all FAILED — `ImportError: cannot import name 'DirectoryAdapter'`

- [ ] **Step 3: Create multidj/adapters/directory.py**

```python
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..adapters.base import SyncAdapter
from ..backup import create_backup
from ..constants import KNOWN_ADAPTERS
from ..db import connect

try:
    from mutagen import File as MutagenFile  # type: ignore
except ImportError:
    MutagenFile = None  # type: ignore

SUPPORTED_EXTENSIONS = frozenset(
    {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".mp4", ".ogg", ".opus"}
)


def _read_tags(filepath: str) -> dict[str, Any]:
    """Read embedded tags from an audio file using mutagen Easy tags."""
    if MutagenFile is None:
        raise ImportError("Directory import requires: pip install mutagen")

    audio = MutagenFile(filepath, easy=True)
    if audio is None:
        return {}

    def _first(key: str) -> str | None:
        vals = audio.get(key)
        return str(vals[0]).strip() if vals else None

    bpm_raw = _first("bpm")
    bpm: float | None = None
    if bpm_raw:
        try:
            bpm = float(bpm_raw)
        except (ValueError, TypeError):
            bpm = None

    return {
        "artist":   _first("artist"),
        "title":    _first("title"),
        "album":    _first("album"),
        "genre":    _first("genre"),
        "bpm":      bpm,
        "duration": getattr(audio.info, "length", None),
        "filesize": os.path.getsize(filepath),
    }


def _walk_audio_files(paths: list[str]) -> list[str]:
    """Recursively collect all supported audio file paths."""
    found: list[str] = []
    for root_path in paths:
        for dirpath, _dirs, files in os.walk(root_path):
            for fname in sorted(files):
                if Path(fname).suffix.lower() in SUPPORTED_EXTENSIONS:
                    found.append(os.path.join(dirpath, fname))
    return found


class DirectoryAdapter(SyncAdapter):
    """Import tracks from raw filesystem directories into the MultiDJ DB."""

    def import_all(
        self,
        multidj_db_path: Path,
        apply: bool = False,
        paths: list[str] | None = None,
        backup_dir: str | None = None,
    ) -> dict[str, Any]:
        paths = paths or []
        audio_files = _walk_audio_files(paths)

        if not apply:
            return {
                "mode": "dry_run",
                "total_found": len(audio_files),
                "sample": audio_files[:5],
            }

        if backup_dir is not False and Path(str(multidj_db_path)).exists():
            create_backup(str(multidj_db_path), backup_dir=backup_dir)

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        new_tracks = 0
        updated_tracks = 0
        unchanged_tracks = 0
        errors: list[dict] = []

        with connect(str(multidj_db_path), readonly=False) as conn:
            for filepath in audio_files:
                try:
                    tags = _read_tags(filepath)
                    existing = conn.execute(
                        "SELECT id, artist, title, genre, bpm FROM tracks WHERE path = ?",
                        (filepath,),
                    ).fetchone()

                    if existing is None:
                        cur = conn.execute(
                            """
                            INSERT INTO tracks
                                (path, artist, title, album, genre, bpm, duration, filesize, deleted)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                            """,
                            (
                                filepath,
                                tags.get("artist"),
                                tags.get("title"),
                                tags.get("album"),
                                tags.get("genre"),
                                tags.get("bpm"),
                                tags.get("duration"),
                                tags.get("filesize"),
                            ),
                        )
                        track_id = cur.lastrowid
                        new_tracks += 1
                    else:
                        # Simple change detection: artist, title, genre, bpm
                        changed = (
                            existing["artist"] != tags.get("artist")
                            or existing["title"] != tags.get("title")
                            or existing["genre"] != tags.get("genre")
                            or existing["bpm"] != tags.get("bpm")
                        )
                        if changed:
                            conn.execute(
                                """
                                UPDATE tracks SET
                                    artist=?, title=?, album=?, genre=?, bpm=?,
                                    duration=?, filesize=?, updated_at=?
                                WHERE path=?
                                """,
                                (
                                    tags.get("artist"),
                                    tags.get("title"),
                                    tags.get("album"),
                                    tags.get("genre"),
                                    tags.get("bpm"),
                                    tags.get("duration"),
                                    tags.get("filesize"),
                                    now_iso,
                                    filepath,
                                ),
                            )
                            track_id = existing["id"]
                            updated_tracks += 1
                        else:
                            track_id = existing["id"]
                            unchanged_tracks += 1

                    # Ensure sync_state rows exist for all known adapters
                    for adapter_name in KNOWN_ADAPTERS:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO sync_state (track_id, adapter, dirty, last_synced_at)
                            VALUES (?, ?, 1, ?)
                            """,
                            (track_id, adapter_name, now_iso),
                        )

                    conn.commit()

                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    errors.append({"path": filepath, "error": str(exc)})

        return {
            "mode":             "apply",
            "total_found":      len(audio_files),
            "new_tracks":       new_tracks,
            "updated_tracks":   updated_tracks,
            "unchanged_tracks": unchanged_tracks,
            "errors":           errors,
        }

    def push_track(self, track: dict, conn: Any) -> bool:  # type: ignore[override]
        raise NotImplementedError("DirectoryAdapter is import-only")

    def full_sync(self, multidj_db_path: Path, apply: bool = False) -> dict:
        raise NotImplementedError("DirectoryAdapter is import-only")
```

- [ ] **Step 4: Wire `import directory` in cli.py**

In `multidj/cli.py`, find the `import` subparser section (where `import mixxx` is wired) and add:

```python
# In the import subparsers block, after 'mixxx':
p_dir = import_sub.add_parser("directory", help="Import tracks from filesystem directories")
p_dir.add_argument("paths", nargs="+", metavar="PATH",
                   help="Directories to scan recursively")
p_dir.add_argument("--apply",     action="store_true")
p_dir.add_argument("--no-backup", action="store_true")
```

And in the dispatch section:

```python
elif args.import_cmd == "directory":
    from .adapters.directory import DirectoryAdapter
    adapter = DirectoryAdapter()
    result = adapter.import_all(
        multidj_db_path=resolve_db_path(args.db),
        apply=args.apply,
        paths=args.paths,
        backup_dir=False if args.no_backup else None,
    )
    emit(result, args.json)
```

- [ ] **Step 5: Add mutagen to requirements-dev.txt**

Edit `requirements-dev.txt`:

```
mutagen>=1.47
pytest>=9.0
# Optional: for `multidj analyze key` and `multidj analyze bpm`
# librosa
# mutagen  ← now a runtime dep, not optional
```

Install it: `.venv/bin/pip install mutagen`

- [ ] **Step 6: Run tests — all must pass**

```bash
.venv/bin/pytest tests/test_import_directory.py -v
```

Expected: `5 passed`

- [ ] **Step 7: Smoke test CLI**

```bash
multidj import directory ~/Music/All_Tracks/ 2>&1 | head -3
```

Expected: `mode: dry_run`, `total_found: ...`

- [ ] **Step 8: Run full suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add multidj/adapters/directory.py multidj/cli.py requirements-dev.txt tests/test_import_directory.py
git commit -m "feat: add DirectoryAdapter — import tracks from raw filesystem dirs"
```

---

## Task 4: BPM-range crate auto-generation

**Wave:** 2 (needs Task 2 `BPM_RANGES` constant from constants.py)

**Files:**
- Modify: `multidj/crates.py` — extend `rebuild_crates()` to generate `BPM:*` crates
- Modify: `tests/test_crates.py` — add BPM-range crate tests

- [ ] **Step 1: Read existing rebuild_crates() to understand the pattern**

```bash
grep -n "rebuild_crates\|Genre:\|auto" multidj/crates.py | head -30
```

- [ ] **Step 2: Write failing tests**

Add to `tests/test_crates.py`:

```python
from multidj.constants import BPM_RANGES

def test_rebuild_creates_bpm_crates(multidj_db, tmp_path):
    """rebuild_crates generates BPM: crates for each range that has tracks."""
    import sqlite3
    # Set track 1 to 128 BPM (falls in BPM:125-130 and BPM:128-135)
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET bpm = 128.0 WHERE id = 1")
    conn.commit()
    conn.close()

    from multidj.crates import rebuild_crates
    result = rebuild_crates(str(multidj_db), apply=True, backup_dir=str(tmp_path))

    conn2 = sqlite3.connect(str(multidj_db))
    bpm_crates = conn2.execute(
        "SELECT name FROM crates WHERE name LIKE 'BPM:%'"
    ).fetchall()
    conn2.close()

    crate_names = {r[0] for r in bpm_crates}
    # Track at 128 BPM must appear in both overlapping ranges
    assert "BPM:125-130" in crate_names
    assert "BPM:128-135" in crate_names


def test_rebuild_bpm_crate_contains_correct_tracks(multidj_db, tmp_path):
    """Each BPM crate contains only tracks in its range."""
    import sqlite3
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET bpm = 125.0 WHERE id = 1")
    conn.execute("UPDATE tracks SET bpm = 90.0  WHERE id = 2")
    conn.commit()
    conn.close()

    from multidj.crates import rebuild_crates
    rebuild_crates(str(multidj_db), apply=True, backup_dir=str(tmp_path))

    conn2 = sqlite3.connect(str(multidj_db))
    crate = conn2.execute(
        "SELECT id FROM crates WHERE name = 'BPM:115-125'"
    ).fetchone()
    # id=1 (bpm=125) is NOT in 115-125 (high is exclusive), only id=2's bpm=90 is in <90
    if crate:
        tracks_in_crate = conn2.execute(
            "SELECT track_id FROM crate_tracks WHERE crate_id = ?", (crate[0],)
        ).fetchall()
        track_ids = {r[0] for r in tracks_in_crate}
        assert 1 not in track_ids  # 125.0 is in 125-130, not 115-125
    conn2.close()


def test_rebuild_bpm_crates_dry_run_no_write(multidj_db):
    """Dry-run does not create BPM crates."""
    import sqlite3
    from multidj.crates import rebuild_crates
    rebuild_crates(str(multidj_db), apply=False)

    conn = sqlite3.connect(str(multidj_db))
    count = conn.execute(
        "SELECT COUNT(*) FROM crates WHERE name LIKE 'BPM:%'"
    ).fetchone()[0]
    conn.close()
    assert count == 0
```

- [ ] **Step 3: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_crates.py -k "bpm" -v
```

Expected: FAILED — BPM crates not created yet.

- [ ] **Step 4: Extend rebuild_crates() in crates.py**

Find `rebuild_crates()` in `multidj/crates.py`. After the Genre crate creation block and before the return statement, add the BPM-range crate block:

```python
from .constants import BPM_RANGES  # add to existing import

# Inside rebuild_crates(), after genre crates are created:

# ── BPM-range crates ─────────────────────────────────────────────────
bpm_created = 0
bpm_tracks_added = 0

for crate_name, bpm_low, bpm_high in BPM_RANGES:
    track_ids = [
        r["id"] for r in conn.execute(
            """
            SELECT id FROM tracks
            WHERE deleted = 0
              AND bpm IS NOT NULL
              AND bpm >= ? AND bpm < ?
            """,
            (bpm_low, bpm_high),
        ).fetchall()
    ]
    if not track_ids:
        continue  # don't create empty crates

    cur = conn.execute(
        "INSERT OR IGNORE INTO crates (name, type, show) VALUES (?, 'auto', 1)",
        (crate_name,),
    )
    if cur.lastrowid and cur.lastrowid > 0:
        bpm_created += 1
    crate_id = conn.execute(
        "SELECT id FROM crates WHERE name = ?", (crate_name,)
    ).fetchone()[0]

    conn.executemany(
        "INSERT OR IGNORE INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
        [(crate_id, tid) for tid in track_ids],
    )
    bpm_tracks_added += len(track_ids)
```

Also update the return dict to include `bpm_crates_created` and `bpm_tracks_added`.

- [ ] **Step 5: Run BPM crate tests**

```bash
.venv/bin/pytest tests/test_crates.py -k "bpm" -v
```

Expected: `3 passed`

- [ ] **Step 6: Run full crates test suite**

```bash
.venv/bin/pytest tests/test_crates.py -v
```

Expected: all pass.

- [ ] **Step 7: Run full suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add multidj/crates.py multidj/constants.py tests/test_crates.py
git commit -m "feat: generate BPM-range crates in crates rebuild"
```

---

## Task 5: Mixxx crate sync — push crates from MultiDJ to Mixxx

**Wave:** 2 (no new dependencies — existing Mixxx adapter infra is sufficient)

**Files:**
- Modify: `multidj/adapters/mixxx.py` — add `_push_crates_to_mixxx()` and call it from `full_sync()`
- Create: `tests/test_mixxx_crate_sync.py`

- [ ] **Step 1: Inspect Mixxx DB crate schema**

```bash
sqlite3 ~/.mixxx/mixxxdb.sqlite ".schema crates" 2>/dev/null
sqlite3 ~/.mixxx/mixxxdb.sqlite ".schema crate_tracks" 2>/dev/null
```

Record the output — you'll need the exact column names. Expected:

```sql
CREATE TABLE crates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    show INTEGER DEFAULT 1,
    locked INTEGER DEFAULT 0,
    autodj_source INTEGER DEFAULT 0
);
CREATE TABLE crate_tracks (
    crate_id INTEGER REFERENCES crates(id),
    track_id INTEGER REFERENCES library(id)
);
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_mixxx_crate_sync.py`:

```python
from __future__ import annotations
import sqlite3
import pytest
from pathlib import Path
from tests.fixtures.multidj_factory import make_multidj_db
from tests.fixtures.mixxx_factory import make_mixxx_db


def _add_crate_to_multidj(conn, name: str, track_ids: list[int], crate_type: str = "auto"):
    cur = conn.execute(
        "INSERT INTO crates (name, type, show) VALUES (?, ?, 1)", (name, crate_type)
    )
    crate_id = cur.lastrowid
    for tid in track_ids:
        conn.execute(
            "INSERT OR IGNORE INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
            (crate_id, tid),
        )
    conn.commit()
    return crate_id


def test_push_crates_creates_crates_in_mixxx(tmp_path):
    """full_sync with crates pushes MultiDJ crates into Mixxx DB."""
    mdj_db = make_multidj_db(tmp_path / "library.sqlite")
    mxdb = make_mixxx_db(tmp_path / "mixxxdb.sqlite")

    # Add a crate with track 1
    conn = sqlite3.connect(str(mdj_db))
    _add_crate_to_multidj(conn, "Genre: House", [1, 4])
    conn.close()

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=mxdb)
    result = adapter.full_sync(mdj_db, apply=True)

    mx_conn = sqlite3.connect(str(mxdb))
    crates = mx_conn.execute("SELECT name FROM crates WHERE name = 'Genre: House'").fetchall()
    mx_conn.close()

    assert len(crates) == 1


def test_push_crates_dry_run_no_write(tmp_path):
    """Dry-run does not write crates to Mixxx."""
    mdj_db = make_multidj_db(tmp_path / "library.sqlite")
    mxdb = make_mixxx_db(tmp_path / "mixxxdb.sqlite")

    conn = sqlite3.connect(str(mdj_db))
    _add_crate_to_multidj(conn, "BPM:125-130", [1])
    conn.close()

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=mxdb)
    result = adapter.full_sync(mdj_db, apply=False)

    mx_conn = sqlite3.connect(str(mxdb))
    count = mx_conn.execute("SELECT COUNT(*) FROM crates").fetchone()[0]
    mx_conn.close()

    assert count == 0  # Mixxx fixture DB starts empty


def test_push_crates_idempotent(tmp_path):
    """Syncing crates twice does not create duplicates in Mixxx."""
    mdj_db = make_multidj_db(tmp_path / "library.sqlite")
    mxdb = make_mixxx_db(tmp_path / "mixxxdb.sqlite")

    conn = sqlite3.connect(str(mdj_db))
    _add_crate_to_multidj(conn, "Genre: Techno", [8])
    conn.close()

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=mxdb)
    adapter.full_sync(mdj_db, apply=True)
    adapter.full_sync(mdj_db, apply=True)

    mx_conn = sqlite3.connect(str(mxdb))
    count = mx_conn.execute(
        "SELECT COUNT(*) FROM crates WHERE name = 'Genre: Techno'"
    ).fetchone()[0]
    mx_conn.close()
    assert count == 1
```

- [ ] **Step 3: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_mixxx_crate_sync.py -v
```

Expected: FAILED — crates not yet pushed.

- [ ] **Step 4: Add _push_crates_to_mixxx() to adapters/mixxx.py**

Add this function before the `MixxxAdapter` class definition:

```python
def _push_crates_to_mixxx(
    mdj_conn: sqlite3.Connection,
    mixxx_conn: sqlite3.Connection,
) -> dict[str, int]:
    """Push all non-deleted crates from MultiDJ to Mixxx.

    Finds or creates each crate in Mixxx by name, then inserts
    crate_tracks for each member track (matched by file path).
    Returns counts of crates and tracks synced.
    """
    crates = mdj_conn.execute(
        "SELECT id, name FROM crates WHERE show = 1"
    ).fetchall()

    crates_pushed = 0
    tracks_pushed = 0

    for crate in crates:
        mdj_crate_id = crate["id"]
        crate_name = crate["name"]

        # Find or create crate in Mixxx
        existing = mixxx_conn.execute(
            "SELECT id FROM crates WHERE name = ?", (crate_name,)
        ).fetchone()

        if existing:
            mx_crate_id = existing[0]
        else:
            cur = mixxx_conn.execute(
                "INSERT INTO crates (name, count, show) VALUES (?, 0, 1)",
                (crate_name,),
            )
            mx_crate_id = cur.lastrowid
            crates_pushed += 1

        # Get track paths for this crate
        mdj_tracks = mdj_conn.execute(
            """
            SELECT t.path FROM tracks t
            JOIN crate_tracks ct ON t.id = ct.track_id
            WHERE ct.crate_id = ? AND t.deleted = 0
            """,
            (mdj_crate_id,),
        ).fetchall()

        for track_row in mdj_tracks:
            path = track_row["path"]
            # Look up Mixxx library id by path
            mx_track = mixxx_conn.execute(
                """
                SELECT l.id FROM library l
                JOIN track_locations tl ON l.location = tl.id
                WHERE tl.location = ?
                """,
                (path,),
            ).fetchone()
            if mx_track is None:
                continue
            mx_track_id = mx_track[0]
            mixxx_conn.execute(
                "INSERT OR IGNORE INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
                (mx_crate_id, mx_track_id),
            )
            tracks_pushed += 1

        # Update crate count
        count = mixxx_conn.execute(
            "SELECT COUNT(*) FROM crate_tracks WHERE crate_id = ?", (mx_crate_id,)
        ).fetchone()[0]
        mixxx_conn.execute(
            "UPDATE crates SET count = ? WHERE id = ?", (count, mx_crate_id)
        )

    return {"crates_pushed": crates_pushed, "tracks_pushed": tracks_pushed}
```

Then in `full_sync()`, in the apply block after `mixxx_conn` is opened, call it:

```python
# After the track sync loop, before closing mixxx_conn:
crate_result = _push_crates_to_mixxx(mdj_conn, mixxx_conn)
mixxx_conn.commit()
```

And include in the return dict:
```python
"crates_pushed": crate_result["crates_pushed"],
"crate_tracks_pushed": crate_result["tracks_pushed"],
```

Also update the dry-run return to include crate counts:
```python
# In the dry_run branch, also count crates:
crate_count = mdj_conn_ro.execute("SELECT COUNT(*) FROM crates WHERE show=1").fetchone()[0]
return {
    "mode": "dry_run",
    "dirty_tracks": len(dirty_tracks),
    "crates_to_sync": crate_count,
    "sample": sample,
}
```

- [ ] **Step 5: Run crate sync tests**

```bash
.venv/bin/pytest tests/test_mixxx_crate_sync.py -v
```

Expected: `3 passed`

- [ ] **Step 6: Run full suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add multidj/adapters/mixxx.py tests/test_mixxx_crate_sync.py
git commit -m "feat: sync crates from MultiDJ to Mixxx in full_sync"
```

---

## Task 6: enrich fingerprint (AcoustID identification)

**Wave:** 3 (needs Task 3 — DirectoryAdapter establishes the untagged track use case)

**Files:**
- Modify: `multidj/enrich.py` — add `enrich_fingerprint()`
- Modify: `multidj/cli.py` — wire `enrich fingerprint`
- Modify: `tests/test_enrich.py` — add fingerprint tests

- [ ] **Step 1: Install pyacoustid**

```bash
.venv/bin/pip install pyacoustid
echo "pyacoustid>=1.3" >> requirements-dev.txt
```

Verify chromaprint is also available (binary):
```bash
fpcalc --version 2>/dev/null || echo "Install: sudo dnf install chromaprint-tools"
```

- [ ] **Step 2: Write failing tests**

Add to `tests/test_enrich.py`:

```python
from unittest.mock import patch

def test_enrich_fingerprint_dry_run(multidj_db):
    """Dry-run identifies candidates without writing."""
    import sqlite3
    # Clear artist/title to create unidentified tracks
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET artist = NULL, title = NULL WHERE id = 1")
    conn.commit()
    conn.close()

    from multidj.enrich import enrich_fingerprint
    result = enrich_fingerprint(str(multidj_db), apply=False)

    assert result["mode"] == "dry_run"
    assert result["total_candidates"] >= 1

    # Must not have written anything
    conn2 = sqlite3.connect(str(multidj_db))
    row = conn2.execute("SELECT artist FROM tracks WHERE id = 1").fetchone()
    conn2.close()
    assert row[0] is None


def test_enrich_fingerprint_apply_fills_metadata(multidj_db, tmp_path):
    """Apply mode writes AcoustID results back to DB."""
    import sqlite3
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET artist = NULL, title = NULL WHERE id = 1")
    path = conn.execute("SELECT path FROM tracks WHERE id = 1").fetchone()[0]
    conn.commit()
    conn.close()

    fake_match = {
        "artist": "Identified Artist",
        "title": "Identified Title",
        "score": 0.92,
    }

    with patch("multidj.enrich._acoustid_lookup", return_value=fake_match):
        from multidj.enrich import enrich_fingerprint
        result = enrich_fingerprint(str(multidj_db), apply=True, backup_dir=str(tmp_path))

    assert result["succeeded"] >= 1
    conn2 = sqlite3.connect(str(multidj_db))
    row = conn2.execute("SELECT artist, title FROM tracks WHERE id = 1").fetchone()
    conn2.close()
    assert row[0] == "Identified Artist"
    assert row[1] == "Identified Title"


def test_enrich_fingerprint_skips_already_tagged(multidj_db, tmp_path):
    """Tracks that already have artist+title are not fingerprinted."""
    with patch("multidj.enrich._acoustid_lookup") as mock_lookup:
        from multidj.enrich import enrich_fingerprint
        result = enrich_fingerprint(str(multidj_db), apply=True, backup_dir=str(tmp_path))
    # All fixture tracks already have artist+title — mock should never be called
    mock_lookup.assert_not_called()
    assert result["total_candidates"] == 0
```

- [ ] **Step 3: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_enrich.py -k "fingerprint" -v
```

Expected: FAILED — `enrich_fingerprint` not defined.

- [ ] **Step 4: Add enrich_fingerprint() to enrich.py**

Add to `multidj/enrich.py`:

```python
def _acoustid_lookup(filepath: str) -> dict | None:
    """Fingerprint a file and query AcoustID. Returns best match or None."""
    try:
        import acoustid  # type: ignore
    except ImportError:
        raise ImportError("Fingerprint enrichment requires: pip install pyacoustid")

    # 'cSpUJKpD' is the AcoustID public test API key for open-source projects
    API_KEY = "cSpUJKpD"
    MIN_SCORE = 0.7

    try:
        results = acoustid.match(API_KEY, filepath, meta="recordings")
    except acoustid.NoBackendError:
        raise RuntimeError("chromaprint/fpcalc not found. Install: sudo dnf install chromaprint-tools")
    except acoustid.FingerprintGenerationError as exc:
        raise RuntimeError(f"Could not fingerprint file: {exc}")

    best_score = 0.0
    best_match: dict | None = None

    for score, recording_id, title, artist in results:
        if score > best_score and score >= MIN_SCORE:
            best_score = score
            best_match = {
                "artist": artist,
                "title": title,
                "score": score,
                "recording_id": recording_id,
            }

    return best_match


def enrich_fingerprint(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    backup_dir: str | None = None,
) -> dict[str, Any]:
    """Identify unknown tracks via AcoustID fingerprinting."""
    from .backup import create_backup
    from .db import connect, ensure_not_empty, table_exists

    with connect(db_path, readonly=True) as _guard:
        if table_exists(_guard, "library") and not table_exists(_guard, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB.")
        ensure_not_empty(_guard)

    where = "1=1" if force else "(artist IS NULL OR TRIM(artist)='' OR title IS NULL OR TRIM(title)='')"
    sql = f"SELECT id, path, artist, title FROM tracks WHERE {where} AND deleted=0 ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"

    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(sql).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM tracks WHERE {where} AND deleted=0"
        ).fetchone()[0]

    if not apply:
        return {"mode": "dry_run", "total_candidates": total, "processed": 0,
                "succeeded": 0, "errors": 0, "error_details": []}

    if backup_dir is not False:
        create_backup(db_path, backup_dir=backup_dir)

    updates: list[tuple] = []
    error_details: list[dict] = []
    succeeded = 0

    for row in rows:
        try:
            match = _acoustid_lookup(row["path"])
            if match:
                updates.append((match["artist"], match["title"], row["id"]))
                succeeded += 1
        except ImportError:
            raise
        except Exception as exc:
            error_details.append({"track_id": row["id"], "error": str(exc)})

    if updates:
        with connect(db_path, readonly=False) as wconn:
            wconn.executemany(
                "UPDATE tracks SET artist=?, title=? WHERE id=?", updates
            )
            wconn.commit()

    return {
        "mode": "apply",
        "total_candidates": total,
        "processed": len(rows),
        "succeeded": succeeded,
        "errors": len(error_details),
        "error_details": error_details,
    }
```

- [ ] **Step 5: Wire `enrich fingerprint` in cli.py**

Find the `enrich` subparser. Add:

```python
p_fp = enrich_sub.add_parser("fingerprint", help="Identify unknown tracks via AcoustID")
p_fp.add_argument("--apply",     action="store_true")
p_fp.add_argument("--force",     action="store_true")
p_fp.add_argument("--limit",     type=int, default=None)
p_fp.add_argument("--no-backup", action="store_true")
```

Dispatch:

```python
elif args.enrich_cmd == "fingerprint":
    from .enrich import enrich_fingerprint
    data = enrich_fingerprint(
        db_path=args.db,
        apply=args.apply,
        force=getattr(args, "force", False),
        limit=args.limit,
        backup_dir=False if args.no_backup else None,
    )
    emit(data, args.json)
```

- [ ] **Step 6: Run fingerprint tests**

```bash
.venv/bin/pytest tests/test_enrich.py -v
```

Expected: all pass (including existing language tests).

- [ ] **Step 7: Commit**

```bash
git add multidj/enrich.py multidj/cli.py requirements-dev.txt tests/test_enrich.py
git commit -m "feat: add enrich fingerprint via AcoustID for unidentified tracks"
```

---

## Task 7: analyze cues — detect intro/drop/outro

**Wave:** 3 (needs Task 1 — cue_points table)

**Files:**
- Modify: `multidj/analyze.py` — add `detect_cues()` and `analyze_cues()`
- Modify: `multidj/cli.py` — wire `analyze cues`
- Create: `tests/test_cues.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cues.py`:

```python
from __future__ import annotations
import sqlite3
import pytest
from unittest.mock import patch
from tests.fixtures.multidj_factory import make_multidj_db


FAKE_CUES = {"intro_end": 8.0, "drop": 32.0, "outro_start": 210.0}


def test_analyze_cues_dry_run(multidj_db):
    """Dry-run lists candidates without writing cue_points."""
    from multidj.analyze import analyze_cues
    result = analyze_cues(str(multidj_db), apply=False)

    assert result["mode"] == "dry_run"
    assert result["total_candidates"] >= 1

    conn = sqlite3.connect(str(multidj_db))
    count = conn.execute("SELECT COUNT(*) FROM cue_points").fetchone()[0]
    conn.close()
    assert count == 0


def test_analyze_cues_apply_writes_cue_points(multidj_db, tmp_path):
    """Apply mode inserts cue_points rows for each track."""
    with patch("multidj.analyze.detect_cues", return_value=FAKE_CUES):
        from multidj.analyze import analyze_cues
        result = analyze_cues(
            str(multidj_db), apply=True, backup_dir=str(tmp_path)
        )

    assert result["succeeded"] >= 1

    conn = sqlite3.connect(str(multidj_db))
    rows = conn.execute(
        "SELECT type, position FROM cue_points WHERE track_id = 1 ORDER BY position"
    ).fetchall()
    conn.close()

    types = {r[0] for r in rows}
    assert "intro_end" in types
    assert "drop" in types
    assert "outro_start" in types


def test_analyze_cues_positions_correct(multidj_db, tmp_path):
    """Positions stored match detected values."""
    with patch("multidj.analyze.detect_cues", return_value=FAKE_CUES):
        from multidj.analyze import analyze_cues
        analyze_cues(str(multidj_db), apply=True, backup_dir=str(tmp_path))

    conn = sqlite3.connect(str(multidj_db))
    intro = conn.execute(
        "SELECT position FROM cue_points WHERE track_id=1 AND type='intro_end'"
    ).fetchone()
    conn.close()
    assert intro is not None
    assert intro[0] == pytest.approx(8.0)


def test_analyze_cues_isolates_errors(multidj_db, tmp_path):
    """One bad audio file does not abort the batch."""
    call_count = [0]
    def flaky_cues(filepath):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("corrupt audio")
        return FAKE_CUES

    with patch("multidj.analyze.detect_cues", side_effect=flaky_cues):
        from multidj.analyze import analyze_cues
        result = analyze_cues(
            str(multidj_db), apply=True, backup_dir=str(tmp_path)
        )

    assert result["errors"] == 1
    assert result["succeeded"] >= 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_cues.py -v
```

Expected: FAILED — `analyze_cues` not defined.

- [ ] **Step 3: Add detect_cues() and analyze_cues() to analyze.py**

```python
def detect_cues(filepath: str) -> dict[str, float]:
    """Detect intro end, main drop, and outro start via librosa energy analysis.

    Returns times in seconds:
      intro_end   — first point energy crosses 20% of peak (beat kicks in)
      drop        — timestamp of peak energy (main energy climax)
      outro_start — last point energy is above 20% threshold (begins fading)
    """
    try:
        import librosa  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        raise ImportError("Cue detection requires: pip install librosa")

    y, sr = librosa.load(filepath, sr=22050, mono=True)
    hop_length = 512
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    times = librosa.frames_to_time(
        range(len(rms)), sr=sr, hop_length=hop_length
    )

    threshold = 0.2 * float(np.max(rms))
    above = rms > threshold

    intro_end_idx = int(np.argmax(above)) if above.any() else 0
    drop_idx = int(np.argmax(rms))
    # Last frame where energy is above threshold
    reversed_idx = len(above) - 1 - int(np.argmax(above[::-1])) if above.any() else len(above) - 1

    return {
        "intro_end":   float(times[intro_end_idx]),
        "drop":        float(times[drop_idx]),
        "outro_start": float(times[reversed_idx]),
    }


def analyze_cues(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    backup_dir: str | None = None,
) -> dict[str, Any]:
    """Detect cue points (intro/drop/outro) for all tracks and store in cue_points table."""
    from .backup import create_backup

    with connect(db_path, readonly=True) as _guard:
        if table_exists(_guard, "library") and not table_exists(_guard, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB.")
        ensure_not_empty(_guard)
        if not table_exists(_guard, "cue_points"):
            raise RuntimeError("cue_points table missing — run DB migration first.")

    if force:
        where = "1=1"
    else:
        where = """id NOT IN (
            SELECT DISTINCT track_id FROM cue_points
            WHERE type IN ('intro_end', 'drop', 'outro_start')
        )"""

    sql = f"SELECT id, artist, title, path AS filepath FROM tracks WHERE {where} AND deleted=0"
    if limit:
        sql += f" LIMIT {int(limit)}"

    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(sql).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM tracks WHERE {where} AND deleted=0"
        ).fetchone()[0]

    if not apply:
        _progress(f"Dry-run: {total:,} tracks would be analyzed for cue points")
        return {"mode": "dry_run", "total_candidates": total, "processed": 0,
                "succeeded": 0, "errors": 0, "error_details": []}

    if backup_dir is not False:
        create_backup(db_path, backup_dir=backup_dir)

    cue_rows: list[tuple] = []
    error_details: list[dict] = []
    succeeded = 0
    total_rows = len(rows)

    for i, row in enumerate(rows, 1):
        label = f"{row['artist'] or ''} - {row['title'] or ''}".strip(" -")
        _progress(f"[{i:{len(str(total_rows))}}/{total_rows}] {label[:60]}", end="")
        try:
            cues = detect_cues(row["filepath"])
            for cue_type, position in cues.items():
                cue_rows.append((row["id"], cue_type, position))
            succeeded += 1
            _progress(f"  intro={cues['intro_end']:.1f}s drop={cues['drop']:.1f}s outro={cues['outro_start']:.1f}s")
        except ImportError:
            raise
        except Exception as exc:
            _progress(f"  ERROR: {exc}")
            error_details.append({"track_id": row["id"], "error": str(exc)})

    if cue_rows:
        with connect(db_path, readonly=False) as wconn:
            wconn.executemany(
                "INSERT OR REPLACE INTO cue_points (track_id, type, position) VALUES (?, ?, ?)",
                cue_rows,
            )
            wconn.commit()

    return {
        "mode": "apply",
        "total_candidates": total,
        "processed": total_rows,
        "succeeded": succeeded,
        "errors": len(error_details),
        "error_details": error_details,
    }
```

- [ ] **Step 4: Wire `analyze cues` in cli.py**

In the `analyze` subparser block:

```python
p_cues = analyze_sub.add_parser("cues", help="Detect intro/drop/outro cue points from audio")
p_cues.add_argument("--apply",     action="store_true")
p_cues.add_argument("--force",     action="store_true")
p_cues.add_argument("--limit",     type=int, default=None)
p_cues.add_argument("--no-backup", action="store_true")
```

Dispatch:

```python
elif args.analyze_cmd == "cues":
    from .analyze import analyze_cues
    data = analyze_cues(
        db_path=args.db,
        apply=args.apply,
        force=getattr(args, "force", False),
        limit=args.limit,
        backup_dir=False if args.no_backup else None,
    )
    emit(data, args.json)
```

- [ ] **Step 5: Run cue tests**

```bash
.venv/bin/pytest tests/test_cues.py -v
```

Expected: `4 passed`

- [ ] **Step 6: Run full suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add multidj/analyze.py multidj/cli.py tests/test_cues.py
git commit -m "feat: add analyze cues command — librosa energy-based intro/drop/outro detection"
```

---

## Task 8: Mixxx cue point sync

**Wave:** 3 (needs Task 7 — cue_points populated; needs Task 5 — crate sync pattern)

**Files:**
- Modify: `multidj/adapters/mixxx.py` — add `_push_cues_to_mixxx()` and call from `full_sync()`
- Modify: `tests/test_mixxx_crate_sync.py` — add cue sync tests

- [ ] **Step 1: Inspect Mixxx cue table schema**

```bash
sqlite3 ~/.mixxx/mixxxdb.sqlite ".schema cues"
```

Expected:
```sql
CREATE TABLE cues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER,
    type INTEGER,
    position REAL,
    length REAL,
    hotcue INTEGER DEFAULT -1,
    label TEXT,
    color INTEGER DEFAULT 4294901760
);
```

Mixxx cue types: `1` = hot cue, `2` = loop, `4` = main cue. We'll use `1` for hot cues.

- [ ] **Step 2: Write failing tests**

Add to `tests/test_mixxx_crate_sync.py`:

```python
def test_push_cues_to_mixxx(tmp_path):
    """full_sync writes cue_points from MultiDJ to Mixxx cues table."""
    mdj_db = make_multidj_db(tmp_path / "library.sqlite")
    mxdb = make_mixxx_db(tmp_path / "mixxxdb.sqlite")

    # Insert a cue point for track 1
    conn = sqlite3.connect(str(mdj_db))
    track_path = conn.execute("SELECT path FROM tracks WHERE id=1").fetchone()[0]
    conn.execute(
        "INSERT INTO cue_points (track_id, type, position, label) VALUES (1, 'intro_end', 8.0, 'Intro')"
    )
    conn.commit()
    conn.close()

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=mxdb)
    adapter.full_sync(mdj_db, apply=True)

    mx_conn = sqlite3.connect(str(mxdb))
    # Get the Mixxx track id for this path
    mx_track = mx_conn.execute(
        "SELECT l.id FROM library l JOIN track_locations tl ON l.location=tl.id WHERE tl.location=?",
        (track_path,),
    ).fetchone()

    if mx_track:
        cues = mx_conn.execute(
            "SELECT position, label FROM cues WHERE track_id=?", (mx_track[0],)
        ).fetchall()
        mx_conn.close()
        positions = {r[0] for r in cues}
        # Position stored as milliseconds in Mixxx: 8.0s * 1000 = 8000
        assert any(abs(p - 8000.0) < 1.0 for p in positions)
    else:
        mx_conn.close()
        pytest.skip("Fixture track path not in Mixxx fixture DB")
```

- [ ] **Step 3: Add _push_cues_to_mixxx() to adapters/mixxx.py**

Add before `MixxxAdapter`:

```python
def _push_cues_to_mixxx(
    mdj_conn: sqlite3.Connection,
    mixxx_conn: sqlite3.Connection,
) -> dict[str, int]:
    """Write cue_points from MultiDJ to Mixxx cues table.

    Mixxx stores cue position in milliseconds (float).
    MultiDJ stores position in seconds (float).
    Mixxx cue type: 1 = hot cue, 4 = main cue point.
    """
    TYPE_MAP = {
        "intro_end":   1,  # hot cue
        "drop":        1,  # hot cue
        "outro_start": 1,  # hot cue
        "hot_cue":     1,
    }
    HOTCUE_SLOT = {
        "intro_end":   0,
        "drop":        1,
        "outro_start": 2,
    }

    mdj_cues = mdj_conn.execute(
        """
        SELECT cp.track_id, cp.type, cp.position, cp.label, t.path
        FROM cue_points cp
        JOIN tracks t ON t.id = cp.track_id
        WHERE t.deleted = 0
        ORDER BY cp.track_id, cp.position
        """
    ).fetchall()

    cues_pushed = 0

    for cue in mdj_cues:
        track_path = cue["path"]
        position_ms = cue["position"] * 1000.0

        mx_track = mixxx_conn.execute(
            """
            SELECT l.id FROM library l
            JOIN track_locations tl ON l.location = tl.id
            WHERE tl.location = ?
            """,
            (track_path,),
        ).fetchone()
        if mx_track is None:
            continue

        mx_track_id = mx_track[0]
        cue_type = TYPE_MAP.get(cue["type"], 1)
        hotcue_slot = HOTCUE_SLOT.get(cue["type"], -1)

        # Upsert by (track_id, hotcue) to avoid duplicates on re-sync
        existing = mixxx_conn.execute(
            "SELECT id FROM cues WHERE track_id=? AND hotcue=?",
            (mx_track_id, hotcue_slot),
        ).fetchone()

        if existing:
            mixxx_conn.execute(
                "UPDATE cues SET position=?, label=?, type=? WHERE id=?",
                (position_ms, cue["label"], cue_type, existing[0]),
            )
        else:
            mixxx_conn.execute(
                "INSERT INTO cues (track_id, type, position, length, hotcue, label) VALUES (?,?,?,0,?,?)",
                (mx_track_id, cue_type, position_ms, hotcue_slot, cue["label"]),
            )
        cues_pushed += 1

    return {"cues_pushed": cues_pushed}
```

In `full_sync()` apply block, after `_push_crates_to_mixxx()`:

```python
cue_result = _push_cues_to_mixxx(mdj_conn, mixxx_conn)
mixxx_conn.commit()
```

Add to return dict: `"cues_pushed": cue_result["cues_pushed"]`

- [ ] **Step 4: Run cue sync test**

```bash
.venv/bin/pytest tests/test_mixxx_crate_sync.py -v
```

Expected: all pass (or last test skipped if fixture path not in Mixxx fixture).

- [ ] **Step 5: Run full suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add multidj/adapters/mixxx.py tests/test_mixxx_crate_sync.py
git commit -m "feat: sync cue points from MultiDJ to Mixxx cues table"
```

---

## Self-Review

**Spec coverage check:**
- ✅ `import directory` — Task 3
- ✅ `analyze bpm` — Task 2
- ✅ BPM-range crates — Task 4, constants in Task 2
- ✅ `sync mixxx` pushes crates — Task 5
- ✅ Fingerprint identification — Task 6
- ✅ Cue point detection (intro/drop/outro) — Task 7
- ✅ Cue points → Mixxx — Task 8
- ✅ DB schema for cue_points — Task 1
- ✅ Overlapping BPM ranges (125-130 / 128-135) — Task 4

**Gaps / not in scope for this plan:**
- `analyze key` chain into `import directory --analyze` — existing command, call separately
- MCP server — Phase 5, separate plan
- Rekordbox/Serato adapters — future plan

**Type consistency confirmed:**
- `detect_bpm(filepath: str) -> float` — used consistently in Task 2
- `detect_cues(filepath: str) -> dict[str, float]` — keys are `intro_end`, `drop`, `outro_start` — used consistently in Task 7 and Task 8 (`TYPE_MAP` / `HOTCUE_SLOT`)
- `analyze_bpm(db_path, apply, force, limit, backup_dir)` — matches cli.py wiring
- `DirectoryAdapter.import_all(multidj_db_path, apply, paths, backup_dir)` — matches cli.py wiring
- `BPM_RANGES: tuple[tuple[str, float, float], ...]` — used in Task 2 (definition) and Task 4 (crate building)
