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
        inputs = processor(audios=w, sampling_rate=_SR, return_tensors="pt", padding=True)
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            feat = model.get_audio_features(**inputs)
        embeddings.append(feat[0].cpu().numpy())

    return np.mean(embeddings, axis=0)
