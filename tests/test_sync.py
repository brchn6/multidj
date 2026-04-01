"""
Tests for multidj sync mixxx — push dirty MultiDJ tracks to Mixxx.
"""
from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path
from tests.fixtures.mixxx_factory import make_mixxx_db
from tests.fixtures.multidj_factory import make_multidj_db
from multidj.adapters.mixxx import MixxxAdapter


@pytest.fixture
def both_dbs(tmp_path):
    """Returns (multidj_path, mixxx_path) — both pre-populated"""
    multidj_path = make_multidj_db(tmp_path / "library.sqlite")
    mixxx_path = make_mixxx_db(tmp_path / "mixxxdb.sqlite")
    return multidj_path, mixxx_path


def test_sync_dry_run_returns_summary(both_dbs):
    multidj_path, mixxx_path = both_dbs
    adapter = MixxxAdapter(mixxx_db_path=mixxx_path)
    # Make track 1 dirty first
    conn = sqlite3.connect(str(multidj_path))
    conn.execute("UPDATE sync_state SET dirty=1 WHERE track_id=1 AND adapter='mixxx'")
    conn.commit()
    conn.close()
    result = adapter.full_sync(multidj_path, apply=False)
    assert result["mode"] == "dry_run"
    assert result["dirty_tracks"] >= 1


def test_sync_dry_run_no_write(both_dbs):
    multidj_path, mixxx_path = both_dbs
    adapter = MixxxAdapter(mixxx_db_path=mixxx_path)
    conn = sqlite3.connect(str(multidj_path))
    conn.execute("UPDATE sync_state SET dirty=1 WHERE track_id=1 AND adapter='mixxx'")
    conn.commit()
    # Get Mixxx state before
    mixxx_conn = sqlite3.connect(str(mixxx_path))
    before = mixxx_conn.execute("SELECT artist FROM library WHERE id=1").fetchone()[0]
    mixxx_conn.close()
    # Modify artist in MultiDJ but dry-run sync
    conn.execute("UPDATE tracks SET artist='Modified Artist' WHERE id=1")
    conn.commit()
    conn.close()
    adapter.full_sync(multidj_path, apply=False)
    # Mixxx should be unchanged
    mixxx_conn = sqlite3.connect(str(mixxx_path))
    after = mixxx_conn.execute("SELECT artist FROM library WHERE id=1").fetchone()[0]
    mixxx_conn.close()
    assert before == after  # not "Modified Artist"


def test_sync_apply_pushes_dirty(both_dbs, tmp_path):
    multidj_path, mixxx_path = both_dbs
    # Modify track 1 in MultiDJ and make it dirty
    conn = sqlite3.connect(str(multidj_path))
    conn.execute("UPDATE tracks SET artist='New Artist Name' WHERE id=1")
    conn.execute("UPDATE sync_state SET dirty=1 WHERE track_id=1 AND adapter='mixxx'")
    conn.commit()
    conn.close()
    adapter = MixxxAdapter(mixxx_db_path=mixxx_path)
    result = adapter.full_sync(multidj_path, apply=True)
    assert result["pushed"] >= 1
    # Verify Mixxx was updated
    mixxx_conn = sqlite3.connect(str(mixxx_path))
    row = mixxx_conn.execute(
        "SELECT l.artist FROM library l "
        "JOIN track_locations tl ON l.location=tl.id "
        "WHERE tl.location='/music/fixture/01_DJ_Tiesto_-_Red_Lights.mp3'"
    ).fetchone()
    mixxx_conn.close()
    assert row[0] == "New Artist Name"


def test_sync_marks_clean_after_push(both_dbs):
    multidj_path, mixxx_path = both_dbs
    conn = sqlite3.connect(str(multidj_path))
    conn.execute("UPDATE sync_state SET dirty=1 WHERE track_id=1 AND adapter='mixxx'")
    conn.commit()
    conn.close()
    adapter = MixxxAdapter(mixxx_db_path=mixxx_path)
    adapter.full_sync(multidj_path, apply=True)
    conn = sqlite3.connect(str(multidj_path))
    row = conn.execute(
        "SELECT dirty FROM sync_state WHERE track_id=1 AND adapter='mixxx'"
    ).fetchone()
    conn.close()
    assert row[0] == 0


def test_sync_skips_clean_tracks(both_dbs):
    multidj_path, mixxx_path = both_dbs
    # All tracks are dirty=0 in fixture (post-import state)
    adapter = MixxxAdapter(mixxx_db_path=mixxx_path)
    result = adapter.full_sync(multidj_path, apply=True)
    # No dirty tracks → nothing pushed
    assert result.get("pushed", 0) == 0
    assert result.get("total_dirty", 0) == 0


def test_dirty_trigger_fires(both_dbs):
    """UPDATE tracks → sync_state.dirty becomes 1"""
    multidj_path, _ = both_dbs
    conn = sqlite3.connect(str(multidj_path))
    # Confirm dirty=0 initially
    before = conn.execute(
        "SELECT dirty FROM sync_state WHERE track_id=1 AND adapter='mixxx'"
    ).fetchone()[0]
    assert before == 0
    # Update the track — trigger should fire
    conn.execute("UPDATE tracks SET genre='NewGenre' WHERE id=1")
    conn.commit()
    after = conn.execute(
        "SELECT dirty FROM sync_state WHERE track_id=1 AND adapter='mixxx'"
    ).fetchone()[0]
    conn.close()
    assert after == 1
