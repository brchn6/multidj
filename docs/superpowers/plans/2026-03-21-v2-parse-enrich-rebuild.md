# V2: parse + enrich language + crates rebuild — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three new commands — `parse`, `enrich language`, and `crates rebuild` — to turn messy filenames and fragmented auto-crates into clean, DJ-usable metadata and a tight crate list.

**Architecture:** Each command follows the existing pattern: pure function in its own module, dry-run by default, `--apply` for writes, `backup.create_backup()` before mutations, `executemany()` for batched DB writes, `utils.emit()` for output. Language detection lives in `enrich.py` as a reusable helper and is imported by `crates.py` to avoid duplication.

**Tech Stack:** Python 3.9+ stdlib only (re, unicodedata, sqlite3). No new dependencies.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `mixxx_tool/parse.py` | **Create** | Filename → `{artist, title, remixer, featuring}` extraction via regex heuristics |
| `mixxx_tool/enrich.py` | **Create** | `is_hebrew()` helper + `enrich_language()` command function |
| `mixxx_tool/crates.py` | **Modify** | Add `rebuild_crates()` — delete old auto-crates, create Genre: + Lang: crates |
| `mixxx_tool/constants.py` | **Modify** | Add `REBUILD_CRATE_RE`, `NOISE_PREFIX_RE`, `CAMELOT_SUFFIX_RE`, `DUPLICATE_SUFFIX_RE` |
| `mixxx_tool/cli.py` | **Modify** | Wire up `parse`, `enrich language`, `crates rebuild` subcommands |

> **Note:** No test files exist in this repo (intentionally deferred per MVP_PROGRESS.md). Skip all test steps. Verify behavior manually with dry-run output.

---

## Filename Pattern Catalogue
*(derived from real library scan — keep these in mind during Task 1)*

| Pattern | Example | Strategy |
|---|---|---|
| `Artist - Title.ext` | `ABBA - Waterloo.mp3` | HIGH confidence: split on ` - ` |
| `Artist - Title - Source.ext` | `Alex Derron - Mad World (Original mix) - Alex Derron.mp3` | MEDIUM: 3-part, part[2] is uploader noise |
| `Artist - Title (X Remix) - Source.ext` | `50 Cent - In Da Club (Grummel Remix) - Grummel.mp3` | MEDIUM: extract remixer from parens |
| `Artist - Title [Camelot analysis].ext` | `ABBA - Dancing Queen - [11B][E0.87][...].opus` | Strip the `[NA][E...][D...][][L - X.YdB]` suffix first |
| Numeric prefix | `2 David Penn - Losing You - Dj PETTO.mp3` | Strip `^\d+\s*[-x]\s*` or `^\d+\s+` before parsing |
| Marketing prefix | `(BUY =) Post Malone - Wow...` | Strip `^\([^)]*\)\s*` patterns |
| Underscore format | `ABBA_Dancing_Queen_101bpm_11B.opus` | Replace `_` with space, then parse |
| Featuring in title | `Ozuna feat Daddy Yankee - Baila Rmx` | Extract `feat\.?\s+(.+?)` before `(` or ` - ` |
| Duplicate files | `ABBA - Dancing Queen.1.opus` | Strip `\.\d+\.ext` → low confidence unless only copy |
| Hebrew title | `אביעד סהר - מחרוזת חתונות (Amar's Official Remix)` | Detected by Unicode range, not regex structure |

---

## Task 1 — `constants.py`: Add shared regex constants

**Files:**
- Modify: `mixxx_tool/constants.py`

- [ ] **Add the four new constants** to the bottom of `constants.py`:

```python
# Camelot/audio-analysis tags appended by some tagging tools.
# Matches: [11B][E0.87][D0.54][][L - 6.5dB]  or  [10A][E0.81][D0.85][][L - 8.3dB]
CAMELOT_SUFFIX_RE = re.compile(
    r"\s*[-–]?\s*\[[0-9]{1,2}[AB]\](\[E[0-9.]+\])?(\[D[0-9.]+\])?\[\]\[L\s*-\s*[0-9.]+dB\]",
    re.IGNORECASE,
)

# Numeric or marketing junk that prefixes many filenames.
# Matches: "2 - ", "100 x ", "(BUY =) ", "[FREE DOWNLOAD] ", "#DKMIX "
NOISE_PREFIX_RE = re.compile(
    r"^(?:\d+\s*[-x]\s+|\d+\s+|\([^)]*\)\s*|\[[^\]]*\]\s*|#\S+\s*)+",
    re.IGNORECASE,
)

# Duplicate-copy suffix: "Song.1.mp3" → strip ".1" before extension.
DUPLICATE_SUFFIX_RE = re.compile(r"\.\d+$")

# Auto-crate prefixes for rebuild operations (superset of AUTO_CRATE_PREFIXES).
# Matches Genre:, BPM:, Lang: prefixes (case-insensitive).
REBUILD_CRATE_RE = re.compile(r"^(Genre:\s|BPM:\s|Lang:\s)", re.IGNORECASE)
```

- [ ] **Update `AUTO_CRATE_PREFIXES`** to include `Lang:` so existing hide/delete commands recognise it:

```python
AUTO_CRATE_PREFIXES = re.compile(r"^(Genre:\s|BPM:\s|Lang:\s)", re.IGNORECASE)
```

- [ ] **Verify** `python -c "from mixxx_tool.constants import REBUILD_CRATE_RE, NOISE_PREFIX_RE, CAMELOT_SUFFIX_RE; print('ok')"` prints `ok`.

- [ ] **Commit:** `git add mixxx_tool/constants.py && git commit -m "feat: add v2 regex constants to constants.py"`

---

## Task 2 — `parse.py`: Filename metadata extractor

**Files:**
- Create: `mixxx_tool/parse.py`

- [ ] **Create `mixxx_tool/parse.py`** with this exact content:

```python
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .backup import create_backup
from .constants import CAMELOT_SUFFIX_RE, DUPLICATE_SUFFIX_RE, NOISE_PREFIX_RE
from .db import connect

_ACTIVE = "mixxx_deleted = 0"

# Remix/edit/bootleg/flip inside parens or brackets.
_REMIX_RE = re.compile(
    r"[\(\[]\s*(.+?)\s+"
    r"(remix|edit|bootleg|flip|rework|re-?work|mashup|mash-?up|version|mix|dub)\s*[\)\]]",
    re.IGNORECASE,
)

# feat. / ft. / featuring — may appear in artist or title position.
_FEAT_RE = re.compile(
    r"\bfeat(?:uring|\.?)?\s+(.+?)(?=\s*[\(\[–\-]|$)",
    re.IGNORECASE,
)

# Separator used to split filename into parts.
_SEP = re.compile(r"\s+-\s+")


def _strip_extension(name: str) -> str:
    """Remove file extension."""
    return Path(name).stem


def _strip_noise(stem: str) -> tuple[str, bool]:
    """Strip leading noise prefixes. Returns (cleaned, had_noise)."""
    cleaned = NOISE_PREFIX_RE.sub("", stem).strip()
    return cleaned, cleaned != stem


def _strip_camelot(stem: str) -> str:
    """Remove trailing Camelot/analysis tags."""
    return CAMELOT_SUFFIX_RE.sub("", stem).strip()


def _strip_duplicate_suffix(stem: str) -> str:
    """Remove trailing .N duplicate counters (applied before extension strip)."""
    return DUPLICATE_SUFFIX_RE.sub("", stem).strip()


def _extract_remix(text: str) -> tuple[str, str | None]:
    """Extract remixer from title. Returns (title_without_remix_tag, remixer_or_None)."""
    m = _REMIX_RE.search(text)
    if not m:
        return text, None
    remixer = m.group(1).strip()
    # Keep the parens in the title but extract the name separately.
    return text, remixer


def _extract_feat(text: str) -> tuple[str, str | None]:
    """Extract featuring artist from text. Returns (cleaned_text, featuring_or_None)."""
    m = _FEAT_RE.search(text)
    if not m:
        return text, None
    featuring = m.group(1).strip()
    cleaned = _FEAT_RE.sub("", text).strip().rstrip("(").strip()
    return cleaned, featuring


def _confidence(parts: list[str], had_noise: bool, is_underscore: bool) -> str:
    """
    Assign confidence:
      high   — clean 2-part split, no noise
      medium — 3-part split (third part stripped as uploader noise), or had light noise
      low    — 4+ parts, underscore format, or very short/long artist field
    """
    if is_underscore:
        return "low"
    if len(parts) >= 4:
        return "low"
    if len(parts) == 2 and not had_noise:
        return "high"
    return "medium"


def parse_filename(filename: str) -> dict[str, Any]:
    """
    Parse a single filename into structured metadata.
    Returns a dict with keys:
      stem, artist, title, remixer, featuring, confidence, notes
    """
    notes: list[str] = []

    # 1. Remove extension and duplicate counter (.1, .2 etc.)
    stem = _strip_extension(filename)
    stem = _strip_duplicate_suffix(stem)

    # 2. Handle underscore-delimited names (ABBA_Dancing_Queen_101bpm_...).
    is_underscore = "_" in stem and " - " not in stem
    if is_underscore:
        stem = stem.replace("_", " ")
        notes.append("underscore_format")

    # 3. Strip Camelot/analysis suffix before splitting.
    stem = _strip_camelot(stem)

    # 4. Strip noise prefixes.
    stem, had_noise = _strip_noise(stem)
    if had_noise:
        notes.append("noise_prefix_stripped")

    # 5. Split on " - ".
    parts = [p.strip() for p in _SEP.split(stem) if p.strip()]

    if not parts:
        return {
            "stem": Path(filename).stem,
            "artist": None, "title": None,
            "remixer": None, "featuring": None,
            "confidence": "low", "notes": ["empty_after_cleaning"],
        }

    # 6. Assign artist / title from parts.
    # Part 0 → artist.  Part 1 → title.  Part 2+ → uploader noise (dropped).
    if len(parts) == 1:
        # No separator at all — treat whole thing as title, no artist.
        artist_raw, title_raw = None, parts[0]
    else:
        artist_raw, title_raw = parts[0], parts[1]
        if len(parts) >= 3:
            notes.append(f"dropped_suffix: {' - '.join(parts[2:])}")

    # 7. Extract featuring from artist or title.
    featuring: str | None = None
    if artist_raw:
        artist_raw, feat_a = _extract_feat(artist_raw)
        if feat_a:
            featuring = feat_a
    if title_raw and not featuring:
        title_raw, feat_t = _extract_feat(title_raw)
        if feat_t:
            featuring = feat_t

    # 8. Extract remixer from title.
    remixer: str | None = None
    if title_raw:
        title_raw, remixer = _extract_remix(title_raw)

    confidence = _confidence(parts, had_noise, is_underscore)

    return {
        "stem": Path(filename).stem,
        "artist": artist_raw.strip() if artist_raw else None,
        "title": title_raw.strip() if title_raw else None,
        "remixer": remixer,
        "featuring": featuring,
        "confidence": confidence,
        "notes": notes,
    }


def parse_library(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    min_confidence: str = "medium",
    backup: bool = True,
) -> dict[str, Any]:
    """
    Propose or apply filename-derived metadata to the library table.

    Skips tracks that already have both artist AND title set (unless --force).
    Only writes fields that are currently empty (or all fields if --force).
    """
    mode = "apply" if apply else "dry_run"
    confidence_rank = {"high": 2, "medium": 1, "low": 0}
    min_rank = confidence_rank.get(min_confidence, 1)

    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(f"""
            SELECT l.id, l.artist, l.title, tl.location AS filepath
            FROM library l
            JOIN track_locations tl ON l.location = tl.id
            WHERE l.{_ACTIVE}
        """).fetchall()

    planned: list[dict[str, Any]] = []
    skipped_confident: int = 0
    skipped_low_confidence: int = 0

    for row in rows:
        track_id = row["id"]
        current_artist = row["artist"] or ""
        current_title = row["title"] or ""
        filename = Path(row["filepath"]).name

        # Skip if both fields are already populated and not forcing.
        if current_artist and current_title and not force:
            skipped_confident += 1
            continue

        parsed = parse_filename(filename)

        # Skip below min_confidence.
        if confidence_rank.get(parsed["confidence"], 0) < min_rank:
            skipped_low_confidence += 1
            continue

        # Build the change dict — only fields that are empty (or force).
        change: dict[str, Any] = {"track_id": track_id, "filepath": row["filepath"]}
        has_change = False

        for field in ("artist", "title"):
            new_val = parsed[field]
            current_val = row[field] or ""
            if new_val and (not current_val or force) and new_val != current_val:
                change[f"old_{field}"] = current_val or None
                change[f"new_{field}"] = new_val
                has_change = True

        if not has_change:
            continue

        change["confidence"] = parsed["confidence"]
        change["remixer"] = parsed["remixer"]
        change["featuring"] = parsed["featuring"]
        change["notes"] = parsed["notes"]
        planned.append(change)

    if limit is not None:
        planned = planned[:limit]

    if apply and planned:
        if backup:
            create_backup(db_path)
        artist_updates = [(c["new_artist"], c["track_id"]) for c in planned if "new_artist" in c]
        title_updates  = [(c["new_title"],  c["track_id"]) for c in planned if "new_title"  in c]
        with connect(db_path, readonly=False) as conn:
            if artist_updates:
                conn.executemany("UPDATE library SET artist = ? WHERE id = ?", artist_updates)
            if title_updates:
                conn.executemany("UPDATE library SET title = ? WHERE id = ?", title_updates)
            conn.commit()

    return {
        "mode": mode,
        "total_tracks": len(rows),
        "skipped_already_tagged": skipped_confident,
        "skipped_low_confidence": skipped_low_confidence,
        "total_changes": len(planned),
        "changes": planned,
    }
```

- [ ] **Smoke test** (dry-run against your library):

```bash
python -m mixxx_tool parse --limit 20
```

Expected: see a table of proposed changes with confidence levels. No writes.

- [ ] **Commit:** `git add mixxx_tool/parse.py && git commit -m "feat: add parse command for filename metadata extraction"`

---

## Task 3 — `enrich.py`: Language detection

**Files:**
- Create: `mixxx_tool/enrich.py`

- [ ] **Create `mixxx_tool/enrich.py`** with this exact content:

```python
from __future__ import annotations

from typing import Any

from .db import connect

_ACTIVE = "mixxx_deleted = 0"

# Hebrew Unicode blocks:
#   U+0590–U+05FF  — Hebrew
#   U+FB1D–U+FB4F  — Hebrew Presentation Forms
_HEBREW_RANGES = (
    (0x0590, 0x05FF),
    (0xFB1D, 0xFB4F),
)


def is_hebrew(text: str | None) -> bool:
    """Return True if text contains at least one Hebrew character."""
    if not text:
        return False
    return any(
        lo <= ord(ch) <= hi
        for ch in text
        for lo, hi in _HEBREW_RANGES
    )


def enrich_language(db_path: str | None = None) -> dict[str, Any]:
    """
    Detect Hebrew tracks by scanning title and artist fields.
    Read-only — no DB writes. Use 'crates rebuild' to act on results.
    """
    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(f"""
            SELECT id, artist, title
            FROM library
            WHERE {_ACTIVE}
        """).fetchall()

    total = len(rows)
    hebrew_tracks: list[dict[str, Any]] = []

    for row in rows:
        if is_hebrew(row["title"]) or is_hebrew(row["artist"]):
            hebrew_tracks.append({
                "track_id": row["id"],
                "artist": row["artist"],
                "title": row["title"],
            })

    hebrew_count = len(hebrew_tracks)
    pct = round(hebrew_count / total * 100, 1) if total else 0.0

    return {
        "total_active_tracks": total,
        "hebrew_tracks": hebrew_count,
        "hebrew_pct": pct,
        "tracks": hebrew_tracks,
    }
```

- [ ] **Smoke test:**

```bash
python -c "
from mixxx_tool.enrich import is_hebrew
assert is_hebrew('Dancing Queen') is False
assert is_hebrew('מחרוזת חתונות') is True
assert is_hebrew(None) is False
print('is_hebrew: ok')
"
```

- [ ] **Commit:** `git add mixxx_tool/enrich.py && git commit -m "feat: add enrich.py with is_hebrew helper and enrich_language command"`

---

## Task 4 — `crates.py`: Add `rebuild_crates()`

**Files:**
- Modify: `mixxx_tool/crates.py`

- [ ] **Add import** at top of `crates.py`:

```python
from .constants import AUTO_CRATE_PREFIXES, CATCH_ALL_CRATE_NAMES, REBUILD_CRATE_RE
from .enrich import is_hebrew
```

(Replace the existing `from .constants import AUTO_CRATE_PREFIXES, CATCH_ALL_CRATE_NAMES` line.)

- [ ] **Append `rebuild_crates()` function** to the bottom of `crates.py`:

```python
def rebuild_crates(
    db_path: str | None = None,
    min_tracks: int = MIN_TRACKS_DEFAULT,
    apply: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    """
    Rebuild auto-crates from scratch:
      1. Delete all existing Genre:, BPM:, and Lang: crates (and their track assignments).
      2. Create Genre: <canonical> crates for genres with >= min_tracks active tracks.
      3. Create Lang: Hebrew crate if >= min_tracks Hebrew tracks exist.
    Hand-curated and catch-all crates are never touched.
    """
    mode = "apply" if apply else "dry_run"

    with connect(db_path, readonly=True) as conn:
        # --- existing auto-crates to remove ---
        old_auto = [
            {"crate_id": r["id"], "name": r["name"]}
            for r in conn.execute("SELECT id, name FROM crates").fetchall()
            if REBUILD_CRATE_RE.match(r["name"] or "")
        ]

        # --- genre groups ---
        genre_rows = conn.execute(f"""
            SELECT genre, COUNT(*) AS cnt
            FROM library
            WHERE mixxx_deleted = 0
              AND genre IS NOT NULL AND TRIM(genre) != ''
            GROUP BY genre
            HAVING cnt >= ?
            ORDER BY cnt DESC, genre
        """, (min_tracks,)).fetchall()
        genre_groups = [{"name": f"Genre: {r['genre']}", "genre": r["genre"], "count": r["cnt"]}
                        for r in genre_rows]

        # --- Hebrew tracks ---
        all_tracks = conn.execute(f"""
            SELECT id, artist, title FROM library WHERE mixxx_deleted = 0
        """).fetchall()
        hebrew_ids = [r["id"] for r in all_tracks
                      if is_hebrew(r["title"]) or is_hebrew(r["artist"])]

        # --- track id lookup by genre ---
        genre_track_map: dict[str, list[int]] = {}
        for g in genre_groups:
            track_ids = [
                r["id"] for r in conn.execute(f"""
                    SELECT id FROM library
                    WHERE mixxx_deleted = 0 AND genre = ?
                """, (g["genre"],)).fetchall()
            ]
            genre_track_map[g["genre"]] = track_ids

    # Build crates-to-create list
    crates_to_create: list[dict[str, Any]] = []
    for g in genre_groups:
        crates_to_create.append({
            "name": g["name"],
            "track_ids": genre_track_map[g["genre"]],
            "track_count": g["count"],
        })

    lang_crate: dict[str, Any] | None = None
    if len(hebrew_ids) >= min_tracks:
        lang_crate = {
            "name": "Lang: Hebrew",
            "track_ids": hebrew_ids,
            "track_count": len(hebrew_ids),
        }
        crates_to_create.append(lang_crate)

    # Re-query skipped genres for reporting (read-only conn above is already closed)
    with connect(db_path, readonly=True) as conn:
        skipped_genres = conn.execute(f"""
            SELECT genre, COUNT(*) AS cnt
            FROM library
            WHERE mixxx_deleted = 0
              AND genre IS NOT NULL AND TRIM(genre) != ''
            GROUP BY genre
            HAVING cnt < ?
            ORDER BY cnt DESC
        """, (min_tracks,)).fetchall()
        skipped_below = [{"genre": r["genre"], "count": r["cnt"]} for r in skipped_genres]

    if apply:
        if backup:
            create_backup(db_path)
        with connect(db_path, readonly=False) as conn:
            # 1. Delete old auto-crates
            if old_auto:
                old_ids = [c["crate_id"] for c in old_auto]
                ph = ",".join("?" * len(old_ids))
                conn.execute(f"DELETE FROM crate_tracks WHERE crate_id IN ({ph})", old_ids)
                conn.execute(f"DELETE FROM crates WHERE id IN ({ph})", old_ids)

            # 2. Create new crates and populate
            total_assignments = 0
            for crate in crates_to_create:
                conn.execute(
                    "INSERT INTO crates (name, show, locked, autodj_source) VALUES (?, 1, 0, 0)",
                    (crate["name"],),
                )
                crate_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                pairs = [(crate_id, tid) for tid in crate["track_ids"]]
                conn.executemany(
                    "INSERT OR IGNORE INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
                    pairs,
                )
                total_assignments += len(pairs)

            conn.commit()

    return {
        "mode": mode,
        "min_tracks_threshold": min_tracks,
        "old_auto_crates_deleted": len(old_auto),
        "crates_created": len(crates_to_create),
        "genre_crates": len(genre_groups),
        "lang_hebrew_crate": lang_crate is not None,
        "lang_hebrew_tracks": len(hebrew_ids),
        "skipped_genres_below_threshold": len(skipped_below),
        "skipped_genres": skipped_below,
        "crates": [{"name": c["name"], "track_count": c["track_count"]}
                   for c in crates_to_create],
        "deleted": old_auto,
    }
```

- [ ] **Smoke test** (dry-run):

```bash
python -m mixxx_tool crates rebuild
```

Expected: shows old auto-crates to delete, new Genre:/Lang: crates to create, no writes.

- [ ] **Commit:** `git add mixxx_tool/crates.py && git commit -m "feat: add crates rebuild command"`

---

## Task 5 — `cli.py`: Wire up all three commands

**Files:**
- Modify: `mixxx_tool/cli.py`

- [ ] **Add imports** at the top of `cli.py` (after existing imports):

```python
from .enrich import enrich_language
from .parse import parse_library
from .crates import audit_crates, delete_crates, hide_crates, show_crates, rebuild_crates
```

(The crates import just needs `rebuild_crates` added to the existing line.)

- [ ] **Add `parse` subparser** in `build_parser()` after the `scan` block:

```python
# ── parse ─────────────────────────────────────────────────────────────────
p = sub.add_parser("parse", help="Propose metadata from filenames (artist, title, remixer)")
p.add_argument("--apply",          action="store_true", help="Write proposed changes to DB")
p.add_argument("--force",          action="store_true", help="Overwrite non-empty fields")
p.add_argument("--limit",          type=int,            help="Cap number of changes")
p.add_argument("--no-backup",      action="store_true", help="Skip backup before apply")
p.add_argument("--min-confidence", choices=["high", "medium", "low"], default="medium",
               help="Minimum confidence to include (default: medium)")
```

- [ ] **Add `enrich` subparser** after `audit`:

```python
# ── enrich ────────────────────────────────────────────────────────────────
enrich_p = sub.add_parser("enrich", help="Enrich track metadata from external signals")
enrich_sub = enrich_p.add_subparsers(dest="enrich_target", required=True)
enrich_sub.add_parser("language", help="Detect Hebrew tracks (Unicode range check)")
```

- [ ] **Add `crates rebuild` subparser** inside the existing `crates_sub` block:

```python
p = crates_sub.add_parser("rebuild", help="Delete old auto-crates and rebuild Genre: + Lang: crates")
p.add_argument("--min-tracks", type=int, default=5)
p.add_argument("--apply",      action="store_true")
p.add_argument("--no-backup",  action="store_true")
```

- [ ] **Add dispatch branches** in `main()`:

After the `scan` block, add:

```python
elif args.command == "parse":
    result = parse_library(
        args.db,
        apply=args.apply,
        force=args.force,
        limit=args.limit,
        min_confidence=args.min_confidence,
        backup=not args.no_backup,
    )
```

After the `audit` block, add:

```python
elif args.command == "enrich":
    result = enrich_language(args.db)
```

Inside the `crates` elif, the current catch-all `else:  # delete` must first become an explicit branch, then add `rebuild` before it:

```python
# IMPORTANT: change the existing bare `else:` to `elif args.crates_target == "delete":` first,
# then add the rebuild branch above it:
elif args.crates_target == "rebuild":
    result = rebuild_crates(
        args.db,
        min_tracks=args.min_tracks,
        apply=args.apply,
        backup=not args.no_backup,
    )
elif args.crates_target == "delete":   # was: else:  ← must be made explicit
    result = delete_crates(...)
```

- [ ] **Full end-to-end dry-run verification:**

```bash
python -m mixxx_tool parse --limit 10
python -m mixxx_tool parse --limit 10 --min-confidence high
python -m mixxx_tool enrich language
python -m mixxx_tool crates rebuild
python -m mixxx_tool --json enrich language | python -m json.tool | head -20
```

- [ ] **Commit:** `git add mixxx_tool/cli.py && git commit -m "feat: wire parse, enrich language, crates rebuild into CLI"`

---

## Task 6 — Update `emit()` for human-readable parse output

**Files:**
- Modify: `mixxx_tool/cli.py`

The raw `emit()` dict dump is ugly for `parse` results (hundreds of change dicts). Add a human formatter for `parse` in `cli.py`, modelled on how `scan` does it.

- [ ] **Add `format_parse()` helper** in `cli.py` just below the `format_scan` import:

```python
def _format_parse(data: dict) -> str:
    lines = [
        f"Parse — {data['total_changes']} proposed changes  "
        f"({data['skipped_already_tagged']} already tagged, "
        f"{data['skipped_low_confidence']} below confidence threshold)",
        "",
    ]
    for c in data["changes"][:50]:
        conf = c["confidence"].upper()
        fp = c["filepath"].split("/")[-1][:55]
        lines.append(f"  [{conf}] {fp}")
        if "new_artist" in c:
            old_a = c.get('old_artist') or '(empty)'
            lines.append(f"         artist: {old_a!r} → {c['new_artist']!r}")
        if "new_title" in c:
            old_t = c.get('old_title') or '(empty)'
            lines.append(f"         title:  {old_t!r} → {c['new_title']!r}")
        if c.get("remixer"):
            lines.append(f"         remixer: {c['remixer']}")
        if c.get("featuring"):
            lines.append(f"         feat:    {c['featuring']}")
    if data["total_changes"] > 50:
        lines.append(f"  ... and {data['total_changes'] - 50} more (use --json for full list)")
    return "\n".join(lines)
```

- [ ] **Use it in the parse dispatch branch:**

```python
elif args.command == "parse":
    result = parse_library(...)
    emit(_format_parse(result) if not args.json else result, as_json=args.json)
    continue  # skip the final emit() call
```

Actually — follow the same pattern used for `scan`: set result and return early, or emit inline and `return 0`. Model it exactly like the scan branch:

```python
elif args.command == "parse":
    data = parse_library(
        args.db, apply=args.apply, force=args.force,
        limit=args.limit, min_confidence=args.min_confidence,
        backup=not args.no_backup,
    )
    emit(_format_parse(data) if not args.json else data, as_json=args.json)
    return 0
```

- [ ] **Similarly format `enrich language` output** — add `_format_enrich_language()`:

```python
def _format_enrich_language(data: dict) -> str:
    total = data["total_active_tracks"]
    n = data["hebrew_tracks"]
    pct = data["hebrew_pct"]
    bar_w = 20
    filled = round(n / total * bar_w) if total else 0
    bar = "█" * filled + "░" * (bar_w - filled)
    lines = [
        f"Language detection — {total:,} active tracks",
        "",
        f"  Hebrew  {n:>5,} / {total:,}  {pct:>5.1f}%  {bar}",
        "",
    ]
    sample = data["tracks"][:10]
    if sample:
        lines.append("  Sample Hebrew tracks:")
        for t in sample:
            lines.append(f"    [{t['track_id']}] {t['artist'] or ''} — {t['title'] or ''}")
        if n > 10:
            lines.append(f"    ... and {n - 10} more (use --json for full list)")
    return "\n".join(lines)
```

And update the enrich dispatch:

```python
elif args.command == "enrich":
    data = enrich_language(args.db)
    emit(_format_enrich_language(data) if not args.json else data, as_json=args.json)
    return 0
```

- [ ] **Final verification of all outputs:**

```bash
python -m mixxx_tool parse --limit 20
python -m mixxx_tool enrich language
python -m mixxx_tool crates rebuild
python -m mixxx_tool --json crates rebuild | python -m json.tool | head -30
```

- [ ] **Commit:** `git add mixxx_tool/cli.py && git commit -m "feat: add human-readable formatters for parse and enrich language"`

---

## Task 7 — Update docs

**Files:**
- Modify: `README.md`
- Modify: `MVP_PROGRESS.md`
- Modify: `CLAUDE.md`

- [ ] **Add to `README.md`** Commands table and usage examples for the 3 new commands.

- [ ] **Update `MVP_PROGRESS.md`**: mark v2 commands as complete, move from Pending to Completed.

- [ ] **Update `CLAUDE.md`** Commands table.

- [ ] **Commit:** `git add README.md MVP_PROGRESS.md CLAUDE.md && git commit -m "docs: update for v2 commands"`

---

## Recommended execution order

```
Task 1 (constants) → Task 2 (parse.py) → Task 3 (enrich.py)
→ Task 4 (crates rebuild) → Task 5 (cli wiring) → Task 6 (formatters) → Task 7 (docs)
```

Tasks 2 and 3 are independent and can be done in parallel.

## Known edge cases to watch

| Edge case | Handling |
|---|---|
| Filename with no ` - ` separator | Returns `title=whole_stem, artist=None, confidence=low` |
| Hebrew filename with good `Artist - Title` structure | Still parsed correctly; language detection is separate |
| Genre with apostrophe or special chars in crate name | SQLite handles fine via parameterised queries |
| Duplicate ABBA files (`.1.opus`, `.opus`, `.mp3`) | All three will be parsed; deduplication is a separate step |
| `crates rebuild --apply` with empty genre field | Genre=NULL rows excluded by `WHERE genre IS NOT NULL` filter |
| `parse --apply --force` on 1800 tracks | `--limit` flag available to cap blast radius |

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
