from __future__ import annotations
import sqlite3
import pytest
from pathlib import Path
from tests.fixtures.multidj_factory import make_multidj_db
from tests.fixtures.mixxx_factory import make_mixxx_db


def _add_crate_to_multidj(conn, name: str, track_ids: list[int], crate_type: str = "auto"):
    conn.execute(
        "INSERT OR IGNORE INTO crates (name, type, show) VALUES (?, ?, 1)", (name, crate_type)
    )
    crate_id = conn.execute(
        "SELECT id FROM crates WHERE name = ?", (name,)
    ).fetchone()[0]
    for tid in track_ids:
        conn.execute(
            "INSERT OR IGNORE INTO crate_tracks (crate_id, track_id) VALUES (?, ?)",
            (crate_id, tid),
        )
    conn.commit()
    return crate_id


def test_push_crates_creates_crates_in_mixxx(tmp_path):
    """full_sync with apply pushes MultiDJ crates into Mixxx DB."""
    mdj_db = make_multidj_db(tmp_path / "library.sqlite")
    mxdb = make_mixxx_db(tmp_path / "mixxxdb.sqlite")

    conn = sqlite3.connect(str(mdj_db))
    _add_crate_to_multidj(conn, "Genre: House", [1, 4])
    conn.close()

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=mxdb)
    result = adapter.full_sync(mdj_db, apply=True)

    mx_conn = sqlite3.connect(str(mxdb))
    crates = mx_conn.execute("SELECT name FROM crates WHERE name = 'Genre: House'").fetchall()
    mx_conn.close()

    assert len(crates) == 1


def test_push_crates_dry_run_no_write(tmp_path):
    """Dry-run does not write crates to Mixxx."""
    mdj_db = make_multidj_db(tmp_path / "library.sqlite")
    mxdb = make_mixxx_db(tmp_path / "mixxxdb.sqlite")

    conn = sqlite3.connect(str(mdj_db))
    _add_crate_to_multidj(conn, "BPM:125-130", [1])
    conn.close()

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=mxdb)
    result = adapter.full_sync(mdj_db, apply=False)

    mx_conn = sqlite3.connect(str(mxdb))
    # The fixture already has crates from make_mixxx_db; check that no NEW
    # crate named "BPM:125-130" was written.
    count = mx_conn.execute(
        "SELECT COUNT(*) FROM crates WHERE name = 'BPM:125-130'"
    ).fetchone()[0]
    mx_conn.close()

    assert count == 0


def test_push_crates_idempotent(tmp_path):
    """Syncing crates twice does not create duplicates in Mixxx."""
    mdj_db = make_multidj_db(tmp_path / "library.sqlite")
    mxdb = make_mixxx_db(tmp_path / "mixxxdb.sqlite")

    conn = sqlite3.connect(str(mdj_db))
    _add_crate_to_multidj(conn, "Genre: Techno", [8])
    conn.close()

    from multidj.adapters.mixxx import MixxxAdapter
    adapter = MixxxAdapter(mixxx_db_path=mxdb)
    adapter.full_sync(mdj_db, apply=True)
    adapter.full_sync(mdj_db, apply=True)

    mx_conn = sqlite3.connect(str(mxdb))
    count = mx_conn.execute(
        "SELECT COUNT(*) FROM crates WHERE name = 'Genre: Techno'"
    ).fetchone()[0]
    mx_conn.close()
    assert count == 1
