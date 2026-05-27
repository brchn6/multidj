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
