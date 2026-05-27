from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.multidj_factory import make_multidj_db


@pytest.fixture()
def db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_allin1_result(segments=None, downbeats=None):
    """Build a mock allin1 analysis result."""
    if segments is None:
        segments = [
            MagicMock(start=0.0,   end=30.0,  label="intro"),
            MagicMock(start=30.0,  end=90.0,  label="verse"),
            MagicMock(start=90.0,  end=150.0, label="chorus"),
            MagicMock(start=150.0, end=180.0, label="outro"),
        ]
    if downbeats is None:
        downbeats = [float(i * 2) for i in range(100)]
    result = MagicMock()
    result.segments = segments
    result.downbeats = downbeats
    return result


# ---------------------------------------------------------------------------
# detect_cues — unit tests
# ---------------------------------------------------------------------------

def test_detect_cues_returns_list_of_dicts(tmp_path):
    from multidj.cues import detect_cues

    allin1_result = _make_allin1_result()

    import numpy as np

    with (
        patch("multidj.cues.allin1") as mock_a1,
        patch("multidj.cues.librosa") as mock_lr,
        patch("multidj.cues.np", np),
    ):
        mock_a1.analyze.return_value = allin1_result
        mock_lr.load.return_value = (np.zeros(22050 * 10), 22050)
        mock_lr.feature.rms.return_value = np.array([np.zeros(500)])
        mock_lr.onset.onset_strength.return_value = np.zeros(500)
        mock_lr.feature.chroma_cqt.return_value = np.zeros((12, 500))
        mock_lr.times_like.return_value = np.linspace(0, 10, 500)
        mock_lr.util.peak_pick.return_value = np.array([], dtype=int)

        cues = detect_cues("/fake/track.mp3", bpm=128.0)

    assert isinstance(cues, list)
    assert len(cues) > 0
    for c in cues:
        assert "type" in c
        assert "position" in c
        assert "confidence" in c
        assert "label" in c


def test_detect_cues_high_confidence_when_librosa_agrees(tmp_path):
    from multidj.cues import detect_cues
    import numpy as np

    allin1_result = _make_allin1_result()

    with (
        patch("multidj.cues.allin1") as mock_a1,
        patch("multidj.cues.librosa") as mock_lr,
        patch("multidj.cues.np", np),
    ):
        mock_a1.analyze.return_value = allin1_result
        mock_lr.load.return_value = (np.zeros(22050 * 200), 22050)
        mock_lr.feature.rms.return_value = np.array([np.zeros(500)])
        mock_lr.onset.onset_strength.return_value = np.zeros(500)
        mock_lr.feature.chroma_cqt.return_value = np.zeros((12, 500))
        mock_lr.times_like.return_value = np.array([0.0, 30.0, 90.0, 150.0] + [200.0] * 496)
        mock_lr.util.peak_pick.return_value = np.array([0, 1, 2, 3])

        cues = detect_cues("/fake/track.mp3", bpm=128.0)

    high = [c for c in cues if c["confidence"] == "high"]
    assert len(high) >= 3


def test_detect_cues_low_confidence_when_librosa_disagrees(tmp_path):
    from multidj.cues import detect_cues
    import numpy as np

    allin1_result = _make_allin1_result()

    with (
        patch("multidj.cues.allin1") as mock_a1,
        patch("multidj.cues.librosa") as mock_lr,
        patch("multidj.cues.np", np),
    ):
        mock_a1.analyze.return_value = allin1_result
        mock_lr.load.return_value = (np.zeros(22050 * 200), 22050)
        mock_lr.feature.rms.return_value = np.array([np.zeros(500)])
        mock_lr.onset.onset_strength.return_value = np.zeros(500)
        mock_lr.feature.chroma_cqt.return_value = np.zeros((12, 500))
        mock_lr.times_like.return_value = np.linspace(0, 200, 500)
        mock_lr.util.peak_pick.return_value = np.array([], dtype=int)

        cues = detect_cues("/fake/track.mp3", bpm=128.0)

    low = [c for c in cues if c["confidence"] == "low"]
    assert len(low) > 0


def test_detect_cues_derives_drop_from_chorus():
    from multidj.cues import detect_cues
    import numpy as np

    allin1_result = _make_allin1_result()

    with (
        patch("multidj.cues.allin1") as mock_a1,
        patch("multidj.cues.librosa") as mock_lr,
        patch("multidj.cues.np", np),
    ):
        mock_a1.analyze.return_value = allin1_result
        mock_lr.load.return_value = (np.zeros(22050 * 200), 22050)
        mock_lr.feature.rms.return_value = np.array([np.zeros(500)])
        mock_lr.onset.onset_strength.return_value = np.zeros(500)
        mock_lr.feature.chroma_cqt.return_value = np.zeros((12, 500))
        mock_lr.times_like.return_value = np.linspace(0, 200, 500)
        mock_lr.util.peak_pick.return_value = np.array([], dtype=int)

        cues = detect_cues("/fake/track.mp3", bpm=128.0)

    types = [c["type"] for c in cues]
    assert "drop" in types


def test_detect_cues_snaps_to_nearest_downbeat():
    from multidj.cues import detect_cues
    import numpy as np

    downbeats = [0.0, 2.0, 4.0, 28.0, 30.0, 32.0, 90.0, 150.0]
    segs = [
        MagicMock(start=0.0,   end=30.7,  label="intro"),
        MagicMock(start=30.7,  end=90.2,  label="verse"),
        MagicMock(start=90.2,  end=150.1, label="chorus"),
        MagicMock(start=150.1, end=180.0, label="outro"),
    ]
    allin1_result = _make_allin1_result(segments=segs, downbeats=downbeats)

    with (
        patch("multidj.cues.allin1") as mock_a1,
        patch("multidj.cues.librosa") as mock_lr,
        patch("multidj.cues.np", np),
    ):
        mock_a1.analyze.return_value = allin1_result
        mock_lr.load.return_value = (np.zeros(22050 * 200), 22050)
        mock_lr.feature.rms.return_value = np.array([np.zeros(500)])
        mock_lr.onset.onset_strength.return_value = np.zeros(500)
        mock_lr.feature.chroma_cqt.return_value = np.zeros((12, 500))
        mock_lr.times_like.return_value = np.linspace(0, 200, 500)
        mock_lr.util.peak_pick.return_value = np.array([], dtype=int)

        cues = detect_cues("/fake/track.mp3", bpm=128.0)

    verse_cue = next(c for c in cues if c["type"] == "verse")
    assert verse_cue["position"] == pytest.approx(30.0)


def test_detect_cues_raises_on_missing_dep():
    import sys
    import importlib
    saved = sys.modules.pop("allin1", None)
    try:
        import multidj.cues as cues_mod
        importlib.reload(cues_mod)
        with pytest.raises(RuntimeError, match="embeddings"):
            cues_mod.detect_cues("/fake/track.mp3", bpm=128.0)
    finally:
        if saved is not None:
            sys.modules["allin1"] = saved
