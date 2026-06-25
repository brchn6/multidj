from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from tests.fixtures.mixxx_factory import make_mixxx_db
from tests.fixtures.multidj_factory import make_multidj_db


@pytest.fixture()
def multidj_db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


@pytest.fixture()
def mixxx_db(tmp_path):
    return make_mixxx_db(tmp_path / "mixxxdb.sqlite")


_MOCK_DETECT_RETURN = [
    {"type": "intro",  "position": 0.0,   "confidence": "high", "label": "intro"},
    {"type": "chorus", "position": 90.0,  "confidence": "high", "label": "chorus"},
    {"type": "drop",   "position": 90.0,  "confidence": "high", "label": "Drop (chorus)"},
    {"type": "outro",  "position": 180.0, "confidence": "high", "label": "outro"},
]


def _populate_cues(multidj_db, tmp_path):
    from multidj.cues import analyze_cues
    with patch("multidj.cues.detect_cues", return_value=_MOCK_DETECT_RETURN):
        analyze_cues(db_path=str(multidj_db), apply=True, backup_dir=str(tmp_path))


def test_sync_pushes_intro_drop_outro_to_mixxx(multidj_db, mixxx_db, tmp_path):
    _populate_cues(multidj_db, tmp_path)

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=str(mixxx_db))
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    conn = sqlite3.connect(str(mixxx_db))
    cue_rows = conn.execute("SELECT label FROM cues ORDER BY label").fetchall()
    conn.close()

    labels = {r[0] for r in cue_rows}
    assert "Drop" in labels
    assert "Intro" in labels
    assert "Outro" in labels


def test_sync_does_not_push_low_confidence_cues(multidj_db, mixxx_db, tmp_path):
    low_conf = [
        {"type": "intro",  "position": 0.0,   "confidence": "low",  "label": "intro"},
        {"type": "drop",   "position": 90.0,  "confidence": "low",  "label": "Drop (chorus)"},
        {"type": "outro",  "position": 180.0, "confidence": "low",  "label": "outro"},
    ]
    from multidj.cues import analyze_cues
    with patch("multidj.cues.detect_cues", return_value=low_conf):
        analyze_cues(db_path=str(multidj_db), apply=True, backup_dir=str(tmp_path))

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=str(mixxx_db))
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    conn = sqlite3.connect(str(mixxx_db))
    cue_count = conn.execute("SELECT COUNT(*) FROM cues").fetchone()[0]
    conn.close()

    assert cue_count == 0


def test_sync_cue_positions_in_sample_frames(multidj_db, mixxx_db, tmp_path):
    _populate_cues(multidj_db, tmp_path)

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=str(mixxx_db))
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    conn = sqlite3.connect(str(mixxx_db))
    drop_row = conn.execute("SELECT position FROM cues WHERE label='Drop'").fetchone()
    conn.close()

    # Drop at 90.0s → 90.0 * 44100 = 3_969_000 frames
    assert drop_row is not None
    assert drop_row[0] == pytest.approx(90.0 * 44100, rel=0.01)


def test_sync_never_deletes_mixxx_hot_cues(multidj_db, mixxx_db, tmp_path):
    """MultiDJ never deletes hot cues from Mixxx — even after clear_cues.

    MultiDJ self-annotates intro/drop/outro into Mixxx but treats Mixxx hot
    cues as write-only: it pushes/replaces the slots it manages and never wipes
    the Mixxx cues table. Clearing cues in the MultiDJ DB (`cues clear`) removes
    them from MultiDJ only; the cues already written into Mixxx persist so the
    DJ's prepped hot cues (and any manual ones) are never destroyed.

    The old behavior — a global `DELETE FROM cues WHERE hotcue IN (0,1,2)` that
    reconciled stale slots on every sync — was removed per the auto-cues design
    (never destroy work in Mixxx).
    """
    _populate_cues(multidj_db, tmp_path)

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=str(mixxx_db))
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    conn = sqlite3.connect(str(mixxx_db))
    pushed = conn.execute(
        "SELECT COUNT(*) FROM cues WHERE label IN ('Intro','Drop','Outro')"
    ).fetchone()[0]
    conn.close()
    assert pushed > 0  # intro/drop/outro were annotated into Mixxx

    from multidj.cues import clear_cues
    clear_cues(db_path=str(multidj_db), apply=True)

    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    conn = sqlite3.connect(str(mixxx_db))
    remaining = conn.execute(
        "SELECT COUNT(*) FROM cues WHERE label IN ('Intro','Drop','Outro')"
    ).fetchone()[0]
    conn.close()

    # Clearing MultiDJ cues must NOT remove the cues already written to Mixxx.
    assert remaining == pushed
