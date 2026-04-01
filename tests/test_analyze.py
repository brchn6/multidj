import sqlite3
import pytest
from unittest.mock import patch

try:
    from multidj.analyze import analyze_key
except ImportError:
    pytest.skip("librosa not installed", allow_module_level=True)


def test_analyze_dry_run_returns_candidates(multidj_db):
    """dry-run lists tracks missing key without loading audio"""
    result = analyze_key(str(multidj_db), apply=False)
    assert result["mode"] == "dry_run"
    # Tracks 2,3,5,7,9 have no key (5 tracks)
    assert result["total_candidates"] == 5


def test_analyze_dry_run_no_write(multidj_db, multidj_db_conn):
    result = analyze_key(str(multidj_db), apply=False)
    # No keys should be written
    changed = multidj_db_conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE id IN (2,3,5,7,9) AND key IS NOT NULL"
    ).fetchone()[0]
    assert changed == 0


def test_analyze_force_includes_keyed(multidj_db):
    """--force should include tracks that already have a key"""
    result = analyze_key(str(multidj_db), apply=False, force=True)
    # All 9 active tracks should be candidates
    assert result["total_candidates"] == 9


def test_analyze_apply_writes_key(multidj_db, multidj_db_conn):
    """mocked detect_key result written to tracks.key"""
    with patch("multidj.analyze.detect_key", return_value="11B"):
        result = analyze_key(str(multidj_db), apply=True, limit=1)
    # At least one track should now have key="11B"
    count = multidj_db_conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE key='11B'"
    ).fetchone()[0]
    assert count >= 1


def test_analyze_limit(multidj_db):
    """--limit caps the number of candidates processed"""
    result = analyze_key(str(multidj_db), apply=False, limit=2)
    assert result["processed"] == 2


def test_analyze_mode_field(multidj_db):
    r = analyze_key(str(multidj_db), apply=False)
    assert r["mode"] == "dry_run"
