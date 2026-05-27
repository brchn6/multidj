# MultiDJ — Embeddings & Semantic Clustering Design Spec

**Date:** 2026-05-27
**Status:** Approved — ready for implementation plan
**Scope:** Phase 12 (embeddings + vibe crates) + long-term vision (LLM-instructable playlist building)

---

## 1. Goal

Add a semantic embedding layer to MultiDJ that encodes every track as a 512-dimensional vector in a shared audio+meaning latent space, then clusters those vectors into human-readable `Vibe/` crates automatically. This is the foundation for the long-term goal: a DJ that can ask "build me a dark techno peak-hour playlist for a 2am set" and get back a musically coherent, ready-to-play tracklist.

---

## 2. Encoder: CLAP

**Model:** `laion/larger_clap_music` (~400 MB, HuggingFace Hub)

CLAP (Contrastive Language-Audio Pretraining) is trained on music+text pairs. Its latent space captures both timbral character (how the track sounds) and semantic meaning (what kind of music it is) in a single 512-dim vector. One encoder handles everything — no concatenation or multi-model complexity.

**Why not MERT:** MERT produces richer audio-only embeddings but requires ~15–30 sec/track on CPU, making 2.5k tracks impractical without a dedicated GPU. CLAP runs at ~1–3 sec/track on CPU.

**Why not Essentia:** Faster than CLAP but produces shallower embeddings with less semantic richness. Harder to install (C++ bindings).

**GPU auto-detection:** PyTorch selects CUDA/MPS if available, falls back to CPU. No config required.

**Audio sampling strategy:** Sample three 30-second windows per track (beginning, middle, end) and average the embeddings. This mirrors the BPM analysis approach and prevents intros/outros from dominating the vector.

---

## 3. Data Model

### New migration: `004_embeddings.sql`

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS embeddings
USING vec0(
    track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    model_name TEXT NOT NULL,
    embedding FLOAT[512]
);
```

sqlite-vec stores vectors as compact BLOBs. Its `vec_distance_cosine()` function enables KNN queries later (e.g. "find 10 tracks similar to this one") without re-running UMAP.

`model_name` allows detecting stale embeddings when the model version changes.

**No changes to `tracks` table.** Embeddings are a sidecar, not a core track property.

### Crate prefix: `Vibe/`

Added to `AUTO_CRATE_PREFIXES` in `constants.py` alongside `Genre:/BPM:/Key:/Energy:/Lang:`. Vibe crates are type `auto`, protected from manual deletion, cleared and rebuilt on every `cluster vibe` run.

---

## 4. New Modules

### `multidj/embed.py`

Loads the CLAP model, encodes audio files, stores embeddings.

- **Incremental:** only encodes tracks without a stored embedding (or `--force`)
- **Per-track error isolation:** one corrupt file never kills the batch
- **Progress to stderr:** same pattern as `analyze.py`
- **Model download:** auto-downloaded on first use via HuggingFace Hub; errors cleanly if download fails

### `multidj/cluster.py`

Reads all embeddings, clusters them, names clusters via LLM, writes `Vibe/` crates.

**Clustering pipeline:**
1. Load all embeddings from sqlite-vec
2. UMAP: 512d → 10d (dimensionality reduction preserving neighborhood structure)
3. HDBSCAN: find natural clusters in 10d space (no fixed cluster count)
4. For each cluster: sample up to 20 tracks, collect artist/title/genre/BPM/key
5. LLM call: send sample metadata → get back a 2–3 word evocative crate name
6. Write `Vibe/<name>` crates, assign tracks
7. Outliers (HDBSCAN noise points) → `Vibe/Unclassified`

**Fallback naming:** if `[llm]` is not configured, clusters get numbered names (`Vibe/Cluster-01`). Never aborts over a naming failure.

---

## 5. LLM Configuration

Uses any OpenAI-compatible API endpoint. Configured in `~/.multidj/config.toml`:

```toml
[llm]
base_url = "https://opencode.ai/api/v1"   # or any OpenAI-compatible provider
api_key  = "sk-..."
model    = "deepseek/deepseek-chat"        # or whichever model you prefer
```

**Dependency:** `openai` Python package (handles any OpenAI-compatible endpoint).

**Token usage:** minimal. Each cluster naming call sends ~20 track titles + genres and requests one short string back. At 2.5k tracks with ~30 clusters, total naming cost is negligible.

**Fallback:** if `[llm]` is absent, use numbered names. The cluster command is still fully functional offline.

---

## 6. New Optional Dependency Group

```toml
[project.optional-dependencies]
embeddings = [
    "torch>=2.0",
    "transformers>=4.40",
    "sqlite-vec>=0.1",
    "umap-learn>=0.5",
    "hdbscan>=0.8",
    "openai>=1.0",
]
```

Installed with: `uv sync --extra embeddings`

Auto-skip in pipeline if not installed (same pattern as `[analysis]` for librosa).

---

## 7. CLI Commands

### `multidj analyze embed`

```
multidj analyze embed [--apply] [--force] [--limit N]
```

- Dry-run by default: reports how many tracks need embedding, estimated time
- `--apply`: encode and store vectors
- `--force`: re-embed already-encoded tracks (e.g. after model upgrade)
- `--limit N`: encode first N unembedded tracks (for testing)
- Skips tracks whose files don't exist on disk

### `multidj cluster vibe`

```
multidj cluster vibe [--apply] [--min-cluster-size N] [--prefix Vibe/]
```

- Dry-run: prints proposed cluster sizes and LLM-generated names without writing crates
- `--apply`: write `Vibe/` crates and assign tracks
- `--min-cluster-size`: default 5 (matches `MIN_TRACKS_DEFAULT`)
- `--prefix`: defaults to `Vibe/`; change to namespace differently if desired
- Guards: requires at least `min_cluster_size * 2` embedded tracks; errors clearly if too few

### `multidj similar <path-or-id>`

*(Phase 12b — follow-on command)*

```
multidj similar "Artist - Title.mp3" [--top N]
```

Queries sqlite-vec KNN to return the N most similar tracks by cosine distance in embedding space. Read-only. Useful for "what plays well after this track?" without re-running UMAP.

---

## 8. Pipeline Integration

Two new steps inserted after `energy`:

```
import → fix_mismatches → parse → dedupe → bpm → key → energy →
embed → cluster → clean_genres → clean_text → crates → sync → report
```

Config toggles in `~/.multidj/config.toml`:

```toml
[pipeline]
embed   = true
cluster = true
```

Both steps auto-skip if `[embeddings]` is not installed. `cluster` auto-skips if fewer tracks are embedded than `min_cluster_size * 2`.

One backup at pipeline start covers both steps (existing behavior unchanged).

---

## 9. Error Handling

| Failure | Behavior |
|---------|----------|
| Model download fails | Clear error, no DB writes, points to install docs |
| Bad audio file | Per-track try/except, log error, continue batch |
| LLM unreachable | Falls back to numbered names, logs warning, does not abort |
| Too few embedded tracks | Error with count and minimum threshold |
| UMAP/HDBSCAN failure | Propagates as step error in pipeline (non-fatal), logged |
| Missing `[llm]` config | Detected at startup of cluster command, falls back to numbered names |

---

## 10. Testing Strategy

### `tests/test_embed.py`
- Mock CLAP model entirely (stub returning a fixed 512-dim vector)
- Verify embeddings are stored in sqlite-vec
- Verify incremental logic (already-embedded tracks skipped)
- Verify `--force` re-embeds
- Verify per-track error isolation

### `tests/test_cluster.py`
- Inject synthetic embeddings: two obvious clusters of 10 tracks each
- Verify correct number of `Vibe/` crates created
- Verify `Vibe/Unclassified` created for noise points
- Verify crate lifecycle: clear-and-rebuild on second run
- LLM naming injected as callable stub (returns `"Test Name"`) — no real API call

Both follow existing fixture pattern: `tmp_path` isolated DB, `make_multidj_db()` from fixtures.

---

## 11. Long-Term Vision: LLM-Instructable DJ

This embedding layer is the semantic foundation for a conversational DJ assistant. The full vision:

> "I'm playing a late-night warehouse party, 2am, peak hour. Dark melodic techno. I need a 90-minute playlist that builds slowly then goes hard. Start with something hypnotic around 128 BPM and peak at 136."

The system would:

1. **Parse intent** via LLM → extract constraints (mood, BPM range, key arc, duration, energy arc)
2. **Seed from Vibe crates** → candidate tracks matching the semantic profile using CLAP cosine similarity
3. **Apply harmonic constraints** → Camelot wheel compatibility (already in `CAMELOT_KEY_MAP`)
4. **Apply BPM progression** → gradual tempo arc within the requested window
5. **Sequence the playlist** → graph traversal through the similarity space respecting all constraints
6. **Output** → ordered tracklist as a new hand-curated crate, exportable to Mixxx

### How this builds on Phase 12:

- `Vibe/` crates provide the semantic pool to draw from
- `multidj similar` provides the neighborhood graph for sequencing
- The MCP server (Phase 11) exposes all of this as agent-callable tools
- The LLM acts as the "DJ brain" orchestrating tools, not doing the analysis itself

### Implementation phases:

| Phase | Feature | Depends on |
|-------|---------|------------|
| 12 | CLAP embeddings + `Vibe/` crates | This spec |
| 12b | `multidj similar` KNN query | Phase 12 |
| 13 | `multidj playlist build` — constraint-based playlist generator | Phase 12 + Camelot logic |
| 14 | MCP tools for embedding/playlist queries | Phase 11 (MCP server) + Phase 13 |
| 15 | Natural language playlist requests via MCP | Phase 14 |

The knowledge graph idea (explicit nodes + typed edges between tracks) becomes relevant at Phase 13+, when you have multiple relationship types: `SIMILAR_TO` (CLAP cosine), `HARMONICALLY_COMPATIBLE_WITH` (Camelot), `TEMPO_ADJACENT` (BPM proximity), and eventually `PLAYED_AFTER` (from set history). At that point a graph traversal layer on top of sqlite-vec makes sense.

---

## 12. What We Are NOT Building in Phase 12

- Real-time similarity during mixing (latency-sensitive, out of scope)
- Lyrics analysis (requires Whisper transcription pipeline — future phase)
- Knowledge graph infrastructure (deferred to Phase 13+ when multiple edge types exist)
- Cross-track "played together" relationships (requires set history data we don't have yet)
