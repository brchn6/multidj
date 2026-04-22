from __future__ import annotations
import pytest
from unittest.mock import patch
from tests.fixtures.multidj_factory import make_multidj_db
from multidj.analyze import analyze_energy


@pytest.fixture()
def db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


def _mock_detect(path: str) -> float:
    return 0.5


def test_dry_run_returns_candidate_count(db):
    result = analyze_energy(db_path=str(db), apply=False)
    assert result["mode"] == "dry_run"
    assert result["total_candidates"] > 0
    assert result["processed"] == 0


def test_dry_run_does_not_write(db):
    import sqlite3
    analyze_energy(db_path=str(db), apply=False)
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM tracks WHERE energy IS NOT NULL AND deleted=0").fetchone()[0]
    conn.close()
    assert count == 0


def test_apply_writes_energy(db, tmp_path):
    import sqlite3
    with patch("multidj.analyze.detect_energy", side_effect=_mock_detect):
        result = analyze_energy(db_path=str(db), apply=True, backup_dir=str(tmp_path))
    assert result["succeeded"] > 0
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM tracks WHERE energy IS NOT NULL AND deleted=0").fetchone()[0]
    conn.close()
    assert count == result["succeeded"]


def test_force_reprocesses_existing(db, tmp_path):
    import sqlite3
    with patch("multidj.analyze.detect_energy", side_effect=_mock_detect):
        analyze_energy(db_path=str(db), apply=True, backup_dir=str(tmp_path))
    result2 = analyze_energy(db_path=str(db), apply=False)
    assert result2["total_candidates"] == 0
    result3 = analyze_energy(db_path=str(db), apply=False, force=True)
    assert result3["total_candidates"] > 0


def test_per_track_error_isolation(db, tmp_path):
    import sqlite3
    call_count = 0

    def flaky(path: str) -> float:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("bad file")
        return 0.5

    with patch("multidj.analyze.detect_energy", side_effect=flaky):
        result = analyze_energy(db_path=str(db), apply=True, backup_dir=str(tmp_path))

    assert result["errors"] >= 1
    assert result["succeeded"] >= 1


def test_limit_restricts_processed(db, tmp_path):
    with patch("multidj.analyze.detect_energy", side_effect=_mock_detect):
        result = analyze_energy(db_path=str(db), apply=True, limit=1, backup_dir=str(tmp_path))
    assert result["processed"] == 1
