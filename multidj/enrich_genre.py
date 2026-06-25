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
    """Harden genre metadata via layered enrichment: file→Discogs→MusicBrainz→CLAP."""
    enrich_cfg = enrich_cfg or {}
    mode = "apply" if apply else "dry_run"

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
        "user_agent", "multidj/1.0"
    )

    with connect(db_path, readonly=False) as _guard:
        ensure_not_empty(_guard)

    with connect(db_path, readonly=True) as conn:
        if force:
            where = "deleted = 0 AND (genre_source IS NULL OR genre_source != 'manual')"
        else:
            where = "deleted = 0 AND genre_source IS NULL"
        sql = f"SELECT id, artist, title, genre, path FROM tracks WHERE {where} ORDER BY id"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
        infer_cache: dict[int, str] = {}
        for row in rows:
            if _is_specific(row["genre"]):
                infer_cache[row["id"]] = _infer_source(row["id"], conn)

    total_candidates = len(rows)
    applied = 0
    errors: list[dict] = []
    updates: list[tuple[str | None, str | None, float | None, int]] = []

    for row in rows:
        track_id = row["id"]
        artist = row["artist"] or ""
        title = row["title"] or ""
        genre = row["genre"]
        try:
            if _is_specific(genre) and not force:
                # Incremental mode: tag existing specific genre with source, skip API.
                source = infer_cache.get(track_id, "file")
                updates.append((genre, source, 1.0, track_id))
                continue

            # Attempt web enrichment (force=True, or genre is uninformative/None).
            # Always call search_discogs (passes None client when unconfigured;
            # the real function handles NoneType client via try/except).
            if artist and title:
                hit = search_discogs(artist, title, discogs_client)
                if hit and hit.get("styles"):
                    new_genre = hit["styles"][0]
                    updates.append((new_genre, "discogs", 1.0, track_id))
                    continue

            if artist and title:
                hit = search_musicbrainz(artist, title, mb_user_agent)
                if hit and hit.get("genre") and _is_specific(hit["genre"]):
                    updates.append((hit["genre"], "musicbrainz", 1.0, track_id))
                    continue

            # No API hit — fall back to existing specific genre if present.
            if _is_specific(genre):
                source = infer_cache.get(track_id, "file")
                updates.append((genre, source, 1.0, track_id))
                continue

            updates.append((None, None, None, track_id))

        except Exception as exc:
            errors.append({"track_id": track_id, "artist": artist, "title": title, "error": str(exc)})

    clap_needed = [(i, u[3]) for i, u in enumerate(updates) if u[:3] == (None, None, None)]

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

    real_updates = [(g, s, c, tid) for g, s, c, tid in updates if s is not None]
    if apply and real_updates:
        with connect(db_path, readonly=False) as conn:
            conn.executemany(
                "UPDATE tracks SET genre=?, genre_source=?, genre_confidence=? WHERE id=?",
                real_updates,
            )
            conn.commit()
        applied = len(real_updates)

    return {
        "mode": mode,
        "total_candidates": total_candidates,
        "applied": applied if apply else 0,
        "would_apply": len(real_updates),
        "errors": len(errors),
        "error_details": errors,
    }
