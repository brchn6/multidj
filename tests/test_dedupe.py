import sqlite3
from multidj.dedupe import dedupe


def test_dedupe_finds_duplicate_group(multidj_db):
    """Tracks 1 and 4 are duplicates by artist+title"""
    result = dedupe(str(multidj_db), by="artist-title")
    assert result["total_groups"] == 1
    group = result["groups"][0]
    track_ids = {group["keeper"]["track_id"]} | {d["track_id"] for d in group["duplicates"]}
    assert 1 in track_ids
    assert 4 in track_ids


def test_dedupe_keeper_selection(multidj_db):
    """Track 1 (play_count=12) beats track 4 (play_count=1)"""
    result = dedupe(str(multidj_db), by="artist-title")
    group = result["groups"][0]
    assert group["keeper"]["track_id"] == 1


def test_dedupe_dry_run_no_write(multidj_db, multidj_db_conn):
    """dry-run must not set deleted=1"""
    dedupe(str(multidj_db), by="artist-title", apply=False)
    deleted = multidj_db_conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE deleted=1"
    ).fetchone()[0]
    assert deleted == 0


def test_dedupe_apply_marks_duplicate(multidj_db, multidj_db_conn):
    """--apply must soft-delete track 4"""
    dedupe(str(multidj_db), by="artist-title", apply=True, backup=False)
    row4 = multidj_db_conn.execute(
        "SELECT deleted FROM tracks WHERE id=4"
    ).fetchone()
    row1 = multidj_db_conn.execute(
        "SELECT deleted FROM tracks WHERE id=1"
    ).fetchone()
    assert row4["deleted"] == 1   # duplicate soft-deleted
    assert row1["deleted"] == 0   # keeper untouched


def test_dedupe_no_false_positives(multidj_db):
    """Tracks 2 and 8 are not duplicates"""
    result = dedupe(str(multidj_db), by="artist-title")
    all_ids = set()
    for g in result["groups"]:
        all_ids.add(g["keeper"]["track_id"])
        all_ids.update(d["track_id"] for d in g["duplicates"])
    assert 2 not in all_ids
    assert 8 not in all_ids


def test_dedupe_mode_field(multidj_db):
    r = dedupe(str(multidj_db), apply=False)
    assert r["mode"] == "dry_run"
    r2 = dedupe(str(multidj_db), apply=True, backup=False)
    assert r2["mode"] == "apply"
