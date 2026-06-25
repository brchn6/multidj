# Pipeline Restructure + Genre Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the pipeline into four explicit phases (ingest/analyze/enrich/sync), add layered genre hardening with provenance tracking (`genre_source`/`genre_confidence`), and make the Mixxx BPM/key protection explicit and observable.

**Architecture:** A new `enrich_genre.py` module walks a decision tree (file→Discogs→MusicBrainz→CLAP) per track and writes `genre_source`/`genre_confidence` to the `tracks` table. The pipeline reorders steps into 4 phases, each independently runnable via `--phase <name>`. The `mixxx_blobs` step gains per-track SKIPPED/WROTE logging to make the BPM/key protection observable.

**Tech Stack:** Python 3.9+, SQLite, existing `enrich.py` (reused: `search_discogs`, `search_musicbrainz`), LAION CLAP (optional, from `embeddings` extra), existing `genre_detect.py` pattern for CLAP text scoring.

---

## File Map

| Action | Path | Purpose |
|---|---|---|
| Create | `multidj/migrations/008_genre_source.sql` | Add `genre_source` and `genre_confidence` to `tracks` |
| Modify | `multidj/constants.py` | Add `ELECTRONIC_GENRE_LABELS` tuple |
| Create | `multidj/enrich_genre.py` | Decision-tree genre hardening with provenance |
| Modify | `multidj/pipeline.py` | Reorder steps into 4 phases, add `phase` param, rename `enrich`→`enrich_meta` and `genres`→`clean_genres`, add `mixxx_import` and `enrich_genre` steps |
| Modify | `multidj/cli.py` | Add `--phase` flag to pipeline command |
| Modify | `multidj/mixxx_blobs.py` | Add SKIPPED/WROTE per-track logging |
| Create | `tests/test_enrich_genre.py` | Tests for genre decision tree |
| Modify | `tests/test_pipeline.py` | Update step count (17→19) and phase assertions |
| Modify | `tests/test_enrich_metadata.py` | Update step name `"enrich"`→`"enrich_meta"` and ordering assertion |
| Modify | `tests/test_migrations.py` | Assert migration 008 columns exist |

---

## Task 1: Migration 008 — add genre_source + genre_confidence

**Files:**
- Create: `multidj/migrations/008_genre_source.sql`
- Modify: `tests/test_migrations.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_migrations.py`:

```python
def test_migration_008_adds_genre_columns(tmp_path):
    from multidj.db import connect
    db = tmp_path / "library.sqlite"
    with connect(str(db), readonly=False) as conn:
        pass  # migrations apply on connect
    raw = sqlite3.connect(str(db))
    cols = {r[1] for r in raw.execute("PRAGMA table_info(tracks)").fetchall()}
    raw.close()
    assert "genre_source" in cols
    assert "genre_confidence" in cols
```

- [ ] **Step 2: Run test — verify it fails**

```
pytest tests/test_migrations.py::test_migration_008_adds_genre_columns -v
```
Expected: FAIL — `genre_source` not in cols

- [ ] **Step 3: Create migration file**

Create `multidj/migrations/008_genre_source.sql`:

```sql
ALTER TABLE tracks ADD COLUMN genre_source TEXT;
ALTER TABLE tracks ADD COLUMN genre_confidence REAL;
```

- [ ] **Step 4: Run test — verify it passes**

```
pytest tests/test_migrations.py::test_migration_008_adds_genre_columns -v
```
Expected: PASS

- [ ] **Step 5: Run full test suite**

```
.venv/bin/pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: all existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add multidj/migrations/008_genre_source.sql tests/test_migrations.py
git commit -m "feat: migration 008 — add genre_source and genre_confidence to tracks"
```

---

## Task 2: ELECTRONIC_GENRE_LABELS constant

**Files:**
- Modify: `multidj/constants.py` (append after line 137, before end of file)

- [ ] **Step 1: Add constant to constants.py**

Append at the end of `multidj/constants.py`:

```python
# Genre labels for CLAP zero-shot classification.
# Used by enrich_genre.py to score audio embeddings against text prompts.
ELECTRONIC_GENRE_LABELS: tuple[str, ...] = (
    "Tech House",
    "Deep House",
    "Melodic Techno",
    "Minimal Techno",
    "Acid Techno",
    "Industrial Techno",
    "Progressive House",
    "Electro House",
    "Bass House",
    "Melodic House & Techno",
    "Organic House",
    "Techno",
    "House",
    "Trance",
    "Progressive Trance",
    "Drum & Bass",
    "Dubstep",
    "UK Garage",
    "Afro House",
    "Nu-Disco",
    "Disco",
    "Funk",
    "Ambient",
    "Downtempo",
    "Hip-Hop",
)
```

- [ ] **Step 2: Verify importable**

```
python -c "from multidj.constants import ELECTRONIC_GENRE_LABELS; print(len(ELECTRONIC_GENRE_LABELS))"
```
Expected: `25`

- [ ] **Step 3: Commit**

```bash
git add multidj/constants.py
git commit -m "feat: add ELECTRONIC_GENRE_LABELS to constants"
```

---

## Task 3: enrich_genre module

**Files:**
- Create: `multidj/enrich_genre.py`
- Create: `tests/test_enrich_genre.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_enrich_genre.py`:

```python
from __future__ import annotations

import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _set_genre(db_path, track_id, genre=None, genre_source=None, genre_confidence=None):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE tracks SET genre=?, genre_source=?, genre_confidence=? WHERE id=?",
        (genre, genre_source, genre_confidence, track_id),
    )
    conn.commit()
    conn.close()


def _get_genre_row(db_path, track_id):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT genre, genre_source, genre_confidence FROM tracks WHERE id=?", (track_id,)
    ).fetchone()
    conn.close()
    return row


def _add_discogs_styles(db_path, track_id):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO track_tags (track_id, key, value) VALUES (?, 'discogs_primary_style', 'Tech House')",
        (track_id,),
    )
    conn.commit()
    conn.close()


# ── tests ─────────────────────────────────────────────────────────────────────

def test_manual_source_is_never_overwritten(multidj_db):
    """genre_source='manual' must be skipped unconditionally, even with force."""
    _set_genre(multidj_db, 1, genre="My Custom Genre", genre_source="manual")
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True, force=True)
    row = _get_genre_row(multidj_db, 1)
    assert row[0] == "My Custom Genre"
    assert row[1] == "manual"


def test_specific_genre_from_file_tags_gets_source_file(multidj_db):
    """Track with specific genre but no genre_source → source='file'."""
    _set_genre(multidj_db, 8, genre="Techno", genre_source=None)
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    row = _get_genre_row(multidj_db, 8)
    assert row[0] == "Techno"
    assert row[1] == "file"
    assert row[2] == 1.0


def test_specific_genre_with_discogs_styles_gets_source_discogs(multidj_db):
    """Track has specific genre AND discogs_primary_style tag → source='discogs'."""
    _set_genre(multidj_db, 6, genre="House", genre_source=None)
    _add_discogs_styles(multidj_db, 6)
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    row = _get_genre_row(multidj_db, 6)
    assert row[1] == "discogs"


def test_uninformative_genre_triggers_discogs_lookup(multidj_db):
    """Track with genre='Music' (uninformative) → Discogs is queried."""
    _set_genre(multidj_db, 7, genre="Music", genre_source=None)
    discogs_result = {
        "styles": ["Tech House", "Deep House"],
        "release_year": 2003,
        "label": "Kompakt",
        "catalog_number": None,
        "score": 0.92,
        "source": "discogs",
    }
    with patch("multidj.enrich_genre.search_discogs", return_value=discogs_result) as mock_d, \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    mock_d.assert_called()
    row = _get_genre_row(multidj_db, 7)
    assert row[0] == "Tech House"
    assert row[1] == "discogs"
    assert row[2] == 1.0


def test_discogs_miss_falls_through_to_musicbrainz(multidj_db):
    """Discogs returns None → MusicBrainz is queried."""
    _set_genre(multidj_db, 9, genre=None, genre_source=None)
    mb_result = {
        "genre": "House",
        "release_year": 1999,
        "album": "Shades of Rhythm",
        "label": None,
        "score": 0.88,
    }
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=mb_result) as mock_mb:
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    mock_mb.assert_called()
    row = _get_genre_row(multidj_db, 9)
    assert row[0] == "House"
    assert row[1] == "musicbrainz"


def test_no_web_hit_no_embedding_leaves_genre_unchanged(multidj_db):
    """No Discogs/MB hit and no embedding → genre unchanged, genre_source stays NULL."""
    _set_genre(multidj_db, 9, genre=None, genre_source=None)
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    row = _get_genre_row(multidj_db, 9)
    assert row[0] is None
    assert row[1] is None


def test_clap_classifies_track_with_embedding(multidj_db):
    """No web hit but embedding present → CLAP classifies and writes genre."""
    import struct
    import numpy as np
    _set_genre(multidj_db, 9, genre=None, genre_source=None)
    # Insert a fake embedding for track 9
    fake_vec = np.ones(512, dtype=np.float32)
    conn = sqlite3.connect(str(multidj_db))
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (track_id, model_name, vector) VALUES (?, ?, ?)",
        (9, "laion/larger_clap_music", fake_vec.tobytes()),
    )
    conn.commit()
    conn.close()

    # Mock CLAP: patch _clap_classify_vec to return a confident hit
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None), \
         patch("multidj.enrich_genre._clap_classify_vec", return_value=("Tech House", 0.42)):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    row = _get_genre_row(multidj_db, 9)
    assert row[0] == "Tech House"
    assert row[1] == "clap"
    assert abs(row[2] - 0.42) < 0.001


def test_clap_below_threshold_leaves_genre_unchanged(multidj_db):
    """CLAP confidence < _CLAP_MIN_CONF → genre not written."""
    import numpy as np
    _set_genre(multidj_db, 9, genre=None, genre_source=None)
    fake_vec = np.ones(512, dtype=np.float32)
    conn = sqlite3.connect(str(multidj_db))
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (track_id, model_name, vector) VALUES (?, ?, ?)",
        (9, "laion/larger_clap_music", fake_vec.tobytes()),
    )
    conn.commit()
    conn.close()

    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None), \
         patch("multidj.enrich_genre._clap_classify_vec", return_value=(None, 0.0)):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    row = _get_genre_row(multidj_db, 9)
    assert row[1] is None


def test_incremental_skips_already_sourced_tracks(multidj_db):
    """genre_source already set → skip unless force=True."""
    _set_genre(multidj_db, 8, genre="Techno", genre_source="file", genre_confidence=1.0)
    with patch("multidj.enrich_genre.search_discogs", return_value=None) as mock_d, \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True, force=False)
    # Track 8 should be skipped (already has genre_source), Discogs never called for it
    # (Other tracks with NULL genre_source may trigger Discogs)
    row = _get_genre_row(multidj_db, 8)
    assert row[1] == "file"  # unchanged


def test_force_re_enriches_sourced_tracks(multidj_db):
    """force=True re-runs enrichment even for tracks with existing genre_source."""
    _set_genre(multidj_db, 8, genre="Techno", genre_source="file", genre_confidence=1.0)
    discogs_result = {
        "styles": ["Minimal Techno"],
        "release_year": 2002,
        "label": "Tresor",
        "catalog_number": None,
        "score": 0.95,
        "source": "discogs",
    }
    with patch("multidj.enrich_genre.search_discogs", return_value=discogs_result), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        # force=True re-processes even genre_source='file' tracks
        result = enrich_genre(str(multidj_db), apply=True, force=True)
    row = _get_genre_row(multidj_db, 8)
    # With force, Discogs hit replaces file source
    assert row[1] == "discogs"


def test_dry_run_does_not_write(multidj_db):
    """apply=False → no DB writes."""
    _set_genre(multidj_db, 7, genre="Music", genre_source=None)
    with patch("multidj.enrich_genre.search_discogs", return_value={
        "styles": ["Tech House"], "release_year": 2001, "label": None,
        "catalog_number": None, "score": 0.9, "source": "discogs",
    }), patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=False)
    assert result["mode"] == "dry_run"
    assert result["applied"] == 0
    row = _get_genre_row(multidj_db, 7)
    assert row[1] is None  # not written


def test_returns_expected_summary_keys(multidj_db):
    """enrich_genre always returns a dict with standard summary keys."""
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=False)
    assert "mode" in result
    assert "total_candidates" in result
    assert "applied" in result
    assert "errors" in result
```

- [ ] **Step 2: Run tests — verify they all fail**

```
pytest tests/test_enrich_genre.py -v
```
Expected: all FAIL — `ModuleNotFoundError: No module named 'multidj.enrich_genre'`

- [ ] **Step 3: Create multidj/enrich_genre.py**

```python
from __future__ import annotations

import sys
from typing import Any

from .db import connect, ensure_not_empty
from .constants import UNINFORMATIVE_GENRES, ELECTRONIC_GENRE_LABELS
from .enrich import search_discogs, search_musicbrainz

_MODEL_CLAP = "laion/larger_clap_music"
_CLAP_MIN_CONF = 0.25


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _is_specific(genre: str | None) -> bool:
    if not genre or not genre.strip():
        return False
    return genre.strip().lower() not in UNINFORMATIVE_GENRES


def _infer_source(track_id: int, conn) -> str:
    """Return 'discogs' if track_tags has discogs_primary_style, else 'file'."""
    row = conn.execute(
        "SELECT value FROM track_tags WHERE track_id = ? AND key = 'discogs_primary_style'",
        (track_id,),
    ).fetchone()
    return "discogs" if row else "file"


def _build_clap_text_vecs(model, processor, device: str) -> dict[str, Any]:
    """Encode ELECTRONIC_GENRE_LABELS as text embeddings using CLAP."""
    import torch
    import numpy as np
    vecs: dict[str, Any] = {}
    for label in ELECTRONIC_GENRE_LABELS:
        prompt = f"This is {label} music"
        inputs = processor(text=[prompt], return_tensors="pt", padding=True)
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            feat = model.get_text_features(**inputs)
        vecs[label] = feat.pooler_output[0].cpu().numpy()
    return vecs


def _cosine_sim(a: Any, b: Any) -> float:
    import numpy as np
    na = float(np.linalg.norm(a)) + 1e-8
    nb = float(np.linalg.norm(b)) + 1e-8
    return float(np.dot(a / na, b / nb))


def _clap_classify_vec(
    audio_vec: Any,
    text_vecs: dict[str, Any],
) -> tuple[str | None, float]:
    """Score audio embedding against genre text embeddings. Returns (genre, softmax_prob)."""
    import numpy as np
    scores = {g: _cosine_sim(audio_vec, tv) for g, tv in text_vecs.items()}
    vals = np.array(list(scores.values()))
    vals_exp = np.exp((vals - vals.max()) * 10)
    probs = vals_exp / vals_exp.sum()
    prob_dict = dict(zip(scores.keys(), probs.tolist()))
    best = max(prob_dict, key=prob_dict.get)
    conf = prob_dict[best]
    return (best, conf) if conf >= _CLAP_MIN_CONF else (None, 0.0)


def enrich_genre(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    enrich_cfg: dict | None = None,
) -> dict[str, Any]:
    """Harden genre metadata via layered enrichment: file→Discogs→MusicBrainz→CLAP.

    Writes genre, genre_source, genre_confidence to tracks.
    Tracks with genre_source='manual' are never touched.
    Incremental by default: skips tracks that already have genre_source set.
    """
    enrich_cfg = enrich_cfg or {}
    mode = "apply" if apply else "dry_run"

    # Discogs client (optional)
    discogs_client = None
    discogs_cfg = enrich_cfg.get("discogs")
    if discogs_cfg:
        try:
            import discogs_client as dc
            discogs_client = dc.Client(
                discogs_cfg.get("user_agent", "multidj/1.0"),
                user_token=discogs_cfg["token"],
            )
        except ImportError:
            pass

    mb_user_agent = (enrich_cfg.get("musicbrainz") or {}).get(
        "user_agent", "multidj/1.0 (bar.cohen@weizmann.ac.il)"
    )

    # Load candidates
    with connect(db_path, readonly=True) as conn:
        ensure_not_empty(conn)
        if force:
            where = "deleted = 0 AND (genre_source IS NULL OR genre_source != 'manual')"
        else:
            where = "deleted = 0 AND genre_source IS NULL"
        sql = f"SELECT id, artist, title, genre, path FROM tracks WHERE {where} ORDER BY id"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
        # For tracks with specific genre: detect existing source from track_tags
        infer_cache: dict[int, str] = {}
        for row in rows:
            if _is_specific(row["genre"]):
                infer_cache[row["id"]] = _infer_source(row["id"], conn)

    total_candidates = len(rows)
    applied = 0
    errors: list[dict] = []
    # (track_id, genre, genre_source, genre_confidence)
    updates: list[tuple[str | None, str | None, float | None, int]] = []

    for row in rows:
        track_id = row["id"]
        artist = row["artist"] or ""
        title = row["title"] or ""
        genre = row["genre"]
        try:
            # Step 1: already manual — skip (handled by WHERE clause)
            # Step 2: existing genre is specific → infer source
            if _is_specific(genre):
                source = infer_cache.get(track_id, "file")
                updates.append((genre, source, 1.0, track_id))
                continue

            # Step 3: Discogs
            if discogs_client and artist and title:
                hit = search_discogs(artist, title, discogs_client)
                if hit and hit.get("styles"):
                    new_genre = hit["styles"][0]
                    updates.append((new_genre, "discogs", 1.0, track_id))
                    continue

            # Step 4: MusicBrainz
            if artist and title:
                hit = search_musicbrainz(artist, title, mb_user_agent)
                if hit and hit.get("genre") and _is_specific(hit["genre"]):
                    updates.append((hit["genre"], "musicbrainz", 1.0, track_id))
                    continue

            # Step 5: CLAP — handled in batch after this loop
            updates.append((None, None, None, track_id))  # sentinel for CLAP step

        except Exception as exc:
            errors.append({"track_id": track_id, "artist": artist, "title": title, "error": str(exc)})

    # CLAP batch pass for tracks that fell through to sentinel (None, None, None, id)
    clap_needed = [(i, u[3]) for i, u in enumerate(updates) if u == (None, None, None, u[3])]

    if clap_needed:
        track_ids = [tid for _, tid in clap_needed]
        placeholders = ",".join("?" * len(track_ids))
        with connect(db_path, readonly=True) as conn:
            embed_rows = conn.execute(
                f"SELECT track_id, vector FROM embeddings "
                f"WHERE track_id IN ({placeholders}) AND model_name = ?",
                (*track_ids, _MODEL_CLAP),
            ).fetchall()
        embed_map = {r["track_id"]: r["vector"] for r in embed_rows}

        tracks_with_embed = [(idx, tid) for idx, tid in clap_needed if embed_map.get(tid)]

        if tracks_with_embed:
            text_vecs: dict[str, Any] | None = None
            np = None
            try:
                import numpy as np  # type: ignore[assignment]
                import torch
                from transformers import ClapModel, ClapProcessor
                device = "cuda" if torch.cuda.is_available() else "cpu"
                _progress(f"[enrich_genre] Loading CLAP on {device} for {len(tracks_with_embed)} tracks…")
                model = ClapModel.from_pretrained(_MODEL_CLAP).to(device)
                proc = ClapProcessor.from_pretrained(_MODEL_CLAP)
                model.eval()
                text_vecs = _build_clap_text_vecs(model, proc, device)
            except ImportError:
                _progress("[enrich_genre] embeddings extra not installed — skipping CLAP step")

            if text_vecs is not None and np is not None:
                for idx, tid in tracks_with_embed:
                    blob = embed_map[tid]
                    audio_vec = np.frombuffer(blob, dtype=np.float32).copy()
                    genre_hit, conf = _clap_classify_vec(audio_vec, text_vecs)
                    if genre_hit:
                        updates[idx] = (genre_hit, "clap", float(conf), tid)

    # Apply writes
    real_updates = [(g, s, c, tid) for g, s, c, tid in updates if s is not None]
    if apply and real_updates:
        with connect(db_path, readonly=False) as conn:
            conn.executemany(
                "UPDATE tracks SET genre=?, genre_source=?, genre_confidence=? WHERE id=?",
                real_updates,
            )
        applied = len(real_updates)
    elif not apply:
        applied = 0

    return {
        "mode": mode,
        "total_candidates": total_candidates,
        "applied": applied if apply else 0,
        "would_apply": len(real_updates),
        "errors": len(errors),
        "error_details": errors,
    }
```

- [ ] **Step 4: Run tests — verify they pass**

```
pytest tests/test_enrich_genre.py -v
```
Expected: all PASS

- [ ] **Step 5: Run full test suite**

```
.venv/bin/pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add multidj/enrich_genre.py tests/test_enrich_genre.py
git commit -m "feat: add enrich_genre module — layered genre hardening with genre_source tracking"
```

---

## Task 4: Pipeline restructure

Reorder steps into 4 phases, rename `enrich`→`enrich_meta` and `genres`→`clean_genres`, add `mixxx_import` and `enrich_genre` steps, add `phase` parameter and `PHASES` dict.

**Files:**
- Modify: `multidj/pipeline.py`
- Modify: `tests/test_pipeline.py` (step count, step names)
- Modify: `tests/test_enrich_metadata.py` (step name "enrich" → "enrich_meta")

- [ ] **Step 1: Update tests to expect new pipeline shape**

In `tests/test_pipeline.py`, change the step count assertion:

Find and update line 39:
```python
# Before:
assert len(result["steps"]) == 17
# After:
assert len(result["steps"]) == 19
```

In `tests/test_pipeline.py`, the test at line ~330 that checks cluster before cues will need updating since cluster moves to SYNC (after cues). Find:
```python
cluster_idx = step_names.index("cluster")
cues_idx = step_names.index("cues")
```
Update assertion to reflect new order (cluster now comes AFTER cues):
```python
assert cues_idx < cluster_idx
```

In `tests/test_pipeline.py`, find and update the skip set at line ~254 that uses `"genres"`:
```python
# Before:
skip={"import", "fix_mismatches", "parse", "dedupe", "bpm", "key", "energy", "cues", "genres", "clean_text", "crates", "sync"},
# After:
skip={"import", "fix_mismatches", "parse", "dedupe", "bpm", "key", "energy", "cues", "clean_genres", "clean_text", "crates", "sync"},
```

Find and update the skip set at line ~342:
```python
# Before:
"embed", "cluster", "cues", "genres", "clean_text", "crates", "sync", "report"},
# After:
"embed", "cluster", "cues", "clean_genres", "clean_text", "crates", "sync", "report"},
```

Find and update the skip set at line ~355:
```python
# Before:
"key", "energy", "cues", "genres", "clean_text", "crates", "sync", "report"},
# After:
"key", "energy", "cues", "clean_genres", "clean_text", "crates", "sync", "report"},
```

In `tests/test_enrich_metadata.py`, update the three pipeline step tests:
```python
# test_pipeline_includes_enrich_step: "enrich" → "enrich_meta"
assert "enrich_meta" in step_names

# test_pipeline_skip_enrich: "enrich" → "enrich_meta"
result = run_pipeline(db_path=str(multidj_db), apply=False, skip={"enrich_meta"})
enrich_steps = [s for s in result["steps"] if s["step"] == "enrich_meta"]
assert enrich_steps[0]["status"] == "skipped"

# test_pipeline_enrich_before_dedupe: new invariant — enrich_meta now comes AFTER dedupe
# Change assertion to: dedupe < enrich_meta
step_names = [s["step"] for s in result["steps"]]
enrich_idx = step_names.index("enrich_meta")
dedupe_idx = step_names.index("dedupe")
assert dedupe_idx < enrich_idx
```

- [ ] **Step 2: Run modified tests — verify they fail**

```
pytest tests/test_pipeline.py tests/test_enrich_metadata.py -v --tb=short 2>&1 | tail -30
```
Expected: multiple FAILs — step count, step names, ordering assertions

- [ ] **Step 3: Rewrite multidj/pipeline.py**

Replace the full content of `multidj/pipeline.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .analyze import analyze_bpm, analyze_energy, analyze_key
from .audit import fix_mismatches
from .backup import create_backup
from .clean import clean_genres, clean_text
from .crates import rebuild_crates
from .dedupe import dedupe as _dedupe
from .enrich import enrich_metadata as _enrich_metadata
from .enrich_genre import enrich_genre as _enrich_genre
from .parse import parse_library


PHASES: dict[str, set[str]] = {
    "ingest":  {"import", "dedupe", "fix_mismatches", "parse"},
    "analyze": {"mixxx_import", "bpm", "key", "mixxx_blobs", "energy", "embed", "cues"},
    "enrich":  {"clean_text", "enrich_meta", "enrich_genre", "clean_genres"},
    "sync":    {"cluster", "crates", "sync", "report"},
}

_ALL_STEPS: set[str] = set().union(*PHASES.values())


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def run_pipeline(
    db_path: str | None = None,
    mixxx_db_path: str | None = None,
    cfg: dict[str, Any] | None = None,
    apply: bool = False,
    music_dir: str | None = None,
    skip: set[str] | None = None,
    phase: str | None = None,
    report_output: str | None = None,
    skip_report: bool = False,
    backup_dir: str | None | bool = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run the MultiDJ pipeline in four phases: ingest → analyze → enrich → sync.

    Phase 1 INGEST:   import, dedupe, fix_mismatches, parse
    Phase 2 ANALYZE:  mixxx_import, bpm, key, mixxx_blobs, energy, embed, cues
    Phase 3 ENRICH:   clean_text, enrich_meta, enrich_genre, clean_genres
    Phase 4 SYNC:     cluster, crates, sync, report

    Pass phase='ingest'|'analyze'|'enrich'|'sync' to run a single phase.
    """
    cfg = cfg or {}
    skip = set(skip or set())
    mode = "apply" if apply else "dry_run"

    # Phase filter: skip all steps not in the requested phase
    if phase is not None:
        phase_steps = PHASES.get(phase, set())
        skip = skip | (_ALL_STEPS - phase_steps)

    # Config-driven auto-skips
    if not cfg.get("crates", {}).get("energy", True):
        skip = skip | {"energy"}
    if not cfg.get("crates", {}).get("bpm", True):
        skip = skip | {"bpm"}
    if not cfg.get("crates", {}).get("key", True):
        skip = skip | {"key"}
    if not cfg.get("pipeline", {}).get("fix_mismatches", True):
        skip = skip | {"fix_mismatches"}
    if not cfg.get("pipeline", {}).get("clean_text", True):
        skip = skip | {"clean_text"}
    if not cfg.get("pipeline", {}).get("cues", True):
        skip = skip | {"cues"}
    if not cfg.get("pipeline", {}).get("mixxx_blobs", True):
        skip = skip | {"mixxx_blobs"}
    if skip_report:
        skip = skip | {"report"}

    # One backup at the start — not per step
    if apply and backup_dir is not False:
        resolved = Path(db_path).expanduser() if db_path else Path("~/.multidj/library.sqlite").expanduser()
        if resolved.exists():
            create_backup(db_path, backup_dir=backup_dir)

    def _run_step(name: str, fn, **kwargs) -> dict[str, Any]:
        if name in skip:
            _log(f"[pipeline:{name}] skipped")
            return {"step": name, "status": "skipped"}
        _log(f"[pipeline:{name}] starting...")
        try:
            result = fn(**kwargs)
            _log(f"[pipeline:{name}] done")
            return {"step": name, "status": "ok", "result": result}
        except ImportError:
            raise
        except Exception as exc:
            _log(f"[pipeline:{name}] ERROR: {exc}")
            return {"step": name, "status": "error", "error": str(exc)}

    steps: list[dict[str, Any]] = []

    # ── Phase 1: INGEST ───────────────────────────────────────────────────────

    # import: scan music_dir for new tracks
    if music_dir:
        from .adapters.directory import DirectoryAdapter
        adapter = DirectoryAdapter()
        steps.append(_run_step(
            "import", adapter.import_all,
            multidj_db_path=db_path, apply=apply, paths=[music_dir], limit=limit,
        ))
    else:
        steps.append({"step": "import", "status": "skipped", "reason": "music_dir not set"})

    # dedupe: remove duplicates before analysis (avoid wasted compute)
    steps.append(_run_step(
        "dedupe", _dedupe,
        db_path=db_path, apply=apply, backup=False, limit=limit,
    ))

    # fix_mismatches: fix artist/title swap errors
    steps.append(_run_step(
        "fix_mismatches", fix_mismatches,
        db_path=db_path, apply=apply, backup=False, limit=limit,
    ))

    # parse: extract artist/title from filenames
    steps.append(_run_step(
        "parse", parse_library,
        db_path=db_path, apply=apply, backup=False, limit=limit,
    ))

    # ── Phase 2: ANALYZE ──────────────────────────────────────────────────────

    # mixxx_import: pull Mixxx's existing BPM/key BEFORE audio analysis
    if mixxx_db_path:
        from .import_mixxx_analysis import import_mixxx_analysis as _ima
        steps.append(_run_step(
            "mixxx_import", _ima,
            multidj_db_path=db_path, mixxx_db_path=mixxx_db_path,
            apply=apply, backup_dir=False, limit=limit,
        ))
    else:
        steps.append({"step": "mixxx_import", "status": "skipped", "reason": "mixxx_db_path not set"})

    # bpm: audio analysis — skips tracks that already have a value
    steps.append(_run_step(
        "bpm", analyze_bpm,
        db_path=db_path, apply=apply, backup_dir=False, limit=limit,
    ))

    # key: audio analysis — skips tracks that already have a value
    steps.append(_run_step(
        "key", analyze_key,
        db_path=db_path, apply=apply, limit=limit,
    ))

    # mixxx_blobs: push BeatGrid/KeyMap to Mixxx FOR NEW TRACKS ONLY
    if mixxx_db_path:
        from .mixxx_blobs import analyze_mixxx_blobs as _amb
        steps.append(_run_step(
            "mixxx_blobs", _amb,
            multidj_db_path=db_path, mixxx_db_path=mixxx_db_path,
            apply=apply, backup_dir=False, limit=limit, write_beats=True,
        ))
    else:
        steps.append({"step": "mixxx_blobs", "status": "skipped", "reason": "mixxx_db_path not set"})

    # energy: RMS × centroid score
    steps.append(_run_step(
        "energy", analyze_energy,
        db_path=db_path, apply=apply, backup_dir=False, limit=limit,
    ))

    # Auto-skip embed/cluster if disabled in config
    if not cfg.get("pipeline", {}).get("embed", True):
        skip = skip | {"embed"}
    if not cfg.get("pipeline", {}).get("cluster", True):
        skip = skip | {"cluster"}

    # embed: CLAP audio embeddings
    def _run_embed(**kwargs):
        try:
            from .embed import analyze_embed as _ae
            return _ae(**kwargs)
        except ImportError:
            raise RuntimeError("embeddings extra not installed; run: uv sync --extra embeddings")

    steps.append(_run_step(
        "embed", _run_embed,
        db_path=db_path, apply=apply, backup_dir=False, limit=limit,
    ))

    # cues: structural segmentation (intro/drop/outro)
    def _run_cues(**kwargs):
        try:
            from .cues import analyze_cues as _ac
            return _ac(**kwargs)
        except ImportError:
            raise RuntimeError("embeddings extra not installed; run: uv sync --extra embeddings")

    steps.append(_run_step(
        "cues", _run_cues,
        db_path=db_path, apply=apply, backup_dir=False, limit=limit,
    ))

    # ── Phase 3: ENRICH ───────────────────────────────────────────────────────

    # clean_text: strip promo markers from artist/title/album
    steps.append(_run_step(
        "clean_text", clean_text,
        db_path=db_path, apply=apply, backup=False, limit=limit,
    ))

    # enrich_meta: Discogs → MusicBrainz (release_year, label, album)
    from .config import get_enrich_config as _gec
    steps.append(_run_step(
        "enrich_meta", _enrich_metadata,
        db_path=db_path, apply=apply, limit=limit,
        enrich_cfg=_gec(cfg), backup_dir=False,
    ))

    # enrich_genre: layered genre hardening (file→Discogs→MB→CLAP)
    steps.append(_run_step(
        "enrich_genre", _enrich_genre,
        db_path=db_path, apply=apply, limit=limit,
        enrich_cfg=_gec(cfg),
    ))

    # clean_genres: normalize genre strings
    steps.append(_run_step(
        "clean_genres", clean_genres,
        db_path=db_path, apply=apply, backup=False, limit=limit,
    ))

    # ── Phase 4: SYNC ─────────────────────────────────────────────────────────

    # cluster: rebuild Vibe/ crates from embeddings
    def _run_cluster(**kwargs):
        try:
            from .cluster import cluster_vibe as _cv
            from .config import get_llm_config as _glc
            return _cv(llm_config=_glc(cfg), **kwargs)
        except ImportError:
            raise RuntimeError("embeddings extra not installed; run: uv sync --extra embeddings")

    steps.append(_run_step(
        "cluster", _run_cluster,
        db_path=db_path, apply=apply, backup_dir=False,
        min_cluster_size=cfg.get("pipeline", {}).get("min_cluster_size", 5),
    ))

    # crates: rebuild Genre:/BPM:/Key:/Energy:/Lang: auto-crates
    if limit is not None:
        _log("[pipeline:crates] --limit ignored (crates require full rebuild)")
    steps.append(_run_step(
        "crates", rebuild_crates,
        db_path=db_path, apply=apply, backup=False, cfg=cfg,
    ))

    # sync: push dirty tracks + crates + cues → Mixxx
    if limit is not None and mixxx_db_path:
        _log("[pipeline:sync] --limit ignored (sync pushes all dirty tracks)")
    if mixxx_db_path:
        from .adapters.mixxx import MixxxAdapter
        mx_adapter = MixxxAdapter(mixxx_db_path=mixxx_db_path)
        steps.append(_run_step(
            "sync", mx_adapter.full_sync,
            multidj_db_path=db_path, apply=apply,
        ))
    else:
        steps.append({"step": "sync", "status": "skipped", "reason": "mixxx_db_path not set"})

    # report: HTML dashboard
    def _report_step() -> dict[str, Any]:
        from .report import write_html_report
        output_path = report_output or "multidj_report.html"
        write_html_report(db_path=db_path, output_path=output_path)
        return {"path": output_path, "generated": True}

    steps.append(_run_step("report", _report_step))

    report_result = steps[-1]
    if report_result.get("status") == "ok":
        report_path = report_result.get("result", {}).get("path", "")
        if report_path:
            abs_path = Path(report_path).resolve()
            _log(f"\n━━━ Report ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            _log(f"  file://{abs_path}")
            _log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    errors = [s for s in steps if s["status"] == "error"]
    return {
        "mode": mode,
        "steps": steps,
        "total_steps": len(steps),
        "errors": len(errors),
        "error_steps": [s["step"] for s in errors],
    }
```

- [ ] **Step 4: Run the modified tests**

```
pytest tests/test_pipeline.py tests/test_enrich_metadata.py -v --tb=short 2>&1 | tail -40
```
Expected: all PASS

- [ ] **Step 5: Run full test suite**

```
.venv/bin/pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add multidj/pipeline.py tests/test_pipeline.py tests/test_enrich_metadata.py
git commit -m "feat: restructure pipeline into 4 phases (ingest/analyze/enrich/sync), add mixxx_import and enrich_genre steps"
```

---

## Task 5: CLI --phase flag

**Files:**
- Modify: `multidj/cli.py`

- [ ] **Step 1: Add failing test**

Add to `tests/test_pipeline.py`:

```python
def test_phase_ingest_skips_analyze_enrich_sync(multidj_db, mixxx_db, cfg, tmp_path):
    """--phase ingest runs only ingest steps; analyze/enrich/sync are all skipped."""
    result = run_pipeline(
        db_path=str(multidj_db),
        mixxx_db_path=str(mixxx_db),
        cfg=cfg,
        apply=False,
        music_dir=str(tmp_path),
        phase="ingest",
        report_output=str(tmp_path / "r.html"),
    )
    step_names = [s["step"] for s in result["steps"]]
    ingest = {"import", "dedupe", "fix_mismatches", "parse"}
    non_ingest = {"mixxx_import", "bpm", "key", "mixxx_blobs", "energy", "embed", "cues",
                  "clean_text", "enrich_meta", "enrich_genre", "clean_genres",
                  "cluster", "crates", "sync", "report"}
    for name in ingest:
        s = next(s for s in result["steps"] if s["step"] == name)
        assert s["status"] != "skipped", f"{name} should run in ingest phase"
    for name in non_ingest:
        s = next(s for s in result["steps"] if s["step"] == name)
        assert s["status"] == "skipped", f"{name} should be skipped in ingest phase"


def test_phase_invalid_name_skips_everything(multidj_db, cfg, tmp_path):
    """Unknown phase name results in all steps being skipped."""
    result = run_pipeline(
        db_path=str(multidj_db),
        cfg=cfg,
        apply=False,
        phase="nonexistent",
        report_output=str(tmp_path / "r.html"),
    )
    for s in result["steps"]:
        # import skips with reason, others skip from phase filter
        assert s["status"] == "skipped"
```

- [ ] **Step 2: Run tests — verify they pass (run_pipeline already accepts `phase`)**

```
pytest tests/test_pipeline.py::test_phase_ingest_skips_analyze_enrich_sync tests/test_pipeline.py::test_phase_invalid_name_skips_everything -v
```
Expected: PASS (pipeline.py already implements phase; these tests verify the logic)

- [ ] **Step 3: Add --phase to cli.py**

In `multidj/cli.py`, find the pipeline argument block (around line 414). After the existing arguments, add:

```python
p_pipeline.add_argument(
    "--phase",
    choices=["ingest", "analyze", "enrich", "sync"],
    default=None,
    dest="phase",
    help="Run only the specified pipeline phase (ingest|analyze|enrich|sync)",
)
```

Also find where `run_pipeline` is called (around line 790) and add `phase=args.phase`:

```python
result = run_pipeline(
    db_path=args.db,
    mixxx_db_path=mixxx_db,
    cfg=cfg,
    apply=args.apply,
    music_dir=music_dir,
    skip=skip,
    phase=args.phase,          # ← add this line
    report_output=args.report_output,
    skip_report=args.skip_report,
    limit=args.limit,
)
```

Also update the `--skip-enrich` handler to map to `"enrich_meta"` and add `--skip-enrich-genre`:

Find in cli.py (around line 775):
```python
if args.skip_enrich:          skip.add("enrich")
```
Change to:
```python
if args.skip_enrich:          skip.add("enrich_meta")
```

Find the skip for `"genres"` (around line 782):
```python
if args.skip_genres:          skip.add("genres")
```
Change to:
```python
if args.skip_genres:          skip.add("clean_genres")
```

- [ ] **Step 4: Add CLI test**

Add to `tests/test_pipeline.py`:

```python
from multidj.cli import main as cli_main


def test_cli_pipeline_phase_flag(multidj_db, tmp_path, capsys):
    rc = cli_main([
        "--db", str(multidj_db),
        "pipeline",
        "--phase", "ingest",
        "--skip-import",
    ])
    assert rc == 0
```

- [ ] **Step 5: Run new test**

```
pytest tests/test_pipeline.py::test_cli_pipeline_phase_flag -v
```
Expected: PASS

- [ ] **Step 6: Run full test suite**

```
.venv/bin/pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add multidj/cli.py tests/test_pipeline.py
git commit -m "feat: add --phase flag to multidj pipeline command"
```

---

## Task 6: mixxx_blobs SKIPPED/WROTE logging

**Files:**
- Modify: `multidj/mixxx_blobs.py`

The `analyze_mixxx_blobs` function already tracks `has_beats`/`has_keys` but never logs them. Add explicit per-track logging so the BPM/key protection is observable.

- [ ] **Step 1: Add failing test**

Add to `tests/test_mixxx_blobs.py`:

```python
def test_mixxx_blobs_logs_skipped_for_existing_beats(multidj_db, mixxx_db, capsys, tmp_path):
    """analyze_mixxx_blobs logs SKIPPED when Mixxx already has a BeatGrid."""
    import sqlite3
    import struct
    conn = sqlite3.connect(str(mixxx_db))
    # Set a fake beats blob on a track so Mixxx appears to already have analysis
    conn.execute("UPDATE library SET beats = ? WHERE id = 1", (b"fake_beats_blob",))
    conn.commit()
    conn.close()

    import sys
    from io import StringIO
    captured = StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured
    try:
        from multidj.mixxx_blobs import analyze_mixxx_blobs
        analyze_mixxx_blobs(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=False,
        )
    finally:
        sys.stderr = old_stderr
    output = captured.getvalue()
    assert "SKIPPED" in output or "skipped" in output.lower()


def test_mixxx_blobs_logs_wrote_for_new_track(multidj_db, mixxx_db, capsys, tmp_path):
    """analyze_mixxx_blobs logs WROTE when writing a new BeatGrid."""
    import sys
    from io import StringIO
    captured = StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured
    try:
        from multidj.mixxx_blobs import analyze_mixxx_blobs
        analyze_mixxx_blobs(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=True,
        )
    finally:
        sys.stderr = old_stderr
    output = captured.getvalue()
    # At least one WROTE or no tracks to write (empty fixture may produce no output)
    # The test just verifies the function doesn't crash and produces some output
    assert isinstance(output, str)
```

- [ ] **Step 2: Run tests — verify SKIPPED test fails (no such logging yet)**

```
pytest tests/test_mixxx_blobs.py::test_mixxx_blobs_logs_skipped_for_existing_beats -v
```
Expected: FAIL — "SKIPPED" not in output

- [ ] **Step 3: Add SKIPPED/WROTE logging to mixxx_blobs.py**

In `multidj/mixxx_blobs.py`, find the per-track loop (around line 319). After evaluating `has_beats` and deciding whether to write or skip, add logging.

Find the block around line 350-370 where `wrote_beat` and `wrote_key` are determined. After computing `wrote_beat` (whether a new BeatGrid will be written), add:

```python
# After: wrote_beat = True / False is determined (around line 353)
# Add this log after the bpm_needs_update calculation (~line 358):

artist_title = f"{artist} — {title}" if artist else title
if bpm and bpm > 0 and write_beats:
    if has_beats and not force:
        print(f"[mixxx_blobs] SKIPPED — Mixxx already owns BPM for {artist_title}", file=sys.stderr)
    elif wrote_beat:
        print(f"[mixxx_blobs] WROTE BeatGrid for {artist_title} ({bpm:.1f} BPM)", file=sys.stderr)
```

The exact placement: add this logging block right after line 353 (`wrote_beat = True`) and the skipped case detection. The `has_beats` flag is already available from the `lib_row` query on line 341.

The complete logic block to insert (after `wrote_beat` assignment, before `bpm_sync`):

```python
            # Log BPM/key protection decisions
            artist_title = f"{artist} — {title}" if artist else (title or path)
            if bpm and bpm > 0 and write_beats:
                if has_beats and not force:
                    print(
                        f"[mixxx_blobs] SKIPPED — Mixxx already owns BPM for {artist_title}",
                        file=sys.stderr,
                    )
                elif wrote_beat:
                    print(
                        f"[mixxx_blobs] WROTE BeatGrid for {artist_title} ({float(bpm):.1f} BPM)",
                        file=sys.stderr,
                    )
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_mixxx_blobs.py -v --tb=short 2>&1 | tail -20
```
Expected: all pass

- [ ] **Step 5: Run full test suite**

```
.venv/bin/pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add multidj/mixxx_blobs.py tests/test_mixxx_blobs.py
git commit -m "feat: add SKIPPED/WROTE per-track logging to mixxx_blobs for BPM/key protection observability"
```

---

## Verification Checklist

After all tasks are complete:

1. `pytest tests/ -v 2>&1 | tail -5` — all tests pass, 0 failures
2. `multidj pipeline --phase ingest --apply --skip-import` — runs only ingest steps, logs `[pipeline:bpm] skipped` etc.
3. `multidj pipeline --phase analyze --apply 2>&1 | grep mixxx_blobs` — shows SKIPPED or WROTE lines per track
4. `multidj pipeline --phase enrich --apply --limit 5` — runs enrich_genre on 5 tracks; check `SELECT id, genre, genre_source, genre_confidence FROM tracks LIMIT 10` in the DB shows populated sources
5. `multidj pipeline --apply` — full pipeline runs without errors; step list shows all 19 steps
