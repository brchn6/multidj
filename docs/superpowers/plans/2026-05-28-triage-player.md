# Triage Player Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `multidj triage` — an mpv-based keyboard-driven track audition workflow that lets the user rate or soft-delete tracks in real time using numpad keys.

**Architecture:** A Lua script bundled inside the Python package registers numpad key bindings in mpv; each keypress calls `multidj triage tag --path FILE --rating N` as a subprocess, which writes to the MultiDJ DB instantly. `multidj triage` builds an M3U playlist from the DB (unrated library-wide, or all tracks in a named crate) and launches mpv with the Lua script.

**Tech Stack:** Python 3.9+ stdlib only (`sqlite3`, `subprocess`, `tempfile`, `importlib.resources`, `shutil`), Lua 5.x (mpv scripting API), mpv media player (system package, not a Python dependency).

---

## Git Setup

- [ ] **Create feature branch**

```bash
git checkout dev
git pull
git checkout -b feat/triage-player
```

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `multidj/assets/triage.lua` | mpv Lua script: key bindings, OSD, subprocess call, playlist-next |
| Create | `multidj/triage.py` | `build_triage_queue`, `write_m3u`, `tag_track`, `launch_session` |
| Create | `tests/test_triage.py` | Unit tests for `build_triage_queue`, `write_m3u`, `tag_track` |
| Modify | `multidj/cli.py` | Add `triage` parser + routing in `main()` |
| Modify | `pyproject.toml` | Add `[tool.setuptools.package-data]` for `assets/*.lua` |
| Modify | `CLAUDE.md` | Add `triage` row to commands table; add mpv to prerequisites |
| Modify | `MVP_PROGRESS.md` | Add Phase 16 row |

---

## Task 1: Lua Script

**Files:**
- Create: `multidj/assets/triage.lua`

- [ ] **Step 1: Create the assets directory and write the Lua script**

```bash
mkdir -p multidj/assets
```

Write `multidj/assets/triage.lua`:

```lua
-- multidj-triage: numpad key bindings for fast track audition.
-- Loaded only when mpv is launched via `multidj triage` (--script= flag).
-- Never installed to ~/.config/mpv/scripts/ to avoid hijacking normal mpv sessions.

local utils = require("mp.utils")

local function tag_and_next(rating, hard_delete)
    local path = mp.get_property("path")
    if not path then return end

    local args = {
        "multidj", "triage", "tag",
        "--path", path,
        "--rating", tostring(rating),
    }
    if hard_delete then
        table.insert(args, "--hard-delete")
    end
    utils.subprocess({args = args, playback_only = false})

    if rating == 0 and hard_delete then
        mp.osd_message("DELETED from disk", 2)
    elseif rating == 0 then
        mp.osd_message("Trashed", 1.5)
    else
        local filled = string.rep("*", rating)
        local empty  = string.rep("-", 5 - rating)
        mp.osd_message(filled .. empty .. "  (" .. rating .. "/5)", 1.5)
    end

    mp.commandv("playlist-next", "force")
end

mp.add_key_binding("KP0",       "triage-trash",       function() tag_and_next(0, false) end)
mp.add_key_binding("Shift+KP0", "triage-hard-delete", function() tag_and_next(0, true)  end)
mp.add_key_binding("KP1", "triage-rate-1", function() tag_and_next(1, false) end)
mp.add_key_binding("KP2", "triage-rate-2", function() tag_and_next(2, false) end)
mp.add_key_binding("KP3", "triage-rate-3", function() tag_and_next(3, false) end)
mp.add_key_binding("KP4", "triage-rate-4", function() tag_and_next(4, false) end)
mp.add_key_binding("KP5", "triage-rate-5", function() tag_and_next(5, false) end)

mp.add_key_binding("n", "triage-skip", function()
    mp.osd_message("Skipped", 1)
    mp.commandv("playlist-next", "force")
end)

-- Override default ±5s seek with ±30s
mp.add_key_binding("RIGHT", "seek-fwd-30", function() mp.commandv("seek", "30") end)
mp.add_key_binding("LEFT",  "seek-bck-30", function() mp.commandv("seek", "-30") end)
```

- [ ] **Step 2: Commit**

```bash
git add multidj/assets/triage.lua
git commit -m "feat: add mpv Lua triage script (KP0-5 bindings, 30s seek)"
```

---

## Task 2: Failing Tests for `build_triage_queue`

**Files:**
- Create: `tests/test_triage.py`

The fixture DB (`multidj_db`) has 9 active tracks (id=10 is deleted=1).
Unrated (rating=0, deleted=0): tracks 3, 5, 7, 9 → 4 tracks.
Crate "Genre: House" (crate_id=1): tracks 1, 4, 6 → 3 tracks (all rated, re-triage intentional).

- [ ] **Step 1: Write the failing tests**

Write `tests/test_triage.py`:

```python
import pytest
from pathlib import Path
from multidj.triage import build_triage_queue, write_m3u


# ── build_triage_queue ────────────────────────────────────────────────────────

def test_queue_library_wide_returns_unrated(multidj_db):
    """No crate: returns tracks with rating IS NULL or rating=0, deleted=0."""
    tracks = build_triage_queue(str(multidj_db))
    ids = [t["id"] for t in tracks]
    # Fixture unrated active tracks: 3, 5, 7, 9
    assert sorted(ids) == [3, 5, 7, 9]


def test_queue_library_wide_excludes_rated(multidj_db):
    """Rated tracks (1, 2, 4, 6, 8) must not appear in library-wide queue."""
    tracks = build_triage_queue(str(multidj_db))
    ids = {t["id"] for t in tracks}
    for rated_id in (1, 2, 4, 6, 8):
        assert rated_id not in ids


def test_queue_library_wide_excludes_deleted(multidj_db):
    """Track 10 (deleted=1) must never appear."""
    tracks = build_triage_queue(str(multidj_db))
    assert 10 not in {t["id"] for t in tracks}


def test_queue_crate_scoped(multidj_db):
    """--crate returns all non-deleted tracks in that crate, including rated ones."""
    tracks = build_triage_queue(str(multidj_db), crate="Genre: House")
    ids = sorted(t["id"] for t in tracks)
    # Genre: House crate has tracks 1, 4, 6 (all rated but re-triage is OK)
    assert ids == [1, 4, 6]


def test_queue_crate_unknown_returns_empty(multidj_db):
    """Unknown crate name returns empty list, no exception."""
    tracks = build_triage_queue(str(multidj_db), crate="No Such Crate")
    assert tracks == []


def test_queue_limit(multidj_db):
    """--limit caps the result count."""
    tracks = build_triage_queue(str(multidj_db), limit=2)
    assert len(tracks) == 2


def test_queue_track_has_required_fields(multidj_db):
    """Each returned track dict has id, path, artist, title, bpm, key, energy."""
    tracks = build_triage_queue(str(multidj_db))
    assert len(tracks) > 0
    required = {"id", "path", "artist", "title", "bpm", "key", "energy"}
    for t in tracks:
        assert required.issubset(t.keys())


# ── write_m3u ────────────────────────────────────────────────────────────────

def test_write_m3u_creates_file(tmp_path, multidj_db):
    tracks = build_triage_queue(str(multidj_db))
    out = tmp_path / "playlist.m3u"
    write_m3u(tracks, str(out))
    assert out.exists()


def test_write_m3u_header(tmp_path, multidj_db):
    tracks = build_triage_queue(str(multidj_db))
    out = tmp_path / "playlist.m3u"
    write_m3u(tracks, str(out))
    lines = out.read_text().splitlines()
    assert lines[0] == "#EXTM3U"


def test_write_m3u_contains_paths(tmp_path, multidj_db):
    tracks = build_triage_queue(str(multidj_db))
    out = tmp_path / "playlist.m3u"
    write_m3u(tracks, str(out))
    content = out.read_text()
    for t in tracks:
        assert t["path"] in content
```

- [ ] **Step 2: Run tests and confirm they all FAIL**

```bash
.venv/bin/pytest tests/test_triage.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name 'build_triage_queue' from 'multidj.triage'` (module doesn't exist yet).

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_triage.py
git commit -m "test: add failing tests for build_triage_queue and write_m3u"
```

---

## Task 3: Implement `build_triage_queue` and `write_m3u`

**Files:**
- Create: `multidj/triage.py`

- [ ] **Step 1: Write the implementation**

Create `multidj/triage.py`:

```python
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .db import connect, ensure_not_empty


def build_triage_queue(
    db_path: str | None,
    crate: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return tracks to triage as a list of dicts.

    Library-wide (crate=None): unrated active tracks (rating IS NULL OR rating=0).
    Crate-scoped: all active tracks in the named crate, including already-rated ones
    (re-triage is intentional).
    """
    with connect(db_path, readonly=True) as conn:
        if crate is not None:
            sql = """
                SELECT t.id, t.path, t.artist, t.title, t.bpm, t.key, t.energy
                FROM tracks t
                JOIN crate_tracks ct ON ct.track_id = t.id
                JOIN crates c ON c.id = ct.crate_id
                WHERE c.name = ? AND t.deleted = 0
                ORDER BY t.id
            """
            params: list[Any] = [crate]
        else:
            sql = """
                SELECT t.id, t.path, t.artist, t.title, t.bpm, t.key, t.energy
                FROM tracks t
                WHERE t.deleted = 0
                  AND (t.rating IS NULL OR t.rating = 0)
                ORDER BY t.id
            """
            params = []

        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        rows = conn.execute(sql, params).fetchall()

    return [dict(row) for row in rows]


def write_m3u(tracks: list[dict[str, Any]], path: str) -> None:
    """Write a minimal M3U playlist file with one path per line."""
    lines = ["#EXTM3U"] + [t["path"] for t in tracks]
    Path(path).write_text("\n".join(lines) + "\n")
```

- [ ] **Step 2: Run the tests and confirm they all PASS**

```bash
.venv/bin/pytest tests/test_triage.py -v -k "queue or m3u"
```

Expected output:
```
tests/test_triage.py::test_queue_library_wide_returns_unrated PASSED
tests/test_triage.py::test_queue_library_wide_excludes_rated PASSED
tests/test_triage.py::test_queue_library_wide_excludes_deleted PASSED
tests/test_triage.py::test_queue_crate_scoped PASSED
tests/test_triage.py::test_queue_crate_unknown_returns_empty PASSED
tests/test_triage.py::test_queue_limit PASSED
tests/test_triage.py::test_queue_track_has_required_fields PASSED
tests/test_triage.py::test_write_m3u_creates_file PASSED
tests/test_triage.py::test_write_m3u_header PASSED
tests/test_triage.py::test_write_m3u_contains_paths PASSED
```

- [ ] **Step 3: Commit**

```bash
git add multidj/triage.py
git commit -m "feat: implement build_triage_queue and write_m3u"
```

---

## Task 4: Failing Tests for `tag_track`

**Files:**
- Modify: `tests/test_triage.py`

- [ ] **Step 1: Append the failing tests to `tests/test_triage.py`**

```python
from multidj.triage import build_triage_queue, write_m3u, tag_track
import os


# ── tag_track ─────────────────────────────────────────────────────────────────

def test_tag_track_sets_rating(multidj_db, multidj_db_conn):
    """rating 1-5 writes to tracks.rating; deleted stays 0."""
    path = "/music/fixture/01_DJ_Tiesto_-_Red_Lights.mp3"
    tag_track(str(multidj_db), path, rating=3)
    row = multidj_db_conn.execute(
        "SELECT rating, deleted FROM tracks WHERE path=?", (path,)
    ).fetchone()
    assert row["rating"] == 3
    assert row["deleted"] == 0


def test_tag_track_zero_soft_deletes(multidj_db, multidj_db_conn):
    """rating=0 sets deleted=1 and does NOT write rating field."""
    path = "/music/fixture/01_DJ_Tiesto_-_Red_Lights.mp3"
    original_rating = multidj_db_conn.execute(
        "SELECT rating FROM tracks WHERE path=?", (path,)
    ).fetchone()["rating"]

    tag_track(str(multidj_db), path, rating=0)

    row = multidj_db_conn.execute(
        "SELECT rating, deleted FROM tracks WHERE path=?", (path,)
    ).fetchone()
    assert row["deleted"] == 1
    assert row["rating"] == original_rating  # rating unchanged; deleted=1 is the signal


def test_tag_track_unknown_path_noop(multidj_db, multidj_db_conn):
    """Unknown file path is a silent no-op — no exception, no DB change."""
    count_before = multidj_db_conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE deleted=1"
    ).fetchone()[0]

    tag_track(str(multidj_db), "/nonexistent/ghost.mp3", rating=3)

    count_after = multidj_db_conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE deleted=1"
    ).fetchone()[0]
    assert count_after == count_before


def test_tag_track_hard_delete_removes_file(multidj_db, multidj_db_conn, tmp_path):
    """--hard-delete removes the audio file from disk AND sets deleted=1 in DB."""
    # Create a real temp file to represent the track
    fake_file = tmp_path / "fake_track.mp3"
    fake_file.write_bytes(b"fake audio")

    # Insert a track row pointing at our temp file
    multidj_db_conn.execute(
        "INSERT INTO tracks (path, artist, title, deleted) VALUES (?, 'A', 'T', 0)",
        (str(fake_file),),
    )
    multidj_db_conn.commit()

    tag_track(str(multidj_db), str(fake_file), rating=0, hard_delete=True)

    assert not fake_file.exists()  # file gone from disk
    row = multidj_db_conn.execute(
        "SELECT deleted FROM tracks WHERE path=?", (str(fake_file),)
    ).fetchone()
    assert row["deleted"] == 1


def test_tag_track_hard_delete_missing_file_noop(multidj_db, multidj_db_conn):
    """--hard-delete with a file already gone from disk: DB write succeeds, no exception."""
    path = "/music/fixture/01_DJ_Tiesto_-_Red_Lights.mp3"  # in DB but not on disk in tests
    tag_track(str(multidj_db), path, rating=0, hard_delete=True)  # must not raise
    row = multidj_db_conn.execute(
        "SELECT deleted FROM tracks WHERE path=?", (path,)
    ).fetchone()
    assert row["deleted"] == 1


def test_tag_track_marks_dirty(multidj_db):
    """Tagging a track triggers the sync_state dirty flag for 'mixxx' adapter."""
    import sqlite3
    path = "/music/fixture/01_DJ_Tiesto_-_Red_Lights.mp3"

    # Reset dirty to 0 using a fresh connection (same file as triage will use)
    conn = sqlite3.connect(str(multidj_db))
    conn.row_factory = sqlite3.Row
    track_id = conn.execute("SELECT id FROM tracks WHERE path=?", (path,)).fetchone()["id"]
    conn.execute("UPDATE sync_state SET dirty=0 WHERE track_id=?", (track_id,))
    conn.commit()
    conn.close()

    tag_track(str(multidj_db), path, rating=4)

    # Re-open to read the result (tag_track commits its own connection)
    conn2 = sqlite3.connect(str(multidj_db))
    conn2.row_factory = sqlite3.Row
    dirty = conn2.execute(
        "SELECT dirty FROM sync_state WHERE track_id=?", (track_id,)
    ).fetchone()["dirty"]
    conn2.close()
    assert dirty == 1
```

- [ ] **Step 2: Run tests to confirm the new ones FAIL**

```bash
.venv/bin/pytest tests/test_triage.py -v -k "tag_track"
```

Expected: `ImportError` on `tag_track` (not yet implemented).

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_triage.py
git commit -m "test: add failing tests for tag_track"
```

---

## Task 5: Implement `tag_track`

**Files:**
- Modify: `multidj/triage.py`

- [ ] **Step 1: Add `tag_track` to `multidj/triage.py`**

Add after the `write_m3u` function:

```python
def tag_track(
    db_path: str | None,
    file_path: str,
    rating: int,
    hard_delete: bool = False,
) -> None:
    """Write a triage decision to the DB. Called by the Lua script as a subprocess.

    rating=0 → soft-delete (deleted=1). hard_delete=True also removes file from disk.
    rating 1-5 → set rating field. Unknown path is a silent no-op.
    No dry-run gate — keypress is the apply.
    """
    with connect(db_path, readonly=False) as conn:
        if rating == 0:
            conn.execute(
                "UPDATE tracks SET deleted = 1 WHERE path = ? AND deleted = 0",
                (file_path,),
            )
            conn.commit()
            if hard_delete:
                try:
                    os.unlink(file_path)
                except OSError:
                    pass  # file already gone — DB write still stands
        else:
            conn.execute(
                "UPDATE tracks SET rating = ? WHERE path = ? AND deleted = 0",
                (rating, file_path),
            )
            conn.commit()
```

- [ ] **Step 2: Run the tag_track tests and confirm they PASS**

```bash
.venv/bin/pytest tests/test_triage.py -v -k "tag_track"
```

Expected:
```
tests/test_triage.py::test_tag_track_sets_rating PASSED
tests/test_triage.py::test_tag_track_zero_soft_deletes PASSED
tests/test_triage.py::test_tag_track_unknown_path_noop PASSED
tests/test_triage.py::test_tag_track_marks_dirty PASSED
```

- [ ] **Step 3: Run the full triage test file**

```bash
.venv/bin/pytest tests/test_triage.py -v
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add multidj/triage.py
git commit -m "feat: implement tag_track — rating write and soft-delete"
```

---

## Task 6: Implement `launch_session`

**Files:**
- Modify: `multidj/triage.py`

This function is not unit-tested (it spawns mpv). It is integration-only.

- [ ] **Step 1: Add the required imports at the top of `multidj/triage.py`**

The top of the file should already have `import shutil`, `import subprocess`, `import sys`, `import tempfile`. Verify and add any missing ones. Also add:

```python
from pathlib import Path
```

- [ ] **Step 2: Add `launch_session` to `multidj/triage.py`**

Add after `tag_track`:

```python
def launch_session(
    db_path: str | None,
    crate: str | None = None,
    limit: int | None = None,
) -> None:
    """Build a triage queue and launch mpv with the bundled Lua script.

    Requires mpv in PATH. Blocks until mpv exits. Cleans up the temp M3U on exit.
    """
    if shutil.which("mpv") is None:
        print("mpv not found — install it with: dnf install mpv", file=sys.stderr)
        sys.exit(1)

    tracks = build_triage_queue(db_path, crate=crate, limit=limit)
    if not tracks:
        print("No tracks to triage.")
        sys.exit(0)

    lua_path = Path(__file__).parent / "assets" / "triage.lua"

    m3u_fd, m3u_path = tempfile.mkstemp(suffix=".m3u", prefix="multidj_triage_")
    try:
        os.close(m3u_fd)
        write_m3u(tracks, m3u_path)
        subprocess.run(
            [
                "mpv",
                f"--playlist={m3u_path}",
                f"--script={lua_path}",
                "--no-video",
            ],
            check=False,
        )
    finally:
        try:
            os.unlink(m3u_path)
        except OSError:
            pass
```

- [ ] **Step 3: Verify the full triage test file still passes**

```bash
.venv/bin/pytest tests/test_triage.py -v
```

Expected: all tests PASS (launch_session has no tests — that's correct).

- [ ] **Step 4: Commit**

```bash
git add multidj/triage.py
git commit -m "feat: implement launch_session — mpv subprocess with Lua script"
```

---

## Task 7: Wire CLI

**Files:**
- Modify: `multidj/cli.py`

- [ ] **Step 1: Add the triage import at the top of `cli.py`**

After the existing imports (around line 24, after `from .report import write_dashboard_report`), add:

```python
from .triage import launch_session, tag_track
```

- [ ] **Step 2: Add the triage parser to `build_parser()` in `cli.py`**

Add after the `# ── report ───` block (around line 295, before `return parser`):

```python
    # ── triage ───────────────────────────────────────────────────────────────
    triage_p = sub.add_parser("triage", help="Fast keyboard-driven track audition (requires mpv)")
    triage_p.add_argument("--crate", default=None,
                          help="Limit session to tracks in this named crate")
    triage_p.add_argument("--limit", type=int, default=None,
                          help="Cap number of tracks in the session")
    triage_sub = triage_p.add_subparsers(dest="triage_target")
    p_tag = triage_sub.add_parser("tag", help="Internal: write a triage decision (called by Lua)")
    p_tag.add_argument("--path",        required=True,
                       help="Absolute path to the audio file")
    p_tag.add_argument("--rating",      type=int, required=True, choices=range(0, 6),
                       help="0=trash (soft-delete or hard-delete), 1-5=quality rating")
    p_tag.add_argument("--hard-delete", action="store_true", dest="hard_delete",
                       help="Also remove the file from disk (only valid with --rating 0)")
```

- [ ] **Step 3: Add the triage routing to `main()` in `cli.py`**

Add after the `elif args.command == "report":` block (before the final `else:` clause):

```python
    elif args.command == "triage":
        if args.triage_target == "tag":
            tag_track(args.db, args.path, args.rating, hard_delete=args.hard_delete)
            return 0
        else:
            launch_session(args.db, crate=args.crate, limit=args.limit)
            return 0
```

- [ ] **Step 4: Verify the parser works**

```bash
.venv/bin/multidj triage --help
```

Expected output includes `--crate`, `--limit`, and subcommand `tag`.

```bash
.venv/bin/multidj triage tag --help
```

Expected output includes `--path` and `--rating`.

- [ ] **Step 5: Run the full test suite to verify no regressions**

```bash
.venv/bin/pytest tests/ -v 2>&1 | tail -20
```

Expected: all existing 132 tests + new triage tests PASS.

- [ ] **Step 6: Commit**

```bash
git add multidj/cli.py
git commit -m "feat: wire triage command and triage tag subcommand to CLI"
```

---

## Task 8: Package Data

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add package-data section to `pyproject.toml`**

Add after the `[tool.setuptools.packages.find]` block at the end of the file:

```toml
[tool.setuptools.package-data]
multidj = ["assets/*.lua"]
```

- [ ] **Step 2: Verify the Lua file is included in the package**

```bash
.venv/bin/python -c "
from pathlib import Path
lua = Path('multidj/assets/triage.lua')
print('exists:', lua.exists())
print('size:', lua.stat().st_size, 'bytes')
"
```

Expected: `exists: True` and a non-zero size.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: include multidj/assets/*.lua in package data"
```

---

## Task 9: Documentation Updates

**Files:**
- Modify: `CLAUDE.md`
- Modify: `MVP_PROGRESS.md`

- [ ] **Step 1: Add `triage` to the commands table in `CLAUDE.md`**

In the Commands table, add a new row after the `dedupe` row:

```markdown
| `triage` | Keyboard-driven track audition via mpv: KP0=trash, KP1–5=rating, n=skip; `--crate`, `--limit` (requires mpv) |
```

- [ ] **Step 2: Add mpv to the installation prerequisites in `CLAUDE.md`**

In the "Installation and Running" section, after the pip install lines, add:

```markdown
# Optional: mpv media player (required for `multidj triage`)
# Fedora/RHEL: sudo dnf install mpv
# Ubuntu/Debian: sudo apt install mpv
# macOS: brew install mpv
```

- [ ] **Step 3: Add Phase 16 to the roadmap table in `MVP_PROGRESS.md`**

In the "Roadmap Phase Status" table, add after the Phase 15 row:

```markdown
| 16 | **Triage player** — `multidj triage` keyboard-driven audition via mpv + Lua | **Done** |
```

Also add the triage command to the "Completed Features → Commands" checklist:

```markdown
- [x] `triage` — mpv-based keyboard audition: KP0=trash, KP1–5=rating, n=skip; `--crate`, `--limit`; `triage tag` write subcommand called by Lua
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md MVP_PROGRESS.md
git commit -m "docs: document triage command in CLAUDE.md and MVP_PROGRESS.md"
```

---

## Task 10: Final Verification

- [ ] **Step 1: Run the complete test suite**

```bash
.venv/bin/pytest tests/ -v 2>&1 | tail -30
```

Expected: all tests PASS. Count should be 132 (existing) + 16 (new triage tests) = 148 total.

- [ ] **Step 2: Verify the CLI end-to-end (no mpv needed)**

```bash
# Help screens
.venv/bin/multidj triage --help
.venv/bin/multidj triage tag --help

# Triage tag against real DB (if available)
# multidj triage tag --path "/home/barc/Music/All_Tracks/some_track.mp3" --rating 3
```

- [ ] **Step 3: Check git log looks clean**

```bash
git log --oneline feat/triage-player ^dev
```

Expected: 8–10 clean commits, no fixups or reverts.

- [ ] **Step 4: Open PR to `dev`**

```bash
gh pr create \
  --base dev \
  --title "feat: triage player — mpv + Lua keyboard-driven track audition (Phase 16)" \
  --body "$(cat <<'EOF'
## Summary
- Adds \`multidj triage\` command: builds a queue from the DB and launches mpv with a bundled Lua script
- Numpad KP0=trash (soft-delete), KP1-5=rating, n=skip, ←/→=±30s seek
- Each keypress calls \`multidj triage tag\` as a subprocess → instant DB write
- \`--crate NAME\`: scope session to a named crate; default: all unrated tracks library-wide
- \`--limit N\`: cap session size
- Lua script shipped as package data (\`multidj/assets/triage.lua\`), not installed globally
- No new DB migrations — uses existing \`rating\` and \`deleted\` fields

## Test plan
- [ ] \`pytest tests/test_triage.py -v\` — all 14 new tests pass
- [ ] \`pytest tests/ -v\` — all 148 tests pass (no regressions)
- [ ] \`multidj triage --help\` and \`multidj triage tag --help\` render correctly
- [ ] Manual: \`multidj triage --limit 5\` with mpv installed plays tracks and writes decisions
EOF
)"
```
