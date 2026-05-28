from __future__ import annotations

import os
import sys
from typing import Any


def _progress(msg: str, end: str = "\n") -> None:
    print(msg, file=sys.stderr, end=end, flush=True)


def _suppress_decoder_noise():
    """Context manager: redirect stderr to /dev/null during audio decode."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        os.dup2(devnull, 2)
        os.close(devnull)
        try:
            yield
        finally:
            os.dup2(old_stderr, 2)
            os.close(old_stderr)

    return _ctx()


try:
    import allin1  # type: ignore  # noqa: F401
    import librosa  # type: ignore  # noqa: F401
    import numpy as np  # type: ignore  # noqa: F401
except ImportError:
    allin1 = None  # type: ignore
    librosa = None  # type: ignore
    np = None  # type: ignore


_LABEL_MAP: dict[str, str] = {
    "intro":        "intro",
    "verse":        "verse",
    "pre-chorus":   "pre-chorus",
    "chorus":       "chorus",
    "bridge":       "bridge",
    "breakdown":    "breakdown",
    "outro":        "outro",
    "instrumental": "instrumental",
}

_DROP_CANDIDATES = {"chorus", "instrumental"}


def detect_cues(filepath: str, bpm: float) -> list[dict[str, Any]]:
    """Run allin1 + librosa cross-validation; return cue candidates.

    Each item: {'type': str, 'position': float, 'confidence': str, 'label': str}
    Positions are seconds from track start, snapped to the nearest allin1 downbeat.
    A derived 'drop' cue is added from the first chorus/instrumental segment.
    """
    if allin1 is None or librosa is None or np is None:
        raise RuntimeError(
            "Missing optional dependency 'embeddings'. Install with:\n\n"
            "    uv sync --extra embeddings\n"
        )

    analysis = allin1.analyze(filepath)
    segments = analysis.segments or []
    downbeats = list(analysis.downbeats) if analysis.downbeats else []

    bar_duration = (60.0 / bpm * 4) if bpm > 0 else 2.0
    tolerance = bar_duration

    with _suppress_decoder_noise():
        y, sr = librosa.load(filepath, sr=22050, mono=True)

    hop_length = 512
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    flux = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    chroma_diff = np.sum(np.abs(np.diff(chroma, axis=1)), axis=0)
    pad = len(rms) - len(chroma_diff)
    if pad > 0:
        chroma_diff = np.pad(chroma_diff, (0, pad))
    else:
        chroma_diff = chroma_diff[: len(rms)]

    times = librosa.times_like(rms, sr=sr, hop_length=hop_length)

    def _norm(arr: Any) -> Any:
        m = float(arr.max())
        return arr / m if m > 0 else arr

    combined = _norm(rms) + _norm(flux) + _norm(chroma_diff)
    peak_indices = librosa.util.peak_pick(
        combined,
        pre_max=3, post_max=3, pre_avg=3, post_avg=5,
        delta=0.1, wait=10,
    )
    librosa_transitions = {float(times[i]) for i in peak_indices}

    def _snap(pos: float) -> float:
        if not downbeats:
            return pos
        return min(downbeats, key=lambda d: abs(d - pos))

    cues: list[dict[str, Any]] = []
    drop_candidate: dict[str, Any] | None = None

    for seg in segments:
        label = (seg.label or "").lower()
        cue_type = _LABEL_MAP.get(label, "hot_cue")
        position = _snap(seg.start)

        agrees = any(abs(t - position) <= tolerance for t in librosa_transitions)
        confidence = "high" if agrees else "low"

        cues.append({
            "type": cue_type,
            "position": position,
            "confidence": confidence,
            "label": seg.label,
        })

        if drop_candidate is None and cue_type in _DROP_CANDIDATES:
            drop_candidate = {
                "type": "drop",
                "position": position,
                "confidence": confidence,
                "label": f"Drop ({seg.label})",
            }

    if drop_candidate is not None:
        cues.append(drop_candidate)

    return cues


def analyze_cues(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    backup_dir: Any = False,
) -> dict[str, Any]:
    """Detect and store structural cues for tracks missing them.

    With force=True, re-analyzes tracks that already have cues.
    """
    from .db import connect, resolve_db_path, ensure_not_empty

    db_path = str(resolve_db_path(db_path))

    with connect(db_path, readonly=True) as conn:
        ensure_not_empty(conn)

        if force:
            where = "1=1"
        else:
            where = """
                NOT EXISTS (
                    SELECT 1 FROM cue_points cp
                    WHERE cp.track_id = t.id AND cp.source = 'auto'
                )
            """

        rows = conn.execute(
            f"SELECT id, path, bpm FROM tracks t WHERE deleted=0 AND {where} ORDER BY id"
        ).fetchall()

    candidates = [dict(r) for r in rows]
    total_candidates = len(candidates)
    if limit is not None:
        candidates = candidates[:limit]

    if not apply:
        _progress(
            f"Dry-run: {total_candidates:,} tracks would be analyzed "
            "(run with --apply to process)"
        )
        return {"mode": "dry_run", "total_candidates": total_candidates, "processed": 0}

    _progress(f"Analyzing {len(candidates):,} tracks for structural cues...")

    succeeded = 0
    failed = 0
    errors: list[dict[str, str]] = []

    with connect(db_path, readonly=False) as conn:
        for row in candidates:
            track_id = row["id"]
            filepath = row["path"]
            bpm = float(row["bpm"] or 120.0)
            _progress(f"  {filepath.split('/')[-1][:60]}", end=" ")
            try:
                cues = detect_cues(filepath, bpm)

                conn.execute(
                    "DELETE FROM cue_points WHERE track_id=? AND source='auto'",
                    (track_id,),
                )
                conn.executemany(
                    """
                    INSERT INTO cue_points (track_id, type, position, label, confidence, source)
                    VALUES (?, ?, ?, ?, ?, 'auto')
                    """,
                    [(track_id, c["type"], c["position"], c["label"], c["confidence"])
                     for c in cues],
                )

                non_intro = [c for c in cues if c["type"] not in ("intro", "hot_cue")]
                intro_end = non_intro[0]["position"] if non_intro else None
                outro_cue = next((c for c in cues if c["type"] == "outro"), None)
                outro_start = outro_cue["position"] if outro_cue else None

                conn.execute(
                    "UPDATE tracks SET intro_end=?, outro_start=? WHERE id=?",
                    (intro_end, outro_start, track_id),
                )

                types = [c["type"] for c in cues if c["type"] not in ("hot_cue",)]
                marks = {c["type"]: "✓" if c["confidence"] == "high" else "" for c in cues}
                summary = " ".join(f"{t}{marks.get(t,'')}" for t in types[:6])
                _progress(f"→ {summary}")
                succeeded += 1
            except Exception as exc:
                failed += 1
                errors.append({"path": filepath, "reason": str(exc)})
                _progress(f"ERROR: {exc}")

        conn.commit()

    return {
        "mode": "apply",
        "total_candidates": total_candidates,
        "processed": len(candidates),
        "succeeded": succeeded,
        "failed": failed,
        "errors": errors,
    }


def clear_cues(
    db_path: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Remove all source='auto' cue points and reset tracks.intro_end / outro_start."""
    from .db import connect, resolve_db_path

    db_path = str(resolve_db_path(db_path))

    with connect(db_path, readonly=True) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM cue_points WHERE source='auto'"
        ).fetchone()[0]

    if not apply:
        _progress(f"Dry-run: {count:,} auto-detected cues would be removed")
        return {"mode": "dry_run", "would_remove": count}

    with connect(db_path, readonly=False) as conn:
        conn.execute("DELETE FROM cue_points WHERE source='auto'")
        conn.execute("UPDATE tracks SET intro_end=NULL, outro_start=NULL WHERE deleted=0")
        conn.commit()

    _progress(f"Removed {count:,} auto-detected cues")
    return {"mode": "apply", "removed": count}
