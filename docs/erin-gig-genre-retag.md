# Erin_gig Genre Re-Tag Plan (from Exportify CSVs)

Created 2026-08-12. Owner: tomorrow's session (promised by user).

## Problem

All 255 Erin_gig tracks in Mixxx have genre `Music` / `Entertainment` /
`People & Blogs` - these came from **YouTube's video category** (yt-dlp writes
it into the ID3 genre tag when downloading). The Exportify CSVs that were
fetched carry the **real Spotify genres** (e.g. `מסיבה_ספרדית.csv` rows have
`brazilian funk, brazilian pop, reggaeton, latin...`) but cb never wrote them
to the files. Result: Mixxx genre crates (`Genre: Afro House`, `Genre: BOUNCE`,
...) can't be used for the gig, and the library looks wrong.

## Facts (verified 2026-08-12)

- Gig folder: `~/Weizmann Institute Dropbox/Bar Cohen/Music/Erin_gig/` (255 mp3)
- CSVs (source of truth for genres): `~/Weizmann Institute Dropbox/Bar Cohen/DEL/*.csv`
  - `מסיבה_עברית.csv`, `מסיבה_מיינסטרים_מצומצם.csv`, `מסיבה_אחר.csv`,
    `מסיבה_ספרדית.csv`, `התחלה_של_מסיבה.csv`, `Happiness_💃_Yana_Darom_&_Miss_Albi.csv`
- CSV columns include `Track Name`, `Artist Name(s)`, `Genres`
- mutagen 1.47.0 available in the multidj venv: `/home/barc/dev/multidj/.venv/bin/python`
- Mixxx DB: `~/Weizmann Institute Dropbox/Bar Cohen/Music/.mixxx/mixxxdb.sqlite`
  (local `~/.mixxx/mixxxdb.sqlite` is a symlink to it, self-healed by mixxx-safe)
- MultiDJ source of truth: `~/Weizmann Institute Dropbox/Bar Cohen/Music/.multidj/library.sqlite`

## Plan

1. **Write tags from CSVs** - new script `scripts/` in this repo or
   `~/.pi/agent/skills/dj-sync/scripts/dj-tag-genres.py`:
   - For each CSV: parse rows, match each file under `Erin_gig/<csv-stem>/`
     to its row by normalized artist+title (reuse the normalize logic from
     `multidj/dedupe.py` `_norm_title` + LOWER/TRIM on artist).
   - Write the first genre (or the joined list, decided below) into the ID3
     `TCON` tag with mutagen.
   - Skip files already tagged with a non-YouTube-category genre.
2. **Re-import** - `~/.pi/agent/skills/dj-sync/scripts/dj-sync.sh`
   (import reads tags and updates changed genre; then syncs to Mixxx; it
   also self-heals the symlink and verifies at the end).
3. **Verify** - count Erin_gig tracks per genre in Mixxx; expect zero
   `Music`-category leftovers; report the genre distribution.

## Open decision (ask user)

Single genre (first of the CSV list) vs full comma-joined list in the tag.
Recommendation: full list, so Mixxx genre crates can bucket flexibly.

## Safety

- Follow the usual rules: lock check before Mixxx-DB writes (dj-sync.sh does it),
  backups happen automatically (mixxx-safe --backup at end of dj-sync.sh),
  never delete anything, report verification numbers.
- Do NOT touch the 41 tracks that matched existing library copies - their
  genre fix goes through the surviving library copy, not the gig file.
