# Dedup Enhancement Plan

## Problem

Current dedup (`multidj/dedupe.py`) only matches by exact `(artist, title)` and exact `(filesize, duration)`. It misses:

| Pattern | Tracks affected | Example |
|---|---|---|
| Title containment (same artist, one title contains the other) | 69 | `Dancing Queen` vs `Dancing Queen (Official Lyric Video)` |
| YouTube suffix noise | 45 | `(Official Audio)`, `(Lyric Video)`, `(Remaster)` |
| Free-DL suffix noise | 36 | `[FREE DL]`, `(FREE D/L)`, `[FREE DOWNLOAD]` |

## Changes to `multidj/dedupe.py`

### 1. Add suffix-stripping normalization

Add a regex to strip common suffixes before comparing titles in `_find_groups()`:

```python
_SUFFIX_STRIP = re.compile(
    r"""
    \s*[\[\(] ( Official \s+ (Audio|Lyric\s*Video|Music\s*Video) ) [\]\)] \s*$
    |\s*[\[\(] ( Audio | Lyric\s*Video | Music\s*Video | Extended | Remaster | Single\s*Version ) [\]\)] \s*$
    |\s*[\[\(] FREE \s* ( DL | D/L | DOWNLOAD | DOWLOAD ) [\]\)] \s*$
    |\s*[\[\(] \d{4} \s* Remaster [\]\)] \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)
```

In `_find_groups`, add a new matching mode (e.g., `by="artist-title-normalized"` or as a fallback in the existing `artist-title` branch):

- After grouping by `(artist, title)`, also compute `normalized_title = _SUFFIX_STRIP.sub("", title).strip()`
- Group by `(artist, normalized_title)` as a separate pass
- Only mark as duplicate if `filename_hash != filename_hash` (to avoid marking the exact same track)

### 2. Run dedup after clean_text in pipeline

In `multidj/pipeline.py`, the current order is:

```
dedupe → bpm → key → energy → embed → cluster → cues → genres → clean_text → crates → sync
```

`clean_text` strips `[FREE DL]`, `(FREE DOWNLOAD)` etc. from titles. If dedup runs **after** clean_text, many of these near-duplicates would become exact `(artist, title)` matches and be caught by the existing logic.

**Fix:** Either:
- (a) Move `clean_text` before `dedupe` in the pipeline, or
- (b) Add a second `dedupe` pass after `clean_text`

Option (b) is safer — keep the initial dedup for early cleanup, then catch the remaining after text normalization.

### 3. Update `_keeper_sort_key` to prefer non-suffixed titles

When choosing which track to keep, prefer the one without YouTube/free-download noise:

```python
def _keeper_sort_key(track: dict) -> tuple:
    has_noise = bool(_SUFFIX_STRIP.search(track.get("title", "") or ""))
    return (
        has_noise,           # prefer clean titles (False sorts before True)
        -(track["play_count"] or 0),
        -(track["rating"] or 0),
        -(track["filesize"] or 0),
    )
```

## Verification

After implementing:

```bash
multidj dedupe --apply
multidj sync mixxx --apply
```

Then check Mixxx for remaining duplicates.
