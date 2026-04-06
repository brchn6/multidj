from __future__ import annotations
import sqlite3
import pytest
from unittest.mock import patch
from tests.fixtures.multidj_factory import make_multidj_db


def test_analyze_bpm_dry_run_lists_candidates(multidj_db):
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET bpm = 0 WHERE id = 1")
    conn.commit()
    conn.close()

    from multidj.analyze import analyze_bpm
    result = analyze_bpm(str(multidj_db), apply=False)

    assert result["mode"] == "dry_run"
    assert result["total_candidates"] >= 1
    conn2 = sqlite3.connect(str(multidj_db))
    row = conn2.execute("SELECT bpm FROM tracks WHERE id = 1").fetchone()
    conn2.close()
    assert row[0] == 0.0


def test_analyze_bpm_apply_writes_detected_bpm(multidj_db, tmp_path):
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET bpm = 0 WHERE id = 1")
    conn.commit()
    conn.close()

    with patch("multidj.analyze.detect_bpm", return_value=128.0):
        from multidj.analyze import analyze_bpm
        result = analyze_bpm(
            str(multidj_db), apply=True, backup_dir=str(tmp_path)
        )

    assert result["mode"] == "apply"
    assert result["succeeded"] >= 1
    conn2 = sqlite3.connect(str(multidj_db))
    row = conn2.execute("SELECT bpm FROM tracks WHERE id = 1").fetchone()
    conn2.close()
    assert row[0] == pytest.approx(128.0)


def test_analyze_bpm_skips_tracks_with_bpm(multidj_db, tmp_path):
    with patch("multidj.analyze.detect_bpm", return_value=99.0):
        from multidj.analyze import analyze_bpm
        result = analyze_bpm(
            str(multidj_db), apply=True, backup_dir=str(tmp_path)
        )
    assert result["total_candidates"] == 0


def test_analyze_bpm_force_reanalyzes_all(multidj_db, tmp_path):
    with patch("multidj.analyze.detect_bpm", return_value=130.0):
        from multidj.analyze import analyze_bpm
        result = analyze_bpm(
            str(multidj_db), apply=True, force=True, backup_dir=str(tmp_path)
        )
    assert result["total_candidates"] == 9  # 9 active tracks in fixture


def test_analyze_bpm_isolates_errors(multidj_db, tmp_path):
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET bpm = 0")
    conn.commit()
    conn.close()

    call_count = [0]
    def flaky_detect(filepath):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("bad audio file")
        return 120.0

    with patch("multidj.analyze.detect_bpm", side_effect=flaky_detect):
        from multidj.analyze import analyze_bpm
        result = analyze_bpm(
            str(multidj_db), apply=True, backup_dir=str(tmp_path)
        )

    assert result["errors"] == 1
    assert result["succeeded"] >= 1
