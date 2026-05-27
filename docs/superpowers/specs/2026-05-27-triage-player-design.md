# Triage Player — Design Spec

**Date:** 2026-05-27
**Branch:** `feat/triage-player` (off `dev`)
**Phase:** 16 (new roadmap entry)

---

## Overview

`multidj triage` is a keyboard-driven track audition workflow. It launches mpv with a
playlist built from the MultiDJ DB, and a bundled Lua script maps numpad keys to rating
and soft-delete decisions that are written back to the DB in real time. The goal is to
let a DJ blaze through tens or hundreds of tracks quickly, tagging each with a quality
tier or trashing it, with those decisions feeding downstream ML (embeddings, clustering,
similarity).

mpv owns the screen. Python owns the DB.

---

## Scope

**In scope:**
- `multidj triage` — build queue, launch mpv
- `multidj triage tag` — internal write subcommand called by Lua
- `multidj/assets/triage.lua` — Lua script shipped in the package
- CLAUDE.md update (commands table + mpv dependency note)

**Out of scope:**
- Any TUI or Python-side UI
- Undo / session replay
- Tagging fields beyond `rating` and `deleted`
- Playlist reordering / resume-from-last-position

---

## Architecture

Three new pieces; no existing files change except `cli.py` (routing) and `CLAUDE.md` (docs).

```
multidj/
  triage.py            # new command module
  assets/
    triage.lua         # new Lua script, shipped with package
```

### `triage.py` responsibilities

1. `build_triage_queue(db_path, crate=None, limit=None)` → `list[dict]`
   - Queries `tracks` where `deleted=0`
   - If `crate` given: joins through `crates`/`crate_tracks` to filter; includes already-rated tracks (re-triage is intentional)
   - Otherwise: filters `rating IS NULL` (unrated tracks only)
   - Applies `LIMIT` if given
   - Returns list of `{id, path, artist, title, bpm, key, energy}`

2. `write_m3u(tracks, path)` → writes M3U playlist to given path

3. `launch_session(db_path, crate=None, limit=None)` → entry point for `multidj triage`
   - Calls `build_triage_queue()`
   - Checks `shutil.which("mpv")` — exits with clear error if missing
   - Writes temp M3U via `tempfile.NamedTemporaryFile(suffix=".m3u", delete=False)`
   - Resolves path to bundled `assets/triage.lua` via `importlib.resources`
   - Calls `subprocess.run(["mpv", "--playlist=<m3u>", "--script=<lua>", "--no-video"])`
   - Cleans up temp M3U in `finally` block

4. `tag_track(db_path, file_path, rating, hard_delete=False)` → entry point for `multidj triage tag`
   - `rating=0, hard_delete=False`: `UPDATE tracks SET deleted=1` (soft-delete; file stays on disk)
   - `rating=0, hard_delete=True`: soft-delete in DB **and** `os.unlink(file_path)` — permanently removes the audio file
   - `rating=1–5`: `UPDATE tracks SET rating=? WHERE path=? AND deleted=0`
   - No `--apply` gate — keypress is the user's explicit action
   - If `hard_delete=True` but file does not exist on disk: DB write still happens, unlink silently skipped
   - `sync_state` dirty trigger fires automatically on any `UPDATE tracks`
   - Prints nothing (called from Lua subprocess, output is discarded)

### `assets/triage.lua`

Loaded only when mpv is launched via `multidj triage` (passed via `--script=`).
Does **not** live in `~/.config/mpv/scripts/` — it would hijack numpad keys in all mpv sessions.

```lua
local utils = require("mp.utils")

local function tag_and_next(rating, hard_delete)
    local path = mp.get_property("path")
    if not path then return end

    local args = {"multidj", "triage", "tag", "--path", path, "--rating", tostring(rating)}
    if hard_delete then
        table.insert(args, "--hard-delete")
    end
    utils.subprocess({args = args, playback_only = false})

    if rating == 0 and hard_delete then
        mp.osd_message("DELETED from disk", 2)
    elseif rating == 0 then
        mp.osd_message("Trashed", 1.5)
    else
        local stars = string.rep("*", rating) .. string.rep("-", 5 - rating)
        mp.osd_message(stars .. "  (" .. rating .. "/5)", 1.5)
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

mp.add_key_binding("RIGHT", "seek-fwd-30", function() mp.commandv("seek", "30") end)
mp.add_key_binding("LEFT",  "seek-bck-30", function() mp.commandv("seek", "-30") end)
```

---

## CLI Interface

```
multidj triage                         # all unrated tracks, library-wide
multidj triage --crate "Genre:Techno"  # tracks in a named crate
multidj triage --limit 50              # cap session at 50 tracks

multidj triage tag --path FILE --rating N              # internal; soft-delete or rate
multidj triage tag --path FILE --rating 0 --hard-delete  # internal; rm file from disk
```

`triage tag` is a write command (always applies, no dry-run). It is not intended for
direct user invocation but must be accessible as a CLI command for the Lua subprocess.

---

## Key Bindings Reference

| Key | Action | DB write | File |
|-----|--------|----------|------|
| `KP0` | Soft-delete | `deleted=1` | stays on disk |
| `Shift+KP0` | Hard delete | `deleted=1` | **rm from disk** |
| `KP1`–`KP5` | Rate 1–5 | `rating=N` | untouched |
| `n` | Skip (defer) | none | untouched |
| `→` | Seek +30s | none | untouched |
| `←` | Seek -30s | none | untouched |
| `q` | Quit | none | untouched |

---

## Data Flow

```
multidj triage [--crate NAME] [--limit N]
  → query DB → list of track paths
  → write /tmp/multidj_triage_XXXX.m3u
  → exec mpv --playlist=<m3u> --script=<triage.lua> --no-video
  → [mpv plays, user hears track, scrubs with ← →]

[user presses KP3]
  → Lua: path = mp.get_property("path")
  → Lua: subprocess(["multidj", "triage", "tag", "--path", path, "--rating", "3"])
  → Python: UPDATE tracks SET rating=3 WHERE path=? AND deleted=0
  → sync_state dirty trigger fires (track marked for sync to Mixxx)
  → Lua: osd_message("***-- (3/5)")
  → Lua: playlist-next

[user presses KP0]
  → Lua: subprocess(["multidj", "triage", "tag", "--path", path, "--rating", "0"])
  → Python: UPDATE tracks SET deleted=1 WHERE path=?   (file stays on disk)

[user presses Shift+KP0]
  → Lua: subprocess(["multidj", "triage", "tag", "--path", path, "--rating", "0", "--hard-delete"])
  → Python: UPDATE tracks SET deleted=1 WHERE path=?
  → Python: os.unlink(path)   (file removed from disk — irreversible)

[mpv exits / playlist exhausted]
  → Python finally: os.unlink(m3u_path)
```

---

## Package Data

`pyproject.toml` must include `multidj/assets/triage.lua` in package data so it ships
with the installed package. The Lua path is resolved at runtime via `importlib.resources`
(Python 3.9+), which is already the project's minimum version.

```toml
[tool.setuptools.package-data]
multidj = ["assets/*.lua"]
```

---

## Error Handling

| Condition | Behaviour |
|-----------|-----------|
| `mpv` not in PATH | Print `"mpv not found — install it with: dnf install mpv"` and exit 1 |
| Empty triage queue | Print `"No tracks to triage"` and exit 0 |
| `triage tag` path not in DB | Silent no-op (track may have been deleted between session build and keypress) |
| `--hard-delete` but file missing on disk | DB soft-delete still written; `os.unlink` skipped silently |
| mpv exits non-zero | Temp M3U still cleaned up; exit code propagated |

---

## Documentation Updates

### CLAUDE.md commands table — new row:
```
| `triage` | Fast keyboard-driven audition: KP0=trash, KP1–5=rating; --crate, --limit |
```

### CLAUDE.md dependencies note:
Add `mpv` (system package) to the installation prerequisites alongside `librosa`.

### MVP_PROGRESS.md:
Add Phase 16 row: `triage player — mpv + Lua triage workflow`.

---

## Testing Strategy

- `tests/test_triage.py` — unit tests for `build_triage_queue()` and `tag_track()` using
  the standard `tmp_path` + `multidj_db` fixture pattern
- No mpv process spawned in tests — `launch_session()` is not unit-tested (integration
  only, requires mpv installed)
- Key cases: crate filter, limit, rating write, deleted=1 on rating=0, path-not-found
  no-op, empty queue exit

---

## Git Workflow

1. Branch from `dev`: `git checkout -b feat/triage-player dev`
2. All implementation commits go to `feat/triage-player`
3. PR targets `dev` (not `main`/`master`)
4. No force-push; no `--no-verify`
