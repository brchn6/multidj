import sqlite3
from tests.fixtures.multidj_factory import make_multidj_db

def test_cue_points_table_exists(tmp_path):
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cue_points'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1

def test_cue_points_schema(tmp_path):
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    conn = sqlite3.connect(str(db_path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cue_points)").fetchall()}
    conn.close()
    assert cols == {"id", "track_id", "type", "position", "label", "color", "created_at"}
