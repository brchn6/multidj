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


# ---------------------------------------------------------------------------
# analyze_cues — integration tests (allin1 + librosa mocked)
# ---------------------------------------------------------------------------

_MOCK_DETECT_RETURN = [
    {"type": "intro",  "position": 0.0,   "confidence": "high", "label": "intro"},
    {"type": "verse",  "position": 30.0,  "confidence": "high", "label": "verse"},
    {"type": "chorus", "position": 90.0,  "confidence": "high", "label": "chorus"},
    {"type": "drop",   "position": 90.0,  "confidence": "high", "label": "Drop (chorus)"},
    {"type": "outro",  "position": 180.0, "confidence": "high", "label": "outro"},
]


def test_analyze_cues_dry_run_does_not_write(db):
    from multidj.cues import analyze_cues

    result = analyze_cues(db_path=str(db), apply=False)

    assert result["mode"] == "dry_run"
    assert result["total_candidates"] == 9
    assert result["processed"] == 0

    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM cue_points").fetchone()[0]
    conn.close()
    assert count == 0


def test_analyze_cues_apply_stores_cue_points(db, tmp_path):
    from multidj.cues import analyze_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        result = analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    assert result["mode"] == "apply"
    assert result["succeeded"] == 9
    assert result["failed"] == 0

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT type, position, confidence, source FROM cue_points WHERE track_id=1 ORDER BY position"
    ).fetchall()
    conn.close()

    types = {r[0] for r in rows}
    assert "intro" in types
    assert "drop" in types
    assert "outro" in types
    assert all(r[2] == "high" for r in rows)
    assert all(r[3] == "auto" for r in rows)


def test_analyze_cues_apply_updates_tracks_shortcuts(db, tmp_path):
    from multidj.cues import analyze_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT intro_end, outro_start FROM tracks WHERE id=1"
    ).fetchone()
    conn.close()

    # intro_end = position of first non-intro cue = 30.0 (verse)
    assert row[0] == pytest.approx(30.0)
    # outro_start = position of outro cue = 180.0
    assert row[1] == pytest.approx(180.0)


def test_analyze_cues_skips_tracks_with_existing_cues(db, tmp_path):
    from multidj.cues import analyze_cues

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO cue_points (track_id, type, position, confidence, source) VALUES (1, 'intro', 0.0, 'high', 'auto')"
    )
    conn.commit()
    conn.close()

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        result = analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    assert result["total_candidates"] == 8


def test_analyze_cues_force_reanalyzes_existing(db, tmp_path):
    from multidj.cues import analyze_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    result = analyze_cues(db_path=str(db), apply=False)
    assert result["total_candidates"] == 0

    result2 = analyze_cues(db_path=str(db), apply=False, force=True)
    assert result2["total_candidates"] == 9


def test_analyze_cues_limit(db, tmp_path):
    from multidj.cues import analyze_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        result = analyze_cues(db_path=str(db), apply=True, limit=3, backup_dir=str(tmp_path))

    assert result["processed"] == 3
    assert result["succeeded"] == 3


def test_analyze_cues_per_track_error_isolation(db, tmp_path):
    from multidj.cues import analyze_cues

    call_count = [0]

    def flaky(path, bpm):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("bad audio file")
        return _MOCK_DETECT_RETURN

    with patch("multidj.cues.detect_cues", side_effect=flaky):
        result = analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    assert result["failed"] == 1
    assert result["succeeded"] == 8
    assert len(result["errors"]) == 1


def test_clear_cues_dry_run(db, tmp_path):
    from multidj.cues import analyze_cues, clear_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    result = clear_cues(db_path=str(db), apply=False)
    assert result["mode"] == "dry_run"
    assert result["would_remove"] > 0

    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM cue_points").fetchone()[0]
    conn.close()
    assert count > 0


def test_clear_cues_apply_removes_auto_cues(db, tmp_path):
    from multidj.cues import analyze_cues, clear_cues

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    result = clear_cues(db_path=str(db), apply=True)
    assert result["mode"] == "apply"
    assert result["removed"] > 0

    conn = sqlite3.connect(str(db))
    count = conn.execute(
        "SELECT COUNT(*) FROM cue_points WHERE source='auto'"
    ).fetchone()[0]
    intro_end = conn.execute("SELECT intro_end FROM tracks WHERE id=1").fetchone()[0]
    outro_start = conn.execute("SELECT outro_start FROM tracks WHERE id=1").fetchone()[0]
    conn.close()

    assert count == 0
    assert intro_end is None
    assert outro_start is None


def test_clear_cues_preserves_manual_cues(db, tmp_path):
    from multidj.cues import analyze_cues, clear_cues

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO cue_points (track_id, type, position, confidence, source) "
        "VALUES (1, 'hot_cue', 42.0, 'high', 'manual')"
    )
    conn.commit()
    conn.close()

    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        analyze_cues(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    clear_cues(db_path=str(db), apply=True)

    conn = sqlite3.connect(str(db))
    manual_count = conn.execute(
        "SELECT COUNT(*) FROM cue_points WHERE source='manual'"
    ).fetchone()[0]
    conn.close()
    assert manual_count == 1
