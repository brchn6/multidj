# MultiDJ — Memory Index

This directory is the persistent project memory for MultiDJ.
It documents progress, decisions, and context that would otherwise be lost between sessions.

**Last updated:** 2026-06-20

---

## Files

| File | Contents |
|---|---|
| [PROGRESS.md](PROGRESS.md) | Chronological log of every phase and milestone with hard-coded dates |
| [DECISIONS.md](DECISIONS.md) | Architectural decisions with dates and reasoning — the "why" behind the code |
| [INDEX.md](INDEX.md) | This file |

---

## Quick Reference

**Current branch:** `dev`
**Test count:** 366 (2026-06-20, 0 failures)
**All phases complete as of 2026-06-20**

**Entry points:**
```bash
multidj <command>     # primary
mixxx-tool <command>  # legacy alias
```

**Daily workflow:**
```bash
multidj pipeline --apply   # import → analyze → cluster → crates → sync
```

**Key modules:**
| Module | Purpose |
|---|---|
| `cli.py` | argparse entry point, all subcommands |
| `db.py` | SQLite connection, migration runner |
| `pipeline.py` | 17-step orchestrator |
| `embed.py` | CLAP/CLaMP3 audio embeddings |
| `embed_clamp3.py` | CLaMP3 backend (MERT → 768-dim) |
| `cluster.py` | UMAP + HDBSCAN → Vibe/ crates |
| `suggest.py` | DJ next-track ranking |
| `enrich.py` | Three-layer metadata enrichment |
| `mixxx_blobs.py` | BeatGrid/KeyMap BLOB encoder (no protobuf dep) |
| `cues.py` | Cue detection (allin1 + librosa) |

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

---

## What's Not Done Yet (as of 2026-06-20)

- Rekordbox adapter (design exists; `MixxxAdapter` is the template)
- Serato adapter
- MCP server (was in original spec; all commands are JSON-ready)
- Text→audio agent vibe search (CLaMP3 is ready; CLI not wired)
- Push to `origin/dev` (pending user decision)
