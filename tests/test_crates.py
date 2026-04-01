import sqlite3
from multidj.crates import audit_crates, hide_crates, show_crates, delete_crates, rebuild_crates


def test_audit_classification(multidj_db):
    result = audit_crates(str(multidj_db))
    # Find by name
    all_crates = (
        result.get("crates_above", []) +
        result.get("crates_below_hand_curated", []) +
        result.get("crates_below_auto", [])
    )
    types_by_name = {c["name"]: c["type"] for c in all_crates}
    assert types_by_name.get("Genre: House") == "auto"
    assert types_by_name.get("My Favorites") == "hand-curated"


def test_audit_catch_all_count(multidj_db):
    result = audit_crates(str(multidj_db))
    assert result["catch_all_crates"] == 1


def test_audit_total_crates(multidj_db):
    result = audit_crates(str(multidj_db))
    assert result["total_crates"] == 3


def test_hide_auto_crate(multidj_db, multidj_db_conn):
    """Genre: House (3 tracks < threshold 5) should be hidden"""
    hide_crates(str(multidj_db), min_tracks=5, apply=True, backup=False)
    row = multidj_db_conn.execute(
        "SELECT show FROM crates WHERE name='Genre: House'"
    ).fetchone()
    assert row["show"] == 0


def test_hide_protects_hand_curated(multidj_db, multidj_db_conn):
    """My Favorites should NOT be hidden"""
    hide_crates(str(multidj_db), min_tracks=5, apply=True, backup=False)
    row = multidj_db_conn.execute(
        "SELECT show FROM crates WHERE name='My Favorites'"
    ).fetchone()
    assert row["show"] == 1


def test_hide_dry_run(multidj_db, multidj_db_conn):
    before = multidj_db_conn.execute("SELECT show FROM crates ORDER BY id").fetchall()
    hide_crates(str(multidj_db), min_tracks=5, apply=False)
    after = multidj_db_conn.execute("SELECT show FROM crates ORDER BY id").fetchall()
    assert before == after


def test_show_restores_hidden(multidj_db, multidj_db_conn):
    hide_crates(str(multidj_db), min_tracks=5, apply=True, backup=False)
    show_crates(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute(
        "SELECT show FROM crates WHERE name='Genre: House'"
    ).fetchone()
    assert row["show"] == 1


def test_rebuild_creates_genre_crate(multidj_db, multidj_db_conn):
    """After rebuild with low threshold, Genre: House should exist"""
    rebuild_crates(str(multidj_db), min_tracks=1, apply=True, backup=False)
    row = multidj_db_conn.execute(
        "SELECT id FROM crates WHERE name='Genre: House'"
    ).fetchone()
    assert row is not None


def test_rebuild_creates_hebrew_crate(multidj_db, multidj_db_conn):
    """Track 5 is Hebrew — Lang: Hebrew crate should be created"""
    rebuild_crates(str(multidj_db), min_tracks=1, apply=True, backup=False)
    row = multidj_db_conn.execute(
        "SELECT id FROM crates WHERE name='Lang: Hebrew'"
    ).fetchone()
    assert row is not None


def test_rebuild_deletes_old_auto_crates(multidj_db, multidj_db_conn):
    """Old Genre: House crate deleted and recreated"""
    old_row = multidj_db_conn.execute(
        "SELECT id FROM crates WHERE name='Genre: House'"
    ).fetchone()
    old_id = old_row["id"]
    rebuild_crates(str(multidj_db), min_tracks=1, apply=True, backup=False)
    new_row = multidj_db_conn.execute(
        "SELECT id FROM crates WHERE name='Genre: House'"
    ).fetchone()
    # ID should be different (deleted and re-inserted)
    assert new_row["id"] != old_id


def test_rebuild_idempotent(multidj_db, multidj_db_conn):
    rebuild_crates(str(multidj_db), min_tracks=1, apply=True, backup=False)
    count1 = multidj_db_conn.execute("SELECT COUNT(*) FROM crates").fetchone()[0]
    rebuild_crates(str(multidj_db), min_tracks=1, apply=True, backup=False)
    count2 = multidj_db_conn.execute("SELECT COUNT(*) FROM crates").fetchone()[0]
    assert count1 == count2


def test_delete_auto_crate(multidj_db, multidj_db_conn):
    delete_crates(str(multidj_db), min_tracks=5, apply=True, backup=False)
    row = multidj_db_conn.execute(
        "SELECT id FROM crates WHERE name='Genre: House'"
    ).fetchone()
    assert row is None


def test_delete_protects_hand_curated(multidj_db, multidj_db_conn):
    delete_crates(str(multidj_db), min_tracks=5, apply=True, backup=False)
    row = multidj_db_conn.execute(
        "SELECT id FROM crates WHERE name='My Favorites'"
    ).fetchone()
    assert row is not None
