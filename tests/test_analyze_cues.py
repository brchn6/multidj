from __future__ import annotations
import sqlite3
import pytest
from unittest.mock import patch


_MOCK_CUES = {"intro_end": 12.5, "outro_start": 187.3}


def test_dry_run_returns_candidates(multidj_db):
    # All 9 active tracks have no cues — all are candidates
    from multidj.analyze import analyze_cues
    result = analyze_cues(str(multidj_db), apply=False)

    assert result["mode"] == "dry_run"
    assert result["total_candidates"] == 9
    assert result["processed"] == 0

    # No writes
    conn = sqlite3.connect(str(multidj_db))
    count = conn.execute("SELECT COUNT(*) FROM cue_points").fetchone()[0]
    conn.close()
    assert count == 0


def test_apply_writes_to_tracks(multidj_db, tmp_path):
    with patch("multidj.analyze.detect_cues", return_value=_MOCK_CUES):
        from multidj.analyze import analyze_cues
        result = analyze_cues(str(multidj_db), apply=True, backup_dir=str(tmp_path))

    assert result["mode"] == "apply"
    assert result["succeeded"] == 9

    conn = sqlite3.connect(str(multidj_db))
    row = conn.execute("SELECT intro_end, outro_start FROM tracks WHERE id = 1").fetchone()
    conn.close()
    assert row[0] == pytest.approx(12.5)
    assert row[1] == pytest.approx(187.3)


def test_apply_writes_to_cue_points(multidj_db, tmp_path):
    with patch("multidj.analyze.detect_cues", return_value=_MOCK_CUES):
        from multidj.analyze import analyze_cues
        analyze_cues(str(multidj_db), apply=True, backup_dir=str(tmp_path))

    conn = sqlite3.connect(str(multidj_db))
    rows = conn.execute(
        "SELECT type, position FROM cue_points WHERE track_id = 1 ORDER BY type"
    ).fetchall()
    conn.close()

    types = {r[0] for r in rows}
    assert "intro_end" in types
    assert "outro_start" in types
    positions = {r[0]: r[1] for r in rows}
    assert positions["intro_end"] == pytest.approx(12.5)
    assert positions["outro_start"] == pytest.approx(187.3)


def test_skips_tracks_with_existing_cues(multidj_db, tmp_path):
    # Pre-set cues on track 1
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET intro_end = 5.0, outro_start = 100.0 WHERE id = 1")
    conn.commit()
    conn.close()

    with patch("multidj.analyze.detect_cues", return_value=_MOCK_CUES):
        from multidj.analyze import analyze_cues
        result = analyze_cues(str(multidj_db), apply=True, backup_dir=str(tmp_path))

    assert result["total_candidates"] == 8  # track 1 already has cues


def test_force_includes_already_analyzed(multidj_db, tmp_path):
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET intro_end = 5.0, outro_start = 100.0")
    conn.commit()
    conn.close()

    with patch("multidj.analyze.detect_cues", return_value=_MOCK_CUES):
        from multidj.analyze import analyze_cues
        result = analyze_cues(str(multidj_db), apply=True, force=True, backup_dir=str(tmp_path))

    assert result["total_candidates"] == 9  # all 9 active tracks


def test_limit(multidj_db, tmp_path):
    with patch("multidj.analyze.detect_cues", return_value=_MOCK_CUES):
        from multidj.analyze import analyze_cues
        result = analyze_cues(str(multidj_db), apply=True, limit=3, backup_dir=str(tmp_path))

    assert result["processed"] == 3
    assert result["succeeded"] == 3


def test_per_track_error_isolation(multidj_db, tmp_path):
    call_count = [0]

    def flaky_detect(filepath):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("bad audio file")
        return _MOCK_CUES

    with patch("multidj.analyze.detect_cues", side_effect=flaky_detect):
        from multidj.analyze import analyze_cues
        result = analyze_cues(str(multidj_db), apply=True, backup_dir=str(tmp_path))

    assert result["errors"] == 1
    assert result["succeeded"] == 8
    assert len(result["error_details"]) == 1
