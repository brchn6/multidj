# MultiDJ — Memory Index

This directory is the persistent project memory for MultiDJ.
It documents progress, decisions, and context that would otherwise be lost between sessions.

**Last updated:** 2026-06-25

---

## Files

| File | Contents |
|---|---|
| [PROGRESS.md](PROGRESS.md) | Chronological log of every phase and milestone with hard-coded dates |
| [DECISIONS.md](DECISIONS.md) | Architectural decisions with dates and reasoning — the "why" behind the code |
| [INDEX.md](INDEX.md) | This file |

---

## Quick Reference

**Current branch:** `dev` (mirrored to `master`) — both pushed, identical at `cb3a775`
**Test count:** 370 (2026-06-25, 0 failures)
**All phases complete as of 2026-06-25**

**Entry points:**
```bash
multidj <command>     # primary
mixxx-tool <command>  # legacy alias
```

**★ Everyday workflow (priority #1 — must stay simple):**
```bash
multidj import directory --apply   # pick up new files; reads music_dir from config
multidj sync mixxx --apply         # push dirty tracks + crates to Mixxx
```

**Full pipeline (with analysis):**
```bash
multidj pipeline --apply   # 4 phases / 19 steps; all paths from config
```

**Key modules:**
| Module | Purpose |
|---|---|
| `cli.py` | argparse entry point, all subcommands |
| `db.py` | SQLite connection, migration runner |
| `pipeline.py` | 4-phase / 19-step orchestrator (`--phase ingest\|analyze\|enrich\|sync`) |
| `embed.py` | CLAP/CLaMP3 audio embeddings |
| `embed_clamp3.py` | CLaMP3 backend (MERT → 768-dim) |
| `cluster.py` | UMAP + HDBSCAN → Vibe/ crates |
| `suggest.py` | DJ next-track ranking |
| `enrich.py` | Three-layer metadata enrichment (file→Discogs→MusicBrainz) |
| `enrich_genre.py` | Layered genre hardening with provenance (genre_source/genre_confidence) |
| `mixxx_blobs.py` | BeatGrid-2.0 + KeyMap-1.0 BLOB encoder; logs SKIPPED/WROTE per track |
| `import_mixxx_analysis.py` | One-way pull of Mixxx's own BPM/key into MultiDJ |
| `cues.py` | Cue detection (allin1 + librosa); self-annotates Mixxx slots 0/1/2 |

**Standalone scripts:**
| Script | Purpose |
|---|---|
| `scripts/viz_library.py` | Interactive UMAP HTML scatter plot |
| `scripts/diagnostics.py` | 6-panel data science dashboard |
| `scripts/genre_detect.py` | Zero-shot CLAP genre classification |

---

## Key Invariants (never break these)

1. `--apply` required for all mutations; dry-run is default
2. MultiDJ DB is the source of truth; Mixxx is downstream
3. `connect(readonly=True)` skips migrations — open write connection before reading new columns
4. `source='manual'` cue points are never overwritten by auto-detection
5. Hand-curated crates are protected; only auto-prefixed crates (`Genre:`, `BPM:`, `Key:`, `Energy:`, `Lang:`, `Vibe/`) are rebuilt automatically
6. One backup per pipeline run (at the top); steps get `backup_dir=False`
7. CLAP for clustering/similarity; CLaMP3 only for future text→audio agent queries
8. **MultiDJ NEVER deletes hot cues from Mixxx** — `cues clear` clears MultiDJ DB only; Mixxx cues persist
9. **Mixxx DB path = Dropbox** (`/home/barc/Weizmann Institute Dropbox/Bar Cohen/Music/.mixxx/mixxxdb.sqlite`); `~/.mixxx/mixxxdb.sqlite` is a symlink to it — never use the local path directly

---

## Things that must NEVER come back

- **`multidj triage` / mpv audition** — permanently removed 2026-06-25; user uses Mixxx custom keyboard shortcuts. Do not re-add under any name. If it reappears via a branch merge, delete it immediately.
- **Global `DELETE FROM cues WHERE hotcue IN (0,1,2)`** in `full_sync` — removed by design; MultiDJ never wipes Mixxx hot cues.
- **`[mixxx].path` pointing to local `~/.mixxx/...`** — Dropbox path is always correct.

---

## Known Gaps (active pain points)

### ⚠️ BPM into Mixxx — UNSOLVED
`sync mixxx` fills `library.bpm` when Mixxx has none, but a raw float without a BeatGrid
BLOB is unstable (Mixxx shows it inconsistently). The reliable path: `analyze bpm` →
`analyze mixxx-blobs` (BeatGrid-2.0 BLOB). Stopgap isolated in commit `8fe23be`. See DECISIONS.md.

### Auto-cues Phase 2 — not yet built
Read-before-write skip-occupied + 3→8 slot expansion not implemented.
Design spec: `docs/superpowers/specs/2026-06-20-auto-cues-design.md`

---

## What's Not Done Yet

- Reliable BPM → Mixxx via BeatGrid BLOB (replace the stopgap)
- Auto-cues Phase 2 (read-before-write slot protection, 8 slots)
- Rekordbox adapter (design exists; `MixxxAdapter` is the template)
- Serato adapter
- MCP server (all commands are JSON-ready)
- Text→audio agent vibe search (CLaMP3 ready; CLI not wired)
