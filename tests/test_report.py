from __future__ import annotations

import sqlite3

from tests.fixtures.multidj_factory import make_multidj_db
from multidj.cli import main
from multidj.report import (
    collect_report_data_from_db,
    get_camelot_compatibility,
    write_dashboard_report,
)


def test_get_camelot_compatibility_rules():
    assert get_camelot_compatibility("8A", "8A") == "compatible"
    assert get_camelot_compatibility("8A", "7A") == "compatible"
    assert get_camelot_compatibility("8A", "9A") == "compatible"
    assert get_camelot_compatibility("8A", "8B") == "relative"
    assert get_camelot_compatibility("8A", "11B") == "incompatible"
    assert get_camelot_compatibility(None, "8A") == "incompatible"


def test_collect_report_data_includes_crate_track_keys(tmp_path):
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    data = collect_report_data_from_db(str(db_path))

    assert "crates" in data
    assert len(data["crates"]) >= 1

    first_non_empty = next((c for c in data["crates"] if c["tracks"]), None)
    assert first_non_empty is not None
    track = first_non_empty["tracks"][0]
    assert "title" in track
    assert "artist" in track
    assert "bpm" in track
    assert "key" in track


def test_write_dashboard_report_is_read_only(tmp_path):
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    report_path = tmp_path / "dashboard.html"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    before = conn.execute(
        "SELECT id, artist, title, genre, bpm, key, rating, energy, deleted, updated_at FROM tracks ORDER BY id"
    ).fetchall()
    before_values = [tuple(row) for row in before]
    conn.close()

    write_dashboard_report(str(db_path), str(report_path))

    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    after = conn2.execute(
        "SELECT id, artist, title, genre, bpm, key, rating, energy, deleted, updated_at FROM tracks ORDER BY id"
    ).fetchall()
    after_values = [tuple(row) for row in after]
    conn2.close()

    assert before_values == after_values
    assert report_path.exists()


def test_cli_report_dashboard_command(tmp_path):
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    output = tmp_path / "cli_dashboard.html"

    rc = main(["--db", str(db_path), "report", "dashboard", "--output", str(output)])
    assert rc == 0
    assert output.exists()
