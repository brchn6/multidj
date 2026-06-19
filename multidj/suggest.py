"""DJ next-track suggestion for MultiDJ.

Given a currently playing track, returns the top-N best candidates for
what to play next.  Candidates are ranked by a composite score:

    score = cosine_similarity × 0.70
          + bpm_compatibility × 0.15
          + key_compatibility × 0.15

BPM compatibility
    1.0  if |bpm_diff| == 0
    Linear decay to 0.0 at ``bpm_window`` BPM (default 15).
    Tracks with no BPM data score 0.5.

Key compatibility (Camelot wheel)
    1.0  same key
    0.75 adjacent on the wheel: ±1 position (same letter), or same
         position opposite letter (relative major/minor)
    0.0  otherwise
    Tracks with no key data score 0.5.

Cluster filtering
    By default, candidates are restricted to the same ``Vibe/`` cluster
    as the query track (when cluster data exists).  Pass
    ``any_cluster=True`` to search the full library.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

from .db import connect, ensure_not_empty
from .embed import _blob_to_vec, load_embeddings_from_db, MODEL_CLAP, MODEL_CLAMP3
from .constants import CAMELOT_KEY_MAP


# ---------------------------------------------------------------------------
# Camelot wheel helpers
# ---------------------------------------------------------------------------

_CAMELOT_RE = re.compile(r"^(\d{1,2})([AB])$", re.IGNORECASE)

# Musical notation → Camelot: reuse the map from constants but also accept
# lowercase-root minor convention (e.g. "Gmin" → "6A").
_NOTE_RE = re.compile(r"^([A-G][b#]?)\s*(min|maj|m|M)?$")


def _parse_camelot(key: str | None) -> tuple[int, str] | None:
    """Return (number, letter) for any key string, or None if unparseable.

    Accepts:
      - Camelot: "9B", "1A", "12B"
      - Musical: "Gmin", "F#min", "Am", "C", "Dmaj", "Bb", "Bbmaj", "Cmin"
    """
    if not key:
        return None
    k = key.strip()

    # Direct Camelot format
    m = _CAMELOT_RE.match(k)
    if m:
        return (int(m.group(1)), m.group(2).upper())

    # Already in CAMELOT_KEY_MAP (e.g. "Cmin", "Dmaj")
    if k in CAMELOT_KEY_MAP:
        camelot = CAMELOT_KEY_MAP[k]
        m2 = _CAMELOT_RE.match(camelot)
        if m2:
            return (int(m2.group(1)), m2.group(2).upper())

    # Try building the canonical key string for lookup
    m3 = _NOTE_RE.match(k)
    if m3:
        root = m3.group(1)
        suffix = (m3.group(2) or "").lower()
        if suffix in ("min", "m"):
            canonical = root + "min"
        elif suffix in ("maj", "M"):
            canonical = root + "maj"
        else:
            # bare root = major
            canonical = root + "maj"
        if canonical in CAMELOT_KEY_MAP:
            camelot = CAMELOT_KEY_MAP[canonical]
            m4 = _CAMELOT_RE.match(camelot)
            if m4:
                return (int(m4.group(1)), m4.group(2).upper())

    return None


def _key_compat_score(key_a: str | None, key_b: str | None) -> float:
    """Camelot harmonic compatibility score between two key strings.

    Returns 1.0 (same), 0.75 (adjacent/relative), or 0.0 (incompatible).
    Missing keys score 0.5 (neutral).
    """
    ca = _parse_camelot(key_a)
    cb = _parse_camelot(key_b)
    if ca is None or cb is None:
        return 0.5
    na, la = ca
    nb, lb = cb
    if na == nb and la == lb:
        return 1.0
    # Adjacent on same ring: ±1 position (wrap 1–12)
    if la == lb and (na % 12 + 1 == nb or nb % 12 + 1 == na):
        return 0.75
    # Relative major/minor: same position, different letter
    if na == nb:
        return 0.75
    return 0.0


def _bpm_compat_score(bpm_a: float | None, bpm_b: float | None, window: float = 15.0) -> float:
    """Linear BPM compatibility score in [0, 1].

    Returns 1.0 at zero difference, 0.0 at ≥ window BPM apart.
    Missing BPM scores 0.5 (neutral).
    """
    if not bpm_a or not bpm_b:
        return 0.5
    diff = abs(float(bpm_a) - float(bpm_b))
    return max(0.0, 1.0 - diff / window)


# ---------------------------------------------------------------------------
# Cluster membership helpers
# ---------------------------------------------------------------------------

def _get_vibe_cluster(conn, track_id: int) -> str | None:
    """Return the Vibe/ crate name for a track, or None if unclustered."""
    row = conn.execute(
        """
        SELECT c.name FROM crates c
        JOIN crate_tracks ct ON ct.crate_id = c.id
        WHERE ct.track_id = ? AND c.name LIKE 'Vibe/%'
        LIMIT 1
        """,
        (track_id,),
    ).fetchone()
    return row["name"] if row else None


def _tracks_in_cluster(conn, cluster_name: str) -> set[int]:
    """Return all track IDs in the named Vibe/ cluster."""
    rows = conn.execute(
        """
        SELECT ct.track_id FROM crate_tracks ct
        JOIN crates c ON ct.crate_id = c.id
        WHERE c.name = ?
        """,
        (cluster_name,),
    ).fetchall()
    return {r["track_id"] for r in rows}


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def suggest_next(
    db_path: str | None = None,
    track_ref: str = "",
    top_n: int = 10,
    bpm_window: float = 15.0,
    any_cluster: bool = False,
    model: str | None = None,
) -> dict[str, Any]:
    """Return the top_n best next-track candidates for *track_ref*.

    Parameters
    ----------
    track_ref:
        File path or 'Artist - Title' search string identifying the query track.
    top_n:
        How many suggestions to return.
    bpm_window:
        BPM tolerance for BPM compatibility scoring (default 15).
    any_cluster:
        If False (default), restrict candidates to the same Vibe/ cluster.
        If True, search the whole library.
    model:
        Embedding model alias ("clap", "clamp3") or full model name.
        Defaults to whichever model the query track's embedding uses.
    """
    # Normalise model aliases
    if model in ("clap",):
        model = MODEL_CLAP
    elif model in ("clamp3",):
        model = MODEL_CLAMP3

    with connect(db_path, readonly=True) as conn:
        ensure_not_empty(conn)

        # --- Resolve query track ---
        row = conn.execute(
            "SELECT id, artist, title, bpm, key, path FROM tracks WHERE path = ? AND deleted = 0",
            (track_ref,),
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT id, artist, title, bpm, key, path FROM tracks"
                " WHERE deleted = 0 AND (COALESCE(artist,'') || ' - ' || COALESCE(title,'')) LIKE ?"
                " LIMIT 1",
                (f"%{track_ref}%",),
            ).fetchone()
        if not row:
            raise RuntimeError(f"Track not found: {track_ref!r}")

        query_id = row["id"]
        query_bpm = row["bpm"]
        query_key = row["key"]
        query_info = {
            "id": query_id,
            "artist": row["artist"],
            "title": row["title"],
            "bpm": query_bpm,
            "key": query_key,
        }

        # --- Find the query embedding ---
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

        # --- Cluster filter ---
        cluster_name: str | None = None
        cluster_ids: set[int] | None = None
        if not any_cluster:
            cluster_name = _get_vibe_cluster(conn, query_id)
            if cluster_name:
                cluster_ids = _tracks_in_cluster(conn, cluster_name)

        # --- Load all embeddings ---
        track_ids, vectors = load_embeddings_from_db(conn, model_name=active_model)

        # Apply cluster filter
        if cluster_ids is not None:
            filtered = [(tid, vec) for tid, vec in zip(track_ids, vectors) if tid in cluster_ids]
            if filtered:
                track_ids, vectors = zip(*filtered)
                track_ids = list(track_ids)
                vectors = np.stack(vectors)
            else:
                # Cluster found but empty embeddings — fall back to full library
                cluster_name = None
                cluster_ids = None

        # --- Cosine similarity ---
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-8)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-8
        sims = (vectors / norms) @ query_norm  # (n,) cosine similarities

        # --- Build candidate list ---
        idx_sorted = np.argsort(-sims)  # descending
        results: list[dict[str, Any]] = []
        for idx in idx_sorted:
            tid = track_ids[idx]
            if tid == query_id:
                continue
            t = conn.execute(
                "SELECT id, artist, title, bpm, key FROM tracks WHERE id = ?", (tid,)
            ).fetchone()
            if not t:
                continue

            cos_sim = float(sims[idx])
            bpm_s = _bpm_compat_score(query_bpm, t["bpm"], bpm_window)
            key_s = _key_compat_score(query_key, t["key"])
            total_score = cos_sim * 0.70 + bpm_s * 0.15 + key_s * 0.15

            results.append({
                "id": t["id"],
                "artist": t["artist"],
                "title": t["title"],
                "bpm": t["bpm"],
                "key": t["key"],
                "cosine_sim": round(cos_sim, 4),
                "bpm_score": round(bpm_s, 3),
                "key_score": round(key_s, 3),
                "score": round(total_score, 4),
            })
            if len(results) >= top_n:
                break

    # Sort by composite score (should already be roughly sorted by cosine, but re-sort to be safe)
    results.sort(key=lambda r: r["score"], reverse=True)

    return {
        "query_track": query_info,
        "cluster": cluster_name,
        "model": active_model,
        "bpm_window": bpm_window,
        "suggestions": results[:top_n],
    }
