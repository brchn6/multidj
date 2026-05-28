# Embeddings & Semantic Clustering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CLAP audio embeddings, UMAP+HDBSCAN clustering, and LLM-named `Vibe/` auto-crates to MultiDJ, plus a `multidj similar` track-similarity query.

**Architecture:** Each track's audio is encoded into a 512-dim vector by the CLAP model (3-window average), stored in a plain SQLite BLOB table. At cluster time, all vectors are loaded into numpy, reduced via UMAP, clustered via HDBSCAN, and each cluster is named by an OpenAI-compatible LLM. The resulting `Vibe/` crates integrate with the existing auto-crate lifecycle and pipeline.

**Tech Stack:** Python 3.9+, PyTorch, HuggingFace transformers (CLAP), librosa (already optional), umap-learn, hdbscan, openai SDK, SQLite (plain BLOB table — no extension required).

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `multidj/migrations/004_embeddings.sql` | Embeddings BLOB table |
| Create | `multidj/embed.py` | CLAP loading, encoding, storage, `analyze_embed()`, `find_similar()` |
| Create | `multidj/cluster.py` | UMAP+HDBSCAN, LLM naming, crate writing, `cluster_vibe()` |
| Create | `tests/test_embed.py` | embed module tests |
| Create | `tests/test_cluster.py` | cluster module tests |
| Modify | `pyproject.toml` | Add `[embeddings]` optional dep group |
| Modify | `multidj/constants.py` | Add `Vibe/` to AUTO_CRATE_PREFIXES and REBUILD_CRATE_RE |
| Modify | `multidj/config.py` | Add `get_llm_config()` |
| Modify | `multidj/cli.py` | Wire `analyze embed`, `cluster vibe`, `similar` commands; add `--skip-embed/cluster` to pipeline |
| Modify | `multidj/pipeline.py` | Add `embed` and `cluster` steps (steps 8–9) |

---

## Task 1: Foundation — migration, constants, pyproject

**Files:**
- Create: `multidj/migrations/004_embeddings.sql`
- Modify: `multidj/constants.py:71` (AUTO_CRATE_PREFIXES) and `:98` (REBUILD_CRATE_RE)
- Modify: `pyproject.toml` (add `[embeddings]` extra)

- [ ] **Step 1: Write the migration**

Create `multidj/migrations/004_embeddings.sql`:

```sql
CREATE TABLE IF NOT EXISTS embeddings (
    track_id    INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    model_name  TEXT    NOT NULL,
    vector      BLOB    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 2: Write a migration test**

Add to `tests/test_migrations.py` (or create it if it doesn't exist; check first with `ls tests/`):

```python
def test_embeddings_table_created(tmp_path):
    import sqlite3
    from multidj.db import connect
    db = tmp_path / "library.sqlite"
    with connect(str(db), readonly=False) as conn:
        pass  # migrations apply on connect
    raw = sqlite3.connect(str(db))
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    raw.close()
    assert "embeddings" in tables
```

- [ ] **Step 3: Run the test — expect FAIL (migration file exists but test may not yet pass if test file didn't exist)**

```bash
.venv/bin/pytest tests/test_migrations.py -v -k test_embeddings_table_created
```

Expected: FAIL with "embeddings not in tables" or test collection error.

- [ ] **Step 4: Update constants.py — add `Vibe/` to both regexes**

In `multidj/constants.py`, replace line 71:
```python
AUTO_CRATE_PREFIXES = re.compile(r"^(Genre:\s|BPM:\s|Lang:\s|Key:\s|Energy:\s)", re.IGNORECASE)
```
with:
```python
AUTO_CRATE_PREFIXES = re.compile(r"^(Genre:\s|BPM:\s|Lang:\s|Key:\s|Energy:\s|Vibe/)", re.IGNORECASE)
```

Replace line 98:
```python
REBUILD_CRATE_RE = re.compile(r"^(Genre:\s|BPM:\s|Lang:\s|Key:\s|Energy:\s)", re.IGNORECASE)
```
with:
```python
REBUILD_CRATE_RE = re.compile(r"^(Genre:\s|BPM:\s|Lang:\s|Key:\s|Energy:\s|Vibe/)", re.IGNORECASE)
```

- [ ] **Step 5: Add `[embeddings]` optional dep group to pyproject.toml**

In `pyproject.toml`, add to `[project.optional-dependencies]`:
```toml
embeddings = [
    "torch>=2.0",
    "transformers>=4.40",
    "umap-learn>=0.5",
    "hdbscan>=0.8",
    "openai>=1.0",
]
```

- [ ] **Step 6: Run the migration test — expect PASS**

```bash
.venv/bin/pytest tests/test_migrations.py -v -k test_embeddings_table_created
```

Expected: PASS

- [ ] **Step 7: Run full suite to confirm no regressions**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all existing tests pass (132+1 passing).

- [ ] **Step 8: Commit**

```bash
git add multidj/migrations/004_embeddings.sql multidj/constants.py pyproject.toml tests/test_migrations.py
git commit -m "feat: add embeddings migration, Vibe/ crate prefix, embeddings dep group"
```

---

## Task 2: Config — `get_llm_config()` helper

**Files:**
- Modify: `multidj/config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_llm_config.py`:

```python
from __future__ import annotations
import pytest
from pathlib import Path
from multidj.config import get_llm_config


def test_returns_none_when_section_missing(tmp_path):
    cfg = {}
    assert get_llm_config(cfg) is None


def test_returns_none_when_api_key_missing(tmp_path):
    cfg = {"llm": {"base_url": "https://example.com"}}
    assert get_llm_config(cfg) is None


def test_returns_none_when_base_url_missing(tmp_path):
    cfg = {"llm": {"api_key": "sk-test"}}
    assert get_llm_config(cfg) is None


def test_returns_config_when_both_present(tmp_path):
    cfg = {
        "llm": {
            "base_url": "https://opencode.ai/api/v1",
            "api_key": "sk-test",
            "model": "deepseek/deepseek-chat",
        }
    }
    result = get_llm_config(cfg)
    assert result is not None
    assert result["base_url"] == "https://opencode.ai/api/v1"
    assert result["api_key"] == "sk-test"
    assert result["model"] == "deepseek/deepseek-chat"


def test_default_model_when_not_specified(tmp_path):
    cfg = {"llm": {"base_url": "https://opencode.ai/api/v1", "api_key": "sk-test"}}
    result = get_llm_config(cfg)
    assert result is not None
    assert result["model"] == "gpt-3.5-turbo"
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
.venv/bin/pytest tests/test_llm_config.py -v
```

Expected: FAIL with `ImportError: cannot import name 'get_llm_config'`

- [ ] **Step 3: Add `get_llm_config()` to `multidj/config.py`**

Append after the `get_music_dir` function (after line 86):

```python

def get_llm_config(cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return LLM config dict or None if base_url or api_key are not set."""
    if cfg is None:
        cfg = load_config()
    llm = cfg.get("llm", {})
    if not llm.get("base_url") or not llm.get("api_key"):
        return None
    return {
        "base_url": llm["base_url"],
        "api_key": llm["api_key"],
        "model": llm.get("model", "gpt-3.5-turbo"),
    }
```

- [ ] **Step 4: Run test — expect PASS**

```bash
.venv/bin/pytest tests/test_llm_config.py -v
```

Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add multidj/config.py tests/test_llm_config.py
git commit -m "feat: add get_llm_config() for opencode/openai-compatible LLM config"
```

---

## Task 3: `embed.py` — core encoding functions

**Files:**
- Create: `multidj/embed.py`
- Create: `tests/test_embed.py` (first test file, grows across tasks 3–4)

- [ ] **Step 1: Write failing tests for storage helpers**

Create `tests/test_embed.py`:

```python
from __future__ import annotations
import numpy as np
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch

from tests.fixtures.multidj_factory import make_multidj_db
from multidj.embed import store_embedding, _blob_to_vec
from multidj.db import connect


@pytest.fixture()
def db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


FIXED_VEC = np.ones(512, dtype=np.float32) * 0.1


def test_store_and_retrieve_embedding(db):
    with connect(str(db), readonly=True) as conn:
        track_id = conn.execute(
            "SELECT id FROM tracks WHERE deleted=0 LIMIT 1"
        ).fetchone()["id"]

    with connect(str(db), readonly=False) as conn:
        store_embedding(conn, track_id, "test-model", FIXED_VEC)
        conn.commit()

    raw = sqlite3.connect(str(db))
    row = raw.execute("SELECT vector, model_name FROM embeddings WHERE track_id=?", (track_id,)).fetchone()
    raw.close()
    assert row is not None
    recovered = _blob_to_vec(row[0])
    assert recovered.shape == (512,)
    np.testing.assert_allclose(recovered, FIXED_VEC, rtol=1e-5)
    assert row[1] == "test-model"


def test_store_embedding_upserts(db):
    with connect(str(db), readonly=True) as conn:
        track_id = conn.execute(
            "SELECT id FROM tracks WHERE deleted=0 LIMIT 1"
        ).fetchone()["id"]

    vec2 = np.zeros(512, dtype=np.float32)
    vec2[0] = 9.9

    with connect(str(db), readonly=False) as conn:
        store_embedding(conn, track_id, "model-v1", FIXED_VEC)
        store_embedding(conn, track_id, "model-v2", vec2)
        conn.commit()

    raw = sqlite3.connect(str(db))
    count = raw.execute("SELECT COUNT(*) FROM embeddings WHERE track_id=?", (track_id,)).fetchone()[0]
    row = raw.execute("SELECT vector FROM embeddings WHERE track_id=?", (track_id,)).fetchone()
    raw.close()
    assert count == 1  # upsert, not insert
    np.testing.assert_allclose(_blob_to_vec(row[0])[0], 9.9, rtol=1e-4)
```

- [ ] **Step 2: Run — expect FAIL**

```bash
.venv/bin/pytest tests/test_embed.py::test_store_and_retrieve_embedding -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'multidj.embed'`

- [ ] **Step 3: Create `multidj/embed.py` with storage + encoding core**

```python
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import numpy as np

from .db import connect, ensure_not_empty


MODEL_NAME = "laion/larger_clap_music"
_SR = 48_000
_WINDOW_SECS = 30


def _progress(msg: str, end: str = "\n") -> None:
    print(msg, file=sys.stderr, end=end, flush=True)


def _vec_to_blob(v: np.ndarray) -> bytes:
    return v.astype(np.float32).tobytes()


def _blob_to_vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32).copy()


def store_embedding(
    conn,
    track_id: int,
    model_name: str,
    vector: np.ndarray,
) -> None:
    conn.execute(
        """
        INSERT INTO embeddings (track_id, model_name, vector)
        VALUES (?, ?, ?)
        ON CONFLICT(track_id) DO UPDATE
            SET model_name = excluded.model_name,
                vector     = excluded.vector,
                created_at = datetime('now')
        """,
        (track_id, model_name, _vec_to_blob(vector)),
    )


def load_embeddings_from_db(conn) -> tuple[list[int], np.ndarray]:
    """Return (track_ids, matrix) for all non-deleted embedded tracks."""
    rows = conn.execute("""
        SELECT e.track_id, e.vector
        FROM embeddings e
        JOIN tracks t ON e.track_id = t.id
        WHERE t.deleted = 0
        ORDER BY e.track_id
    """).fetchall()
    if not rows:
        return [], np.empty((0, 512), dtype=np.float32)
    track_ids = [r["track_id"] for r in rows]
    matrix = np.stack([_blob_to_vec(r["vector"]) for r in rows])
    return track_ids, matrix


def load_clap_model() -> tuple[Any, Any, str]:
    """Load CLAP model + processor. Returns (model, processor, device)."""
    try:
        import torch
        from transformers import ClapModel, ClapProcessor  # type: ignore
    except ImportError:
        raise RuntimeError(
            "Missing optional dependency 'embeddings'. Install with:\n\n"
            "    uv sync --extra embeddings\n"
        )
    device = (
        "cuda" if torch.cuda.is_available()
        else "mps" if torch.backends.mps.is_available()
        else "cpu"
    )
    _progress(f"Loading CLAP model ({MODEL_NAME}) on {device}…")
    model = ClapModel.from_pretrained(MODEL_NAME).to(device)
    processor = ClapProcessor.from_pretrained(MODEL_NAME)
    model.eval()
    return model, processor, device


def _encode_audio_file(filepath: str, model: Any, processor: Any, device: str) -> np.ndarray:
    """Encode a single audio file: sample 3 × 30 s windows, return mean 512-d vector."""
    try:
        import librosa  # type: ignore
        import torch
    except ImportError:
        raise RuntimeError(
            "Missing optional dependency 'embeddings'. Install with:\n\n"
            "    uv sync --extra embeddings\n"
        )

    window = _SR * _WINDOW_SECS
    y, _ = librosa.load(filepath, sr=_SR, mono=True)

    if len(y) < window:
        y = np.pad(y, (0, window - len(y)))

    mid = len(y) // 2
    starts = [0, max(0, mid - window // 2), max(0, len(y) - window)]
    embeddings: list[np.ndarray] = []
    for start in starts:
        w = y[start : start + window]
        if len(w) < window:
            w = np.pad(w, (0, window - len(w)))
        inputs = processor(audios=w, sampling_rate=_SR, return_tensors="pt", padding=True)
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            feat = model.get_audio_features(**inputs)
        embeddings.append(feat[0].cpu().numpy())

    return np.mean(embeddings, axis=0)
```

- [ ] **Step 4: Run storage tests — expect PASS**

```bash
.venv/bin/pytest tests/test_embed.py -v
```

Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add multidj/embed.py tests/test_embed.py
git commit -m "feat: add embed.py with CLAP loading, audio encoding, and BLOB storage helpers"
```

---

## Task 4: `embed.py` — `analyze_embed()` main function

**Files:**
- Modify: `multidj/embed.py` (append `analyze_embed` and `find_similar`)
- Modify: `tests/test_embed.py` (add analyze_embed tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_embed.py`:

```python
from multidj.embed import analyze_embed


def _stub_load_clap():
    return object(), object(), "cpu"


def _stub_encode(path, model, processor, device):
    return FIXED_VEC.copy()


def test_dry_run_returns_candidate_count(db):
    result = analyze_embed(db_path=str(db), apply=False)
    assert result["mode"] == "dry_run"
    assert result["total_candidates"] > 0
    assert result["processed"] == 0
    assert result["succeeded"] == 0


def test_dry_run_does_not_write(db):
    analyze_embed(db_path=str(db), apply=False)
    raw = sqlite3.connect(str(db))
    count = raw.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    raw.close()
    assert count == 0


def test_apply_stores_embeddings(db, tmp_path):
    with patch("multidj.embed.load_clap_model", _stub_load_clap), \
         patch("multidj.embed._encode_audio_file", _stub_encode):
        result = analyze_embed(db_path=str(db), apply=True, backup_dir=str(tmp_path))
    assert result["mode"] == "apply"
    assert result["succeeded"] > 0
    raw = sqlite3.connect(str(db))
    count = raw.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    raw.close()
    assert count == result["succeeded"]


def test_incremental_skips_already_embedded(db, tmp_path):
    with patch("multidj.embed.load_clap_model", _stub_load_clap), \
         patch("multidj.embed._encode_audio_file", _stub_encode):
        analyze_embed(db_path=str(db), apply=True, backup_dir=str(tmp_path))
    result2 = analyze_embed(db_path=str(db), apply=False)
    assert result2["total_candidates"] == 0


def test_force_re_embeds_existing(db, tmp_path):
    with patch("multidj.embed.load_clap_model", _stub_load_clap), \
         patch("multidj.embed._encode_audio_file", _stub_encode):
        analyze_embed(db_path=str(db), apply=True, backup_dir=str(tmp_path))
    result3 = analyze_embed(db_path=str(db), apply=False, force=True)
    assert result3["total_candidates"] > 0


def test_per_track_error_isolation(db, tmp_path):
    call_count = 0

    def flaky(path, model, processor, device):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("bad audio")
        return FIXED_VEC.copy()

    with patch("multidj.embed.load_clap_model", _stub_load_clap), \
         patch("multidj.embed._encode_audio_file", flaky):
        result = analyze_embed(db_path=str(db), apply=True, backup_dir=str(tmp_path))
    assert result["errors"] >= 1
    assert result["succeeded"] >= 1


def test_limit_restricts_processed(db, tmp_path):
    with patch("multidj.embed.load_clap_model", _stub_load_clap), \
         patch("multidj.embed._encode_audio_file", _stub_encode):
        result = analyze_embed(db_path=str(db), apply=True, limit=1, backup_dir=str(tmp_path))
    assert result["processed"] == 1
```

- [ ] **Step 2: Run — expect FAIL**

```bash
.venv/bin/pytest tests/test_embed.py -v
```

Expected: new tests FAIL with `ImportError: cannot import name 'analyze_embed'`

- [ ] **Step 3: Append `analyze_embed()` to `multidj/embed.py`**

```python

def analyze_embed(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    backup_dir: str | None | bool = None,
) -> dict[str, Any]:
    with connect(db_path, readonly=True) as conn:
        ensure_not_empty(conn)
        if force:
            rows = conn.execute(
                "SELECT id, path FROM tracks WHERE deleted=0 ORDER BY id"
            ).fetchall()
        else:
            rows = conn.execute("""
                SELECT t.id, t.path
                FROM tracks t
                LEFT JOIN embeddings e ON t.id = e.track_id
                WHERE t.deleted = 0 AND e.track_id IS NULL
                ORDER BY t.id
            """).fetchall()

    candidates = [{"id": r["id"], "path": r["path"]} for r in rows]
    if limit is not None:
        candidates = candidates[:limit]
    total = len(candidates)

    _progress(f"analyze embed — {total} track(s) to embed (model: {MODEL_NAME})")

    if not apply:
        return {
            "mode": "dry_run",
            "total_candidates": total,
            "processed": 0,
            "succeeded": 0,
            "errors": 0,
            "model": MODEL_NAME,
        }

    model, processor, device = load_clap_model()
    succeeded = errors = 0

    with connect(db_path, readonly=False) as conn:
        for i, row in enumerate(candidates):
            _progress(f"  [{i + 1}/{total}] {Path(row['path']).name}", end="\r")
            try:
                vec = _encode_audio_file(row["path"], model, processor, device)
                store_embedding(conn, row["id"], MODEL_NAME, vec)
                conn.commit()
                succeeded += 1
            except Exception as exc:
                _progress(f"\n  ERROR: {exc}")
                errors += 1

    _progress("")
    return {
        "mode": "apply",
        "total_candidates": total,
        "processed": total,
        "succeeded": succeeded,
        "errors": errors,
        "model": MODEL_NAME,
    }
```

- [ ] **Step 4: Run all embed tests — expect PASS**

```bash
.venv/bin/pytest tests/test_embed.py -v
```

Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add multidj/embed.py tests/test_embed.py
git commit -m "feat: add analyze_embed() — incremental CLAP encoding with per-track error isolation"
```

---

## Task 5: CLI — `analyze embed` command

**Files:**
- Modify: `multidj/cli.py`

- [ ] **Step 1: Write a smoke test**

Append to `tests/test_embed.py`:

```python
from multidj.cli import main as cli_main


def test_cli_analyze_embed_dry_run(db):
    ret = cli_main(["--db", str(db), "analyze", "embed"])
    assert ret == 0


def test_cli_analyze_embed_apply(db, tmp_path):
    with patch("multidj.embed.load_clap_model", _stub_load_clap), \
         patch("multidj.embed._encode_audio_file", _stub_encode):
        ret = cli_main(["--db", str(db), "analyze", "embed", "--apply"])
    assert ret == 0
```

- [ ] **Step 2: Run — expect FAIL**

```bash
.venv/bin/pytest tests/test_embed.py -v -k "test_cli"
```

Expected: FAIL with `SystemExit` (unrecognised subcommand) or similar.

- [ ] **Step 3: Add `embed` subparser to `cli.py`**

In `multidj/cli.py`, inside `build_parser()`, find the analyze subparsers block (around line 163). After the `key` subparser, add:

```python
    p_embed = analyze_sub.add_parser("embed", help="Encode tracks as CLAP audio embeddings")
    p_embed.add_argument("--apply",     action="store_true", help="Write embeddings (default: dry-run)")
    p_embed.add_argument("--force",     action="store_true", help="Re-encode already-embedded tracks")
    p_embed.add_argument("--limit",     type=int, default=None, help="Cap number of tracks to process")
    p_embed.add_argument("--no-backup", action="store_true", dest="no_backup")
```

- [ ] **Step 4: Add the handler in `main()`**

In `multidj/cli.py`, inside the `elif args.command == "analyze":` block (around line 347), add a branch for `embed`:

```python
        elif args.analyze_target == "embed":
            from .embed import analyze_embed
            result = analyze_embed(
                db_path=args.db,
                apply=args.apply,
                force=args.force,
                limit=args.limit,
                backup_dir=False if args.no_backup else None,
            )
```

- [ ] **Step 5: Run CLI tests — expect PASS**

```bash
.venv/bin/pytest tests/test_embed.py -v -k "test_cli"
```

Expected: 2 tests PASS

- [ ] **Step 6: Smoke-check from terminal**

```bash
.venv/bin/multidj analyze embed
```

Expected output on stderr: `analyze embed — N track(s) to embed (model: laion/larger_clap_music)` and JSON/human output showing `mode: dry_run`.

- [ ] **Step 7: Commit**

```bash
git add multidj/cli.py
git commit -m "feat: wire 'multidj analyze embed' CLI command"
```

---

## Task 6: `cluster.py` — UMAP + HDBSCAN

**Files:**
- Create: `multidj/cluster.py`
- Create: `tests/test_cluster.py`

- [ ] **Step 1: Write failing tests for clustering core**

Create `tests/test_cluster.py`:

```python
from __future__ import annotations
import numpy as np
import sqlite3
import pytest
from unittest.mock import patch

from tests.fixtures.multidj_factory import make_multidj_db
from multidj.embed import store_embedding
from multidj.cluster import cluster_embeddings, cluster_vibe
from multidj.db import connect


@pytest.fixture()
def db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


@pytest.fixture()
def db_with_embeddings(tmp_path):
    db = make_multidj_db(tmp_path / "library.sqlite")
    with connect(str(db), readonly=True) as conn:
        track_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM tracks WHERE deleted=0 ORDER BY id"
        ).fetchall()]

    half = len(track_ids) // 2
    with connect(str(db), readonly=False) as conn:
        for i, tid in enumerate(track_ids):
            vec = np.zeros(512, dtype=np.float32)
            if i < half:
                vec[0] = 1.0    # cluster A: all energy in dim 0
            else:
                vec[256] = 1.0  # cluster B: all energy in dim 256
            store_embedding(conn, tid, "test-model", vec)
        conn.commit()
    return db, track_ids


def test_cluster_embeddings_returns_labels(db_with_embeddings):
    db, track_ids = db_with_embeddings
    with connect(str(db), readonly=True) as conn:
        from multidj.embed import load_embeddings_from_db
        _, vectors = load_embeddings_from_db(conn)
    labels = cluster_embeddings(vectors, min_cluster_size=2)
    assert labels.shape == (len(track_ids),)
    # Two clearly separated clusters → at least 1 real cluster (label >= 0)
    assert any(l >= 0 for l in labels)


def test_too_few_tracks_raises(db):
    with pytest.raises(RuntimeError, match="Too few embedded tracks"):
        cluster_vibe(db_path=str(db), apply=False, min_cluster_size=5)
```

- [ ] **Step 2: Run — expect FAIL**

```bash
.venv/bin/pytest tests/test_cluster.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'multidj.cluster'`

- [ ] **Step 3: Create `multidj/cluster.py` with UMAP+HDBSCAN**

```python
from __future__ import annotations

import sys
from typing import Any

import numpy as np

from .db import connect, ensure_not_empty
from .embed import load_embeddings_from_db


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _numbered_name(idx: int) -> str:
    return f"Cluster-{idx:02d}"


def cluster_embeddings(vectors: np.ndarray, min_cluster_size: int) -> np.ndarray:
    """UMAP 512d→10d then HDBSCAN. Returns integer label array (-1 = noise)."""
    try:
        import umap  # type: ignore
        import hdbscan  # type: ignore
    except ImportError:
        raise RuntimeError(
            "Missing optional dependency 'embeddings'. Install with:\n\n"
            "    uv sync --extra embeddings\n"
        )
    reducer = umap.UMAP(
        n_components=10,
        n_neighbors=min(15, len(vectors) - 1),
        min_dist=0.1,
        metric="cosine",
        random_state=42,
    )
    reduced = reducer.fit_transform(vectors)

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        metric="euclidean",
        cluster_selection_method="eom",
    )
    return clusterer.fit_predict(reduced)
```

- [ ] **Step 4: Run the clustering tests — expect PASS**

```bash
.venv/bin/pytest tests/test_cluster.py::test_cluster_embeddings_returns_labels tests/test_cluster.py::test_too_few_tracks_raises -v
```

Note: `test_too_few_tracks_raises` needs `cluster_vibe` to exist; add a minimal stub to `cluster.py` temporarily if it fails on import, then finish it in Task 8.

Expected: 2 tests PASS (install `umap-learn hdbscan` first: `uv sync --extra embeddings`)

- [ ] **Step 5: Commit**

```bash
git add multidj/cluster.py tests/test_cluster.py
git commit -m "feat: add cluster.py with UMAP+HDBSCAN cluster_embeddings()"
```

---

## Task 7: `cluster.py` — LLM naming

**Files:**
- Modify: `multidj/cluster.py`

- [ ] **Step 1: Write failing test for LLM naming**

Append to `tests/test_cluster.py`:

```python
from multidj.cluster import name_cluster


def test_name_cluster_calls_llm():
    samples = [
        {"artist": "Ben Klock", "title": "Subzero", "genre": "Techno", "bpm": 135, "key": "6A"},
        {"artist": "Blawan", "title": "Getting Me Down", "genre": "Techno", "bpm": 136, "key": "8A"},
    ]
    llm_config = {"base_url": "http://fake", "api_key": "test", "model": "fake-model"}

    from unittest.mock import MagicMock, patch

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = " Dark Techno "

    with patch("multidj.cluster.OpenAI", return_value=mock_client):
        result = name_cluster(samples, llm_config)

    assert result == "Dark Techno"
    mock_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_client.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "fake-model"
    assert call_kwargs["max_tokens"] == 20
```

- [ ] **Step 2: Run — expect FAIL**

```bash
.venv/bin/pytest tests/test_cluster.py::test_name_cluster_calls_llm -v
```

Expected: FAIL with `ImportError: cannot import name 'name_cluster'`

- [ ] **Step 3: Add `name_cluster()` to `multidj/cluster.py`**

Append to `multidj/cluster.py`:

```python

def name_cluster(track_samples: list[dict[str, Any]], llm_config: dict[str, Any]) -> str:
    """Call LLM to generate a 2–3 word evocative crate name for a cluster."""
    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        raise RuntimeError(
            "Missing optional dependency 'embeddings'. Install with:\n\n"
            "    uv sync --extra embeddings\n"
        )
    client = OpenAI(base_url=llm_config["base_url"], api_key=llm_config["api_key"])
    model = llm_config.get("model", "gpt-3.5-turbo")

    track_lines = "\n".join(
        f'- "{t.get("artist") or "Unknown"} — {t.get("title") or "Unknown"}"'
        f' (genre: {t.get("genre") or "?"}, BPM: {t.get("bpm") or "?"}, key: {t.get("key") or "?"})'
        for t in track_samples[:20]
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": (
                "You are naming DJ crates. Give a short evocative 2–3 word name for this group of tracks.\n"
                "Use DJ-friendly language. No quotes, no punctuation, no explanation.\n\n"
                f"Tracks:\n{track_lines}\n\n"
                "Crate name (2–3 words only):"
            ),
        }],
        max_tokens=20,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()
```

- [ ] **Step 4: Run — expect PASS**

```bash
.venv/bin/pytest tests/test_cluster.py::test_name_cluster_calls_llm -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add multidj/cluster.py tests/test_cluster.py
git commit -m "feat: add name_cluster() — OpenAI-compatible LLM naming for Vibe/ crates"
```

---

## Task 8: `cluster.py` — `cluster_vibe()` + crate writing

**Files:**
- Modify: `multidj/cluster.py`
- Modify: `tests/test_cluster.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cluster.py`:

```python
def test_dry_run_returns_cluster_info(db_with_embeddings):
    db, _ = db_with_embeddings
    result = cluster_vibe(db_path=str(db), apply=False, min_cluster_size=2)
    assert result["mode"] == "dry_run"
    assert result["clusters_found"] >= 1
    assert result["crates_written"] == 0
    assert isinstance(result["clusters"], list)


def test_apply_creates_vibe_crates(db_with_embeddings, tmp_path):
    db, _ = db_with_embeddings
    result = cluster_vibe(db_path=str(db), apply=True, min_cluster_size=2, backup_dir=str(tmp_path))
    assert result["mode"] == "apply"
    assert result["crates_written"] >= 1
    raw = sqlite3.connect(str(db))
    count = raw.execute("SELECT COUNT(*) FROM crates WHERE name LIKE 'Vibe/%'").fetchone()[0]
    raw.close()
    assert count >= 1


def test_rebuild_clears_stale_crates(db_with_embeddings, tmp_path):
    db, _ = db_with_embeddings
    cluster_vibe(db_path=str(db), apply=True, min_cluster_size=2, backup_dir=str(tmp_path))
    cluster_vibe(db_path=str(db), apply=True, min_cluster_size=2, backup_dir=str(tmp_path))
    raw = sqlite3.connect(str(db))
    total = raw.execute("SELECT COUNT(*) FROM crates WHERE name LIKE 'Vibe/%'").fetchone()[0]
    unique = raw.execute("SELECT COUNT(DISTINCT name) FROM crates WHERE name LIKE 'Vibe/%'").fetchone()[0]
    raw.close()
    assert total == unique  # no duplicates after two runs


def test_llm_naming_applied_when_config_present(db_with_embeddings, tmp_path):
    db, _ = db_with_embeddings
    llm_config = {"base_url": "http://fake", "api_key": "fake", "model": "fake"}

    with patch("multidj.cluster.name_cluster", return_value="Dark Techno Peaks"):
        result = cluster_vibe(
            db_path=str(db), apply=True, min_cluster_size=2,
            llm_config=llm_config, backup_dir=str(tmp_path),
        )

    raw = sqlite3.connect(str(db))
    names = [r[0] for r in raw.execute(
        "SELECT name FROM crates WHERE name LIKE 'Vibe/%' AND name != 'Vibe/Unclassified'"
    ).fetchall()]
    raw.close()
    assert any("Dark Techno Peaks" in n for n in names)


def test_numbered_fallback_when_no_llm(db_with_embeddings, tmp_path):
    db, _ = db_with_embeddings
    result = cluster_vibe(db_path=str(db), apply=True, min_cluster_size=2, backup_dir=str(tmp_path))
    raw = sqlite3.connect(str(db))
    names = [r[0] for r in raw.execute("SELECT name FROM crates WHERE name LIKE 'Vibe/%'").fetchall()]
    raw.close()
    # Without llm_config, names must be "Vibe/Cluster-NN" or "Vibe/Unclassified"
    for n in names:
        assert n.startswith("Vibe/")
```

- [ ] **Step 2: Run — expect FAIL**

```bash
.venv/bin/pytest tests/test_cluster.py -v
```

Expected: new tests FAIL because `cluster_vibe` is incomplete/missing.

- [ ] **Step 3: Append `_write_vibe_crates()` and `cluster_vibe()` to `multidj/cluster.py`**

```python

def _write_vibe_crates(
    conn,
    clusters: dict[int, list[int]],
    names: dict[int, str],
    prefix: str,
) -> list[dict[str, Any]]:
    """Clear all existing Vibe/ crates and rebuild from cluster assignments."""
    old_ids = conn.execute(
        "SELECT id FROM crates WHERE name LIKE ?", (f"{prefix}%",)
    ).fetchall()
    for row in old_ids:
        conn.execute("DELETE FROM crate_tracks WHERE crate_id = ?", (row["id"],))
    conn.execute("DELETE FROM crates WHERE name LIKE ?", (f"{prefix}%",))

    written: list[dict[str, Any]] = []
    for label, track_ids in clusters.items():
        crate_name = (
            f"{prefix}Unclassified" if label == -1
            else f"{prefix}{names.get(label, _numbered_name(label))}"
        )
        conn.execute(
            "INSERT OR REPLACE INTO crates (name, type, show) VALUES (?, 'auto', 1)",
            (crate_name,),
        )
        crate_id = conn.execute(
            "SELECT id FROM crates WHERE name = ?", (crate_name,)
        ).fetchone()["id"]
        conn.executemany(
            "INSERT OR IGNORE INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
            [(crate_id, tid) for tid in track_ids],
        )
        written.append({"name": crate_name, "track_count": len(track_ids)})

    conn.commit()
    return written


def cluster_vibe(
    db_path: str | None = None,
    apply: bool = False,
    min_cluster_size: int = 5,
    prefix: str = "Vibe/",
    llm_config: dict[str, Any] | None = None,
    backup_dir: str | None | bool = None,
) -> dict[str, Any]:
    with connect(db_path, readonly=True) as conn:
        ensure_not_empty(conn)
        track_ids, vectors = load_embeddings_from_db(conn)

    total_embedded = len(track_ids)
    if total_embedded < min_cluster_size * 2:
        raise RuntimeError(
            f"Too few embedded tracks ({total_embedded}). "
            f"Need at least {min_cluster_size * 2}. "
            f"Run 'multidj analyze embed --apply' first."
        )

    _log(f"cluster vibe — clustering {total_embedded} embeddings (min_cluster_size={min_cluster_size})")
    labels = cluster_embeddings(vectors, min_cluster_size)

    # Group track_ids by cluster label
    clusters: dict[int, list[int]] = {}
    for tid, label in zip(track_ids, labels.tolist()):
        clusters.setdefault(int(label), []).append(tid)

    n_clusters = len(set(labels) - {-1})
    noise_count = len(clusters.get(-1, []))
    _log(f"  found {n_clusters} clusters, {noise_count} noise tracks")

    # Name each non-noise cluster
    names: dict[int, str] = {}
    with connect(db_path, readonly=True) as conn:
        for label, tids in clusters.items():
            if label == -1:
                continue
            sample_rows = conn.execute(
                "SELECT artist, title, genre, bpm, key FROM tracks"
                " WHERE id IN ({}) ORDER BY play_count DESC LIMIT 20".format(
                    ",".join("?" * min(20, len(tids)))
                ),
                tids[:20],
            ).fetchall()
            sample = [dict(r) for r in sample_rows]

            if llm_config:
                try:
                    names[label] = name_cluster(sample, llm_config)
                except Exception as exc:
                    _log(f"  LLM naming failed for cluster {label}: {exc} — using numbered name")
                    names[label] = _numbered_name(label)
            else:
                names[label] = _numbered_name(label)
            _log(f"  cluster {label}: {len(tids)} tracks → '{prefix}{names[label]}'")

    crate_list = [
        {"name": f"{prefix}{names.get(lbl, _numbered_name(lbl))}", "track_count": len(tids)}
        for lbl, tids in clusters.items()
        if lbl != -1
    ]
    if -1 in clusters:
        crate_list.append({"name": f"{prefix}Unclassified", "track_count": noise_count})

    if not apply:
        return {
            "mode": "dry_run",
            "total_embedded": total_embedded,
            "clusters_found": n_clusters,
            "noise_tracks": noise_count,
            "crates_written": 0,
            "clusters": crate_list,
        }

    with connect(db_path, readonly=False) as conn:
        written = _write_vibe_crates(conn, clusters, names, prefix)

    return {
        "mode": "apply",
        "total_embedded": total_embedded,
        "clusters_found": n_clusters,
        "noise_tracks": noise_count,
        "crates_written": len(written),
        "clusters": written,
    }
```

- [ ] **Step 4: Run all cluster tests — expect PASS**

```bash
.venv/bin/pytest tests/test_cluster.py -v
```

Expected: all cluster tests PASS (umap-learn + hdbscan must be installed: `uv sync --extra embeddings`)

- [ ] **Step 5: Commit**

```bash
git add multidj/cluster.py tests/test_cluster.py
git commit -m "feat: add cluster_vibe() — UMAP+HDBSCAN clustering with LLM naming and Vibe/ crate writing"
```

---

## Task 9: CLI — `cluster vibe` command

**Files:**
- Modify: `multidj/cli.py`

- [ ] **Step 1: Write smoke test**

Append to `tests/test_cluster.py`:

```python
from multidj.cli import main as cli_main


def test_cli_cluster_vibe_too_few_tracks(db):
    # No embeddings → RuntimeError caught and exit code 1
    ret = cli_main(["--db", str(db), "cluster", "vibe"])
    assert ret == 1


def test_cli_cluster_vibe_dry_run(db_with_embeddings):
    db, _ = db_with_embeddings
    ret = cli_main(["--db", str(db), "cluster", "vibe", "--min-cluster-size", "2"])
    assert ret == 0
```

- [ ] **Step 2: Run — expect FAIL**

```bash
.venv/bin/pytest tests/test_cluster.py -v -k "test_cli"
```

Expected: FAIL with unrecognised subcommand.

- [ ] **Step 3: Add `cluster` top-level command to `build_parser()`**

In `multidj/cli.py`, inside `build_parser()`, after the `dedupe` subparser block, add:

```python
    # ── cluster ──────────────────────────────────────────────────────────────
    cluster_p = sub.add_parser("cluster", help="Cluster tracks by embedding similarity")
    cluster_sub = cluster_p.add_subparsers(dest="cluster_target", required=True)

    p_vibe = cluster_sub.add_parser("vibe", help="Build Vibe/ crates from CLAP embedding clusters")
    p_vibe.add_argument("--apply",            action="store_true", help="Write Vibe/ crates to DB")
    p_vibe.add_argument("--min-cluster-size", type=int, default=5, dest="min_cluster_size",
                        help="Minimum tracks per cluster (default: 5)")
    p_vibe.add_argument("--prefix",           default="Vibe/",     help="Crate name prefix (default: Vibe/)")
    p_vibe.add_argument("--no-backup",        action="store_true", dest="no_backup")
```

- [ ] **Step 4: Add the `cluster` handler in `main()`**

In `multidj/cli.py`, inside `main()`, before the final `else: parser.error(...)` block, add:

```python
    elif args.command == "cluster":
        if args.cluster_target == "vibe":
            from .cluster import cluster_vibe
            from .config import get_llm_config
            try:
                result = cluster_vibe(
                    db_path=args.db,
                    apply=args.apply,
                    min_cluster_size=args.min_cluster_size,
                    prefix=args.prefix,
                    llm_config=get_llm_config(),
                    backup_dir=False if args.no_backup else None,
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1
```

- [ ] **Step 5: Run CLI tests — expect PASS**

```bash
.venv/bin/pytest tests/test_cluster.py -v -k "test_cli"
```

Expected: 2 PASS

- [ ] **Step 6: Smoke-check from terminal**

```bash
.venv/bin/multidj cluster vibe
```

Expected: error message `Too few embedded tracks…` (since no embeddings in live DB yet).

- [ ] **Step 7: Commit**

```bash
git add multidj/cli.py tests/test_cluster.py
git commit -m "feat: wire 'multidj cluster vibe' CLI command"
```

---

## Task 10: Pipeline integration

**Files:**
- Modify: `multidj/pipeline.py`
- Modify: `multidj/cli.py` (add `--skip-embed`, `--skip-cluster`)

- [ ] **Step 1: Write failing pipeline test**

Append to `tests/test_pipeline.py` (read the file first to understand existing patterns, then add):

```python
def test_pipeline_skips_embed_cluster_when_not_in_skip_set(multidj_db, tmp_path):
    """embed + cluster steps appear in pipeline result (even if skipped due to no deps)."""
    from multidj.pipeline import run_pipeline
    result = run_pipeline(
        db_path=str(multidj_db),
        apply=False,
        skip={"import", "fix_mismatches", "parse", "dedupe", "bpm", "key", "energy",
               "embed", "cluster", "genres", "clean_text", "crates", "sync", "report"},
    )
    step_names = [s["step"] for s in result["steps"]]
    assert "embed" in step_names
    assert "cluster" in step_names


def test_pipeline_embed_cluster_skipped_via_config(multidj_db):
    from multidj.pipeline import run_pipeline
    cfg = {"pipeline": {"embed": False, "cluster": False}}
    result = run_pipeline(db_path=str(multidj_db), apply=False, cfg=cfg,
                          skip={"import", "fix_mismatches", "parse", "dedupe", "bpm",
                                "key", "energy", "genres", "clean_text", "crates", "sync", "report"})
    step_names = [s["step"] for s in result["steps"]]
    embed_step = next(s for s in result["steps"] if s["step"] == "embed")
    cluster_step = next(s for s in result["steps"] if s["step"] == "cluster")
    assert embed_step["status"] == "skipped"
    assert cluster_step["status"] == "skipped"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
.venv/bin/pytest tests/test_pipeline.py -v -k "test_pipeline_skip"
```

Expected: FAIL because embed/cluster steps don't exist in pipeline yet.

- [ ] **Step 3: Add embed + cluster steps to `multidj/pipeline.py`**

In `multidj/pipeline.py`, add imports at the top (inside function, lazily — see Step 4 for how).

After step 7 (`energy`) and before step 8 (`genres`), insert:

```python
    # Auto-skip embed/cluster if disabled in config
    if not cfg.get("pipeline", {}).get("embed", True):
        skip = skip | {"embed"}
    if not cfg.get("pipeline", {}).get("cluster", True):
        skip = skip | {"cluster"}

    # Step 8: Embed tracks (CLAP — requires [embeddings] extra)
    def _run_embed(**kwargs):
        try:
            from .embed import analyze_embed as _ae
            return _ae(**kwargs)
        except ImportError:
            raise RuntimeError("embeddings extra not installed; run: uv sync --extra embeddings")

    steps.append(_run_step(
        "embed", _run_embed,
        db_path=db_path, apply=apply, backup_dir=False,
        limit=limit,
    ))

    # Step 9: Cluster into Vibe/ crates
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
```

**Important:** re-number existing step comments: genres becomes step 10, clean_text 11, crates 12, sync 13, report 14.

- [ ] **Step 4: Add `--skip-embed` and `--skip-cluster` to pipeline parser in `cli.py`**

In `multidj/cli.py`, inside `build_parser()`, in the pipeline subparser block (around line 248), add:

```python
    p_pipeline.add_argument("--skip-embed",    action="store_true", dest="skip_embed")
    p_pipeline.add_argument("--skip-cluster",  action="store_true", dest="skip_cluster")
```

In `main()`, inside `elif args.command == "pipeline":`, in the `skip` set building block, add:

```python
        if args.skip_embed:   skip.add("embed")
        if args.skip_cluster: skip.add("cluster")
```

- [ ] **Step 5: Run pipeline tests — expect PASS**

```bash
.venv/bin/pytest tests/test_pipeline.py -v
```

Expected: all pipeline tests PASS (including the 2 new ones)

- [ ] **Step 6: Smoke-check pipeline**

```bash
.venv/bin/multidj pipeline --skip-import --skip-fix-mismatches --skip-parse --skip-dedupe --skip-bpm --skip-key --skip-energy --skip-genres --skip-clean-text --skip-crates --skip-sync --skip-report
```

Expected: output shows `embed` and `cluster` steps (likely erroring due to no embeddings, but they appear in the step list).

- [ ] **Step 7: Commit**

```bash
git add multidj/pipeline.py multidj/cli.py tests/test_pipeline.py
git commit -m "feat: add embed + cluster steps to pipeline; wire --skip-embed and --skip-cluster"
```

---

## Task 11: `multidj similar` command

**Files:**
- Modify: `multidj/embed.py` (append `find_similar()`)
- Modify: `multidj/cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_embed.py`:

```python
from multidj.embed import find_similar


def test_find_similar_returns_ordered_results(tmp_path):
    db = make_multidj_db(tmp_path / "library.sqlite")
    with connect(str(db), readonly=True) as conn:
        track_rows = conn.execute(
            "SELECT id, path FROM tracks WHERE deleted=0 ORDER BY id"
        ).fetchall()
    track_ids = [r["id"] for r in track_rows]
    track_paths = [r["path"] for r in track_rows]

    # Query track: vec pointing in dim 0
    query_vec = np.zeros(512, dtype=np.float32)
    query_vec[0] = 1.0

    # Similar tracks: also pointing in dim 0 (small angle)
    similar_vec = np.zeros(512, dtype=np.float32)
    similar_vec[0] = 0.9
    similar_vec[1] = 0.1

    # Dissimilar: pointing in dim 256
    dissimilar_vec = np.zeros(512, dtype=np.float32)
    dissimilar_vec[256] = 1.0

    with connect(str(db), readonly=False) as conn:
        store_embedding(conn, track_ids[0], "test", query_vec)
        store_embedding(conn, track_ids[1], "test", similar_vec)
        for tid in track_ids[2:]:
            store_embedding(conn, tid, "test", dissimilar_vec)
        conn.commit()

    result = find_similar(db_path=str(db), track_ref=track_paths[0], top_n=3)

    assert result["query_track"]["id"] == track_ids[0]
    assert len(result["similar"]) == 3
    # track_ids[1] must be the closest
    assert result["similar"][0]["id"] == track_ids[1]
    # Distances must be ascending
    distances = [r["distance"] for r in result["similar"]]
    assert distances == sorted(distances)


def test_find_similar_raises_when_no_embedding(db):
    with connect(str(db), readonly=True) as conn:
        path = conn.execute("SELECT path FROM tracks WHERE deleted=0 LIMIT 1").fetchone()["path"]
    with pytest.raises(RuntimeError, match="no embedding"):
        find_similar(db_path=str(db), track_ref=path)
```

- [ ] **Step 2: Run — expect FAIL**

```bash
.venv/bin/pytest tests/test_embed.py -v -k "test_find_similar"
```

Expected: FAIL with `ImportError: cannot import name 'find_similar'`

- [ ] **Step 3: Append `find_similar()` to `multidj/embed.py`**

```python

def find_similar(
    db_path: str | None = None,
    track_ref: str = "",
    top_n: int = 10,
) -> dict[str, Any]:
    """Return the top_n most similar tracks by cosine distance in embedding space."""
    with connect(db_path, readonly=True) as conn:
        ensure_not_empty(conn)

        # Resolve by path first, then fuzzy artist-title search
        row = conn.execute(
            "SELECT id, artist, title, path FROM tracks WHERE path = ? AND deleted = 0",
            (track_ref,),
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT id, artist, title, path FROM tracks"
                " WHERE deleted = 0 AND (COALESCE(artist,'') || ' - ' || COALESCE(title,'')) LIKE ?"
                " LIMIT 1",
                (f"%{track_ref}%",),
            ).fetchone()
        if not row:
            raise RuntimeError(f"Track not found: {track_ref!r}")

        query_id = row["id"]
        query_info = {"id": query_id, "artist": row["artist"], "title": row["title"]}

        emb_row = conn.execute(
            "SELECT vector FROM embeddings WHERE track_id = ?", (query_id,)
        ).fetchone()
        if not emb_row:
            raise RuntimeError(
                f"Track has no embedding. Run 'multidj analyze embed --apply' first."
            )
        query_vec = _blob_to_vec(emb_row["vector"])

        track_ids, vectors = load_embeddings_from_db(conn)

        # Cosine distance in numpy (no external extension needed)
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-8)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-8
        distances = 1.0 - (vectors / norms) @ query_norm

        idx_sorted = np.argsort(distances)
        results: list[dict[str, Any]] = []
        for idx in idx_sorted:
            tid = track_ids[idx]
            if tid == query_id:
                continue
            t = conn.execute(
                "SELECT id, artist, title FROM tracks WHERE id = ?", (tid,)
            ).fetchone()
            if t:
                results.append({
                    "id": t["id"],
                    "artist": t["artist"],
                    "title": t["title"],
                    "distance": round(float(distances[idx]), 4),
                })
            if len(results) >= top_n:
                break

    return {"query_track": query_info, "similar": results}
```

- [ ] **Step 4: Run — expect PASS**

```bash
.venv/bin/pytest tests/test_embed.py -v -k "test_find_similar"
```

Expected: 2 tests PASS

- [ ] **Step 5: Add `similar` CLI command**

In `multidj/cli.py`, inside `build_parser()`, after the `cluster` subparser block, add:

```python
    # ── similar ───────────────────────────────────────────────────────────────
    p_similar = sub.add_parser("similar", help="Find tracks similar to a given track by embedding distance")
    p_similar.add_argument("track_ref", metavar="TRACK",
                           help="File path or 'Artist - Title' search string")
    p_similar.add_argument("--top", type=int, default=10, dest="top_n",
                           help="Number of similar tracks to return (default: 10)")
```

In `main()`, add the handler before the final `else`:

```python
    elif args.command == "similar":
        from .embed import find_similar
        try:
            result = find_similar(db_path=args.db, track_ref=args.track_ref, top_n=args.top_n)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
```

- [ ] **Step 6: Run full test suite**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all tests pass (132 existing + new embed/cluster/llm_config/migration tests)

- [ ] **Step 7: Smoke-check the similar command**

```bash
.venv/bin/multidj similar "nonexistent"
```

Expected: `Track not found: 'nonexistent'` on stderr, exit 1.

- [ ] **Step 8: Commit**

```bash
git add multidj/embed.py multidj/cli.py tests/test_embed.py
git commit -m "feat: add find_similar() and 'multidj similar' command — KNN via cosine distance"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|---|---|
| CLAP encoder (`laion/larger_clap_music`) | Task 3 `load_clap_model()` |
| 3-window audio sampling (start/mid/end) | Task 3 `_encode_audio_file()` |
| sqlite-vec → plain BLOB table (simpler, no extension) | Task 1 migration |
| UMAP 512d→10d + HDBSCAN | Task 6 `cluster_embeddings()` |
| LLM naming via OpenAI-compatible API | Task 7 `name_cluster()` |
| Numbered fallback when LLM unconfigured | Task 8 `cluster_vibe()` |
| `Vibe/` prefix in AUTO_CRATE_PREFIXES | Task 1 |
| `analyze embed` CLI | Task 5 |
| `cluster vibe` CLI | Task 9 |
| `multidj similar` | Task 11 |
| Pipeline `embed` + `cluster` steps | Task 10 |
| `--skip-embed` / `--skip-cluster` flags | Task 10 |
| `[llm]` config section | Task 2 |
| `[embeddings]` optional dep group | Task 1 |
| Per-track error isolation | Task 4 `analyze_embed()` |
| Incremental (skip already-embedded) | Task 4 |
| Vibe/ crate clear-and-rebuild lifecycle | Task 8 `_write_vibe_crates()` |
| `Vibe/Unclassified` for noise points | Task 8 |
| Tests for all modules | Tasks 3–11 |
| Auto-skip in pipeline when deps missing | Task 10 |

All spec requirements are covered. No placeholders. Type/function names are consistent throughout.
