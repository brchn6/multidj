from __future__ import annotations
import sqlite3
import pytest
from pathlib import Path
from tests.fixtures.multidj_factory import make_multidj_db
from tests.fixtures.mixxx_factory import make_mixxx_db
from multidj.adapters.mixxx import MixxxAdapter


@pytest.fixture()
def multidj_db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


@pytest.fixture()
def mixxx_db(tmp_path):
    return make_mixxx_db(tmp_path / "mixxxdb.sqlite")


def test_crates_pushed_to_mixxx(multidj_db, mixxx_db, tmp_path):
    """After full_sync, crates from MultiDJ appear in Mixxx."""
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("INSERT INTO crates (name, type, show) VALUES ('Genre: Drum and Bass', 'auto', 1)")
    crate_id = conn.execute("SELECT id FROM crates WHERE name='Genre: Drum and Bass'").fetchone()[0]
    track_id = conn.execute("SELECT id FROM tracks WHERE deleted=0 LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO crate_tracks (crate_id, track_id) VALUES (?, ?)", (crate_id, track_id))
    conn.commit()
    conn.close()

    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    mx_conn = sqlite3.connect(str(mixxx_db))
    crate = mx_conn.execute("SELECT id FROM crates WHERE name='Genre: Drum and Bass'").fetchone()
    mx_conn.close()
    assert crate is not None


def test_stale_tracks_removed_from_crate(multidj_db, mixxx_db, tmp_path):
    """Tracks removed from a MultiDJ crate are removed from the Mixxx crate on sync."""
    mdj = sqlite3.connect(str(multidj_db))
    mdj.execute("INSERT INTO crates (name, type, show) VALUES ('Genre: Techno', 'auto', 1)")
    crate_id = mdj.execute("SELECT id FROM crates WHERE name='Genre: Techno'").fetchone()[0]
    track_id = mdj.execute("SELECT id FROM tracks WHERE deleted=0 LIMIT 1").fetchone()[0]
    mdj.execute("INSERT INTO crate_tracks (crate_id, track_id) VALUES (?, ?)", (crate_id, track_id))
    mdj.commit()

    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    # Remove the track from the crate in MultiDJ
    mdj.execute("DELETE FROM crate_tracks WHERE crate_id=?", (crate_id,))
    mdj.commit()
    mdj.close()

    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    mx = sqlite3.connect(str(mixxx_db))
    mx_crate_id = mx.execute("SELECT id FROM crates WHERE name='Genre: Techno'").fetchone()[0]
    members = mx.execute("SELECT COUNT(*) FROM crate_tracks WHERE crate_id=?", (mx_crate_id,)).fetchone()[0]
    mx.close()
    assert members == 0


def test_deleted_auto_crate_removed_from_mixxx(multidj_db, mixxx_db, tmp_path):
    """Auto-crates deleted from MultiDJ are removed from Mixxx on next sync."""
    mdj = sqlite3.connect(str(multidj_db))
    mdj.execute("INSERT INTO crates (name, type, show) VALUES ('BPM: 125-130', 'auto', 1)")
    crate_id = mdj.execute("SELECT id FROM crates WHERE name='BPM: 125-130'").fetchone()[0]
    mdj.commit()

    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    mx = sqlite3.connect(str(mixxx_db))
    assert mx.execute("SELECT id FROM crates WHERE name='BPM: 125-130'").fetchone() is not None
    mx.close()

    # Delete from MultiDJ
    mdj.execute("DELETE FROM crate_tracks WHERE crate_id=?", (crate_id,))
    mdj.execute("DELETE FROM crates WHERE id=?", (crate_id,))
    mdj.commit()
    mdj.close()

    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    mx = sqlite3.connect(str(mixxx_db))
    gone = mx.execute("SELECT id FROM crates WHERE name='BPM: 125-130'").fetchone()
    mx.close()
    assert gone is None


def test_hand_curated_mixxx_crates_not_deleted(multidj_db, mixxx_db, tmp_path):
    """Crates created directly in Mixxx with non-auto names are left alone."""
    mx = sqlite3.connect(str(mixxx_db))
    mx.execute("INSERT INTO crates (name, show) VALUES ('My Favourites', 1)")
    mx.commit()
    mx.close()

    adapter = MixxxAdapter(mixxx_db_path=mixxx_db)
    adapter.full_sync(multidj_db_path=multidj_db, apply=True)

    mx = sqlite3.connect(str(mixxx_db))
    still_there = mx.execute("SELECT id FROM crates WHERE name='My Favourites'").fetchone()
    mx.close()
    assert still_there is not None
