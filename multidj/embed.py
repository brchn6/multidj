from __future__ import annotations

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
        inputs = processor(audio=w, sampling_rate=_SR, return_tensors="pt", padding=True)
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            feat = model.get_audio_features(**inputs)
        embeddings.append(feat.pooler_output[0].cpu().numpy())

    return np.mean(embeddings, axis=0)


def analyze_embed(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    backup_dir: str | None | bool = None,
) -> dict[str, Any]:
    # Ensure migration 005 (embeddings table) is applied before reading.
    with connect(db_path, readonly=False) as _:
        pass
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


def find_similar(
    db_path: str | None = None,
    track_ref: str = "",
    top_n: int = 10,
) -> dict[str, Any]:
    """Return the top_n most similar tracks by cosine distance in embedding space."""
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

        emb_row = conn.execute(
            "SELECT vector FROM embeddings WHERE track_id = ?", (query_id,)
        ).fetchone()
        if not emb_row:
            raise RuntimeError(
                f"Track has no embedding. Run 'multidj analyze embed --apply' first."
            )
        query_vec = _blob_to_vec(emb_row["vector"])

        track_ids, vectors = load_embeddings_from_db(conn)

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
