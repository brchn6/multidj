"""Tests for multidj import mixxx command.

Requires the fixtures built by Sub-agent A:
  tests/fixtures/mixxx_factory.py  — make_mixxx_db(path) -> Path
  tests/fixtures/data.py           — TRACKS list (9 active + 1 deleted = 10 total)
"""
import sqlite3

import pytest
from pathlib import Path

from tests.fixtures.mixxx_factory import make_mixxx_db
from tests.fixtures.data import TRACKS
from multidj.adapters.mixxx import MixxxAdapter


@pytest.fixture
def mixxx_db(tmp_path):
    return make_mixxx_db(tmp_path / "mixxxdb.sqlite")


@pytest.fixture
def multidj_db_path(tmp_path):
    return tmp_path / "library.sqlite"


# ---------------------------------------------------------------------------
# Dry-run tests
# ---------------------------------------------------------------------------

def test_import_dry_run_returns_summary(mixxx_db, multidj_db_path):
    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    result = adapter.import_all(multidj_db_path, apply=False)
    assert result["mode"] == "dry_run"
    assert result["total_tracks"] == 9  # excludes mixxx_deleted=1


def test_import_dry_run_does_not_create_db(mixxx_db, multidj_db_path):
    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    adapter.import_all(multidj_db_path, apply=False)
    # DB should not be created in dry-run mode
    # (or if it is created by migration runner, tracks table should be empty)
    if multidj_db_path.exists():
        conn = sqlite3.connect(str(multidj_db_path))
        count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        conn.close()
        assert count == 0


def test_import_dry_run_returns_sample(mixxx_db, multidj_db_path):
    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    result = adapter.import_all(multidj_db_path, apply=False)
    assert "sample" in result
    assert isinstance(result["sample"], list)
    assert len(result["sample"]) <= 5


# ---------------------------------------------------------------------------
# Apply tests
# ---------------------------------------------------------------------------

def test_import_apply_track_count(mixxx_db, multidj_db_path):
    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    result = adapter.import_all(multidj_db_path, apply=True)
    assert result["new_tracks"] == 9
    conn = sqlite3.connect(str(multidj_db_path))
    count = conn.execute("SELECT COUNT(*) FROM tracks WHERE deleted=0").fetchone()[0]
    conn.close()
    assert count == 9


def test_import_excludes_deleted(mixxx_db, multidj_db_path):
    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    adapter.import_all(multidj_db_path, apply=True)
    conn = sqlite3.connect(str(multidj_db_path))
    row = conn.execute(
        "SELECT id FROM tracks WHERE path='/music/fixture/10_deleted.mp3'"
    ).fetchone()
    conn.close()
    assert row is None


def test_import_field_mapping(mixxx_db, multidj_db_path):
    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    adapter.import_all(multidj_db_path, apply=True)
    conn = sqlite3.connect(str(multidj_db_path))
    conn.row_factory = sqlite3.Row
    track = conn.execute(
        "SELECT * FROM tracks WHERE path='/music/fixture/01_DJ_Tiesto_-_Red_Lights.mp3'"
    ).fetchone()
    conn.close()
    assert track["artist"] == "DJ Tiesto"
    assert track["title"] == "Red Lights"
    assert track["bpm"] == 128.0
    assert track["play_count"] == 12   # timesplayed → play_count
    assert track["rating"] == 4
    assert track["key"] == "8B"


def test_import_sync_state(mixxx_db, multidj_db_path):
    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    adapter.import_all(multidj_db_path, apply=True)
    conn = sqlite3.connect(str(multidj_db_path))
    rows = conn.execute(
        "SELECT * FROM sync_state WHERE adapter='mixxx'"
    ).fetchall()
    dirty_rows = conn.execute(
        "SELECT * FROM sync_state WHERE adapter='mixxx' AND dirty=1"
    ).fetchall()
    conn.close()
    assert len(rows) == 9
    assert len(dirty_rows) == 0  # all imported tracks are in-sync (dirty=0)


def test_import_idempotent(mixxx_db, multidj_db_path):
    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    adapter.import_all(multidj_db_path, apply=True)
    result2 = adapter.import_all(multidj_db_path, apply=True)
    conn = sqlite3.connect(str(multidj_db_path))
    count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    conn.close()
    assert count == 9           # no duplicates on second run
    assert result2["new_tracks"] == 0  # nothing new on second run


def test_import_key_lookup(mixxx_db, multidj_db_path):
    """Mixxx key_id -> Camelot string via keys table."""
    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    adapter.import_all(multidj_db_path, apply=True)
    conn = sqlite3.connect(str(multidj_db_path))
    conn.row_factory = sqlite3.Row
    track = conn.execute(
        "SELECT key FROM tracks WHERE path='/music/fixture/06_Carl_Cox_-_Pressure.mp3'"
    ).fetchone()
    conn.close()
    assert track["key"] == "9A"


def test_import_errors_listed(mixxx_db, multidj_db_path):
    """Result dict must always contain an errors key (empty list on clean import)."""
    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    result = adapter.import_all(multidj_db_path, apply=True)
    assert "errors" in result
    assert isinstance(result["errors"], list)
    assert result["errors"] == []  # clean fixture should have no errors


def test_import_result_mode_apply(mixxx_db, multidj_db_path):
    """Apply result must report mode='apply' and include all count keys."""
    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    result = adapter.import_all(multidj_db_path, apply=True)
    assert result["mode"] == "apply"
    for key in ("total_tracks", "new_tracks", "updated_tracks", "unchanged_tracks", "errors"):
        assert key in result


def test_import_updated_tracks_on_change(mixxx_db, multidj_db_path):
    """Second import after an in-place Mixxx data change counts as updated, not new."""
    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    adapter.import_all(multidj_db_path, apply=True)

    # Simulate a metadata change in the Mixxx DB (e.g. rating bump)
    mixxx_conn = sqlite3.connect(str(mixxx_db))
    mixxx_conn.execute(
        "UPDATE library SET rating = 5 WHERE artist = 'DJ Tiesto' AND mixxx_deleted = 0"
    )
    mixxx_conn.commit()
    mixxx_conn.close()

    result2 = adapter.import_all(multidj_db_path, apply=True)
    assert result2["updated_tracks"] >= 1
    assert result2["new_tracks"] == 0
