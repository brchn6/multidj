"""Audio embedding support for MultiDJ.

Supports two backends, selected via the ``model`` parameter:

* ``"clap"``   — LAION CLAP (``laion/larger_clap_music``), 512-dim.
                 Default; requires ``uv sync --extra embeddings``.
* ``"clamp3"`` — CLaMP 3 SAAS (MERT-v1-95M → CLaMP3 audio encoder), 768-dim.
                 Requires ``uv sync --extra clamp3`` and
                 ``git submodule update --init vendor/clamp3``.

Both backends share the same ``embeddings`` table, keyed by
``(track_id, model_name)``.  The ``model_name`` column distinguishes them so
CLAP and CLaMP3 embeddings can coexist for the same track.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

from .db import connect, ensure_not_empty


# ---------------------------------------------------------------------------
# Model name constants
# ---------------------------------------------------------------------------
MODEL_CLAP = "laion/larger_clap_music"
MODEL_CLAMP3 = "clamp3_saas"

# Legacy alias kept for backward compatibility
MODEL_NAME = MODEL_CLAP

_DEFAULT_MODEL = MODEL_CLAP

_SR = 48_000
_WINDOW_SECS = 30


def _progress(msg: str, end: str = "\n") -> None:
    print(msg, file=sys.stderr, end=end, flush=True)


# ---------------------------------------------------------------------------
# Blob serialisation helpers (shared by both backends)
# ---------------------------------------------------------------------------

def _vec_to_blob(v: np.ndarray) -> bytes:
    return v.astype(np.float32).tobytes()


def _blob_to_vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32).copy()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def store_embedding(
    conn,
    track_id: int,
    model_name: str,
    vector: np.ndarray,
) -> None:
    """Upsert an embedding row keyed by (track_id, model_name)."""
    conn.execute(
        """
        INSERT INTO embeddings (track_id, model_name, vector)
        VALUES (?, ?, ?)
        ON CONFLICT(track_id, model_name) DO UPDATE
            SET vector     = excluded.vector,
                created_at = datetime('now')
        """,
        (track_id, model_name, _vec_to_blob(vector)),
    )


def load_embeddings_from_db(
    conn,
    model_name: str | None = None,
) -> tuple[list[int], np.ndarray]:
    """Return (track_ids, matrix) for all non-deleted embedded tracks.

    If *model_name* is given, only embeddings for that model are returned.
    If *model_name* is ``None``, all embeddings are returned (picking the most
    recently stored one per track when multiple models exist).

    The returned matrix has shape ``(n, dim)`` where ``dim`` depends on the
    model (512 for CLAP, 768 for CLaMP3).  When no rows exist, an empty array
    with shape ``(0, 0)`` is returned.
    """
    if model_name:
        rows = conn.execute(
            """
            SELECT e.track_id, e.vector
            FROM embeddings e
            JOIN tracks t ON e.track_id = t.id
            WHERE t.deleted = 0 AND e.model_name = ?
            ORDER BY e.track_id
            """,
            (model_name,),
        ).fetchall()
    else:
        # Return the most recent embedding per track (across all models)
        rows = conn.execute(
            """
            SELECT e.track_id, e.vector
            FROM embeddings e
            JOIN tracks t ON e.track_id = t.id
            WHERE t.deleted = 0
            GROUP BY e.track_id
            HAVING e.created_at = MAX(e.created_at)
            ORDER BY e.track_id
            """,
        ).fetchall()

    if not rows:
        return [], np.empty((0, 0), dtype=np.float32)

    track_ids = [r["track_id"] for r in rows]
    vecs = [_blob_to_vec(r["vector"]) for r in rows]
    matrix = np.stack(vecs)
    return track_ids, matrix


# ---------------------------------------------------------------------------
# CLAP backend
# ---------------------------------------------------------------------------

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
    _progress(f"Loading CLAP model ({MODEL_CLAP}) on {device}…")
    model = ClapModel.from_pretrained(MODEL_CLAP).to(device)
    processor = ClapProcessor.from_pretrained(MODEL_CLAP)
    model.eval()
    return model, processor, device


def _encode_audio_file(filepath: str, model: Any, processor: Any, device: str) -> np.ndarray:
    """Encode a single audio file with CLAP: 3 × 30 s windows → mean 512-d vector."""
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
        inputs = processor(audio=w, sampling_rate=_SR, return_tensors="pt", padding=True)
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            feat = model.get_audio_features(**inputs)
        embeddings.append(feat.pooler_output[0].cpu().numpy())

    return np.mean(embeddings, axis=0)


# ---------------------------------------------------------------------------
# Unified analyze_embed (dispatches to CLAP or CLaMP3)
# ---------------------------------------------------------------------------

def analyze_embed(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    backup_dir: str | None | bool = None,
    model: str = _DEFAULT_MODEL,
) -> dict[str, Any]:
    """Embed all un-embedded tracks using the selected model backend.

    Parameters
    ----------
    model:
        ``"clap"`` (default) or ``"clamp3"``.  Can also be the full model
        name string (``"laion/larger_clap_music"`` or ``"clamp3_saas"``).
    """
    # Normalise model alias
    if model in ("clap",):
        model = MODEL_CLAP
    elif model in ("clamp3",):
        model = MODEL_CLAMP3

    # Ensure migration 007 (composite PK) is applied before reading.
    with connect(db_path, readonly=False) as _:
        pass

    with connect(db_path, readonly=True) as conn:
        ensure_not_empty(conn)
        if force:
            rows = conn.execute(
                "SELECT id, path FROM tracks WHERE deleted=0 ORDER BY id"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT t.id, t.path
                FROM tracks t
                LEFT JOIN embeddings e ON t.id = e.track_id AND e.model_name = ?
                WHERE t.deleted = 0 AND e.track_id IS NULL
                ORDER BY t.id
                """,
                (model,),
            ).fetchall()

    candidates = [{"id": r["id"], "path": r["path"]} for r in rows]
    if limit is not None:
        candidates = candidates[:limit]
    total = len(candidates)

    _progress(f"analyze embed — {total} track(s) to embed (model: {model})")

    if not apply:
        return {
            "mode": "dry_run",
            "total_candidates": total,
            "processed": 0,
            "succeeded": 0,
            "errors": 0,
            "model": model,
        }

    # --- Load model ---
    if model == MODEL_CLAMP3:
        from .embed_clamp3 import load_clamp3_model, encode_audio_clamp3
        mert_model, mert_processor, clamp3_model, device = load_clamp3_model()

        def _encode(path: str) -> np.ndarray:
            return encode_audio_clamp3(path, mert_model, mert_processor, clamp3_model, device)
    else:
        clap_model, processor, device = load_clap_model()

        def _encode(path: str) -> np.ndarray:  # type: ignore[misc]
            return _encode_audio_file(path, clap_model, processor, device)

    succeeded = errors = 0

    with connect(db_path, readonly=False) as conn:
        for i, row in enumerate(candidates):
            _progress(f"  [{i + 1}/{total}] {Path(row['path']).name}", end="\r")
            try:
                vec = _encode(row["path"])
                store_embedding(conn, row["id"], model, vec)
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
        "model": model,
    }


# ---------------------------------------------------------------------------
# Similarity search
# ---------------------------------------------------------------------------

def find_similar(
    db_path: str | None = None,
    track_ref: str = "",
    top_n: int = 10,
    model: str | None = None,
) -> dict[str, Any]:
    """Return the top_n most similar tracks by cosine distance in embedding space.

    If *model* is ``None``, uses whatever embeddings are stored for the query
    track (preferring the most recently stored model).
    """
    # Normalise model aliases
    if model in ("clap",):
        model = MODEL_CLAP
    elif model in ("clamp3",):
        model = MODEL_CLAMP3

    with connect(db_path, readonly=True) as conn:
        ensure_not_empty(conn)

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

        # Find the query embedding (prefer the requested model if specified)
        if model:
            emb_row = conn.execute(
                "SELECT vector, model_name FROM embeddings WHERE track_id = ? AND model_name = ?",
                (query_id, model),
            ).fetchone()
        else:
            emb_row = conn.execute(
                "SELECT vector, model_name FROM embeddings WHERE track_id = ? ORDER BY created_at DESC LIMIT 1",
                (query_id,),
            ).fetchone()

        if not emb_row:
            raise RuntimeError(
                "Track has no embedding. Run 'multidj analyze embed --apply' first."
            )
        query_vec = _blob_to_vec(emb_row["vector"])
        active_model = emb_row["model_name"]

        # Load all embeddings for the same model
        track_ids, vectors = load_embeddings_from_db(conn, model_name=active_model)

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

    return {"query_track": query_info, "similar": results, "model": active_model}
