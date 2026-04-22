import sqlite3
from multidj.crates import audit_crates, hide_crates, show_crates, delete_crates, rebuild_crates
from multidj.constants import BPM_RANGES


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


def test_rebuild_creates_bpm_crates(multidj_db, tmp_path):
    """rebuild_crates generates BPM: crates for each range that has tracks."""
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET bpm = 128.0 WHERE id = 1")
    conn.commit()
    conn.close()

    result = rebuild_crates(str(multidj_db), apply=True, backup_dir=str(tmp_path))

    conn2 = sqlite3.connect(str(multidj_db))
    bpm_crates = conn2.execute(
        "SELECT name FROM crates WHERE name LIKE 'BPM:%'"
    ).fetchall()
    conn2.close()

    crate_names = {r[0] for r in bpm_crates}
    # 128.0 BPM falls in both BPM:125-130 and BPM:128-135 (intentional overlap)
    assert "BPM:125-130" in crate_names
    assert "BPM:128-135" in crate_names


def test_rebuild_bpm_crate_contains_correct_tracks(multidj_db, tmp_path):
    """Each BPM crate contains only tracks in its range."""
    conn = sqlite3.connect(str(multidj_db))
    conn.execute("UPDATE tracks SET bpm = 125.0 WHERE id = 1")
    conn.execute("UPDATE tracks SET bpm = 90.0  WHERE id = 2")
    conn.commit()
    conn.close()

    rebuild_crates(str(multidj_db), apply=True, backup_dir=str(tmp_path))

    conn2 = sqlite3.connect(str(multidj_db))
    crate = conn2.execute(
        "SELECT id FROM crates WHERE name = 'BPM:115-125'"
    ).fetchone()
    # bpm=125.0 is NOT in 115-125 (high is exclusive)
    if crate:
        tracks_in_crate = conn2.execute(
            "SELECT track_id FROM crate_tracks WHERE crate_id = ?", (crate[0],)
        ).fetchall()
        track_ids = {r[0] for r in tracks_in_crate}
        assert 1 not in track_ids
    conn2.close()


def test_rebuild_bpm_crates_dry_run_no_write(multidj_db):
    """Dry-run does not create BPM crates."""
    rebuild_crates(str(multidj_db), apply=False)

    conn = sqlite3.connect(str(multidj_db))
    count = conn.execute(
        "SELECT COUNT(*) FROM crates WHERE name LIKE 'BPM:%'"
    ).fetchone()[0]
    conn.close()
    assert count == 0


def test_rebuild_creates_key_crates(multidj_db, tmp_path):
    """Key: crates are created when tracks have key values."""
    import sqlite3
    conn = sqlite3.connect(str(multidj_db))
    ids = [r[0] for r in conn.execute("SELECT id FROM tracks WHERE deleted=0").fetchall()[:3]]
    for tid in ids:
        conn.execute("UPDATE tracks SET key = 'Cmaj' WHERE id = ?", (tid,))
    conn.commit()
    conn.close()

    from multidj.config import DEFAULT_CONFIG
    import copy
    cfg = copy.deepcopy(DEFAULT_CONFIG)

    result = rebuild_crates(db_path=str(multidj_db), apply=True, backup_dir=str(tmp_path), cfg=cfg)
    conn = sqlite3.connect(str(multidj_db))
    key_crates = conn.execute(
        "SELECT name FROM crates WHERE name LIKE 'Key:%'"
    ).fetchall()
    conn.close()
    assert len(key_crates) >= 1
    assert any("8B" in r[0] for r in key_crates)  # Cmaj → 8B


def test_rebuild_creates_energy_crates(multidj_db, tmp_path):
    """Energy: crates are created when tracks have energy values."""
    import sqlite3
    conn = sqlite3.connect(str(multidj_db))
    ids = [r[0] for r in conn.execute("SELECT id FROM tracks WHERE deleted=0").fetchall()]
    for i, tid in enumerate(ids[:9]):
        energy = [0.1, 0.1, 0.1, 0.5, 0.5, 0.5, 0.8, 0.8, 0.8][i]
        conn.execute("UPDATE tracks SET energy = ? WHERE id = ?", (energy, tid))
    conn.commit()
    conn.close()

    from multidj.config import DEFAULT_CONFIG
    import copy
    cfg = copy.deepcopy(DEFAULT_CONFIG)

    rebuild_crates(db_path=str(multidj_db), apply=True, backup_dir=str(tmp_path), cfg=cfg)
    conn = sqlite3.connect(str(multidj_db))
    energy_crates = [r[0] for r in conn.execute(
        "SELECT name FROM crates WHERE name LIKE 'Energy:%'"
    ).fetchall()]
    conn.close()
    assert "Energy: Low" in energy_crates
    assert "Energy: Mid" in energy_crates
    assert "Energy: High" in energy_crates


def test_rebuild_respects_config_toggle(multidj_db, tmp_path):
    """With bpm=False in config, no BPM: crates are created."""
    from multidj.config import DEFAULT_CONFIG
    import copy
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["crates"]["bpm"] = False

    rebuild_crates(db_path=str(multidj_db), apply=True, backup_dir=str(tmp_path), cfg=cfg)

    import sqlite3
    conn = sqlite3.connect(str(multidj_db))
    bpm_crates = conn.execute("SELECT name FROM crates WHERE name LIKE 'BPM:%'").fetchall()
    conn.close()
    assert len(bpm_crates) == 0
