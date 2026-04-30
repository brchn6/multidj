import sqlite3
from multidj.clean import clean_genres, clean_text


def _insert_track(multidj_db_conn: sqlite3.Connection, path: str, genre: str) -> int:
    cur = multidj_db_conn.execute(
        "INSERT INTO tracks (path, genre, deleted) VALUES (?, ?, 0)",
        (path, genre),
    )
    multidj_db_conn.commit()
    return int(cur.lastrowid)


def test_clean_genres_detects_uninformative(multidj_db):
    result = clean_genres(str(multidj_db), apply=False)
    reasons = [c["reason"] for c in result["changes"]]
    assert "uninformative" in reasons


def test_clean_genres_detects_case_variant(multidj_db):
    result = clean_genres(str(multidj_db), apply=False)
    reasons = [c["reason"] for c in result["changes"]]
    assert "case_variant" in reasons


def test_clean_genres_detects_whitespace(multidj_db):
    result = clean_genres(str(multidj_db), apply=False)
    reasons = [c["reason"] for c in result["changes"]]
    assert "whitespace" in reasons


def test_clean_genres_dry_run(multidj_db, multidj_db_conn):
    before = multidj_db_conn.execute("SELECT genre FROM tracks ORDER BY id").fetchall()
    clean_genres(str(multidj_db), apply=False)
    after = multidj_db_conn.execute("SELECT genre FROM tracks ORDER BY id").fetchall()
    assert before == after


def test_clean_genres_apply_uninformative(multidj_db, multidj_db_conn):
    clean_genres(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id=7").fetchone()
    assert row["genre"] is None  # "Music" -> NULL


def test_clean_genres_apply_case(multidj_db, multidj_db_conn):
    clean_genres(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id=4").fetchone()
    assert row["genre"] == "House"  # "house" -> "House"


def test_clean_genres_apply_whitespace(multidj_db, multidj_db_conn):
    clean_genres(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id=6").fetchone()
    assert row["genre"] == "House"  # " House " -> "House"


def test_clean_genres_idempotent(multidj_db):
    r1 = clean_genres(str(multidj_db), apply=True, backup=False)
    r2 = clean_genres(str(multidj_db), apply=True, backup=False)
    assert r2["total_changes"] == 0


def test_clean_text_dry_run(multidj_db, multidj_db_conn):
    before = multidj_db_conn.execute("SELECT artist FROM tracks ORDER BY id").fetchall()
    clean_text(str(multidj_db), apply=False)
    after = multidj_db_conn.execute("SELECT artist FROM tracks ORDER BY id").fetchall()
    assert before == after


def test_clean_modes(multidj_db):
    r = clean_genres(str(multidj_db), apply=False)
    assert r["mode"] == "dry_run"
    r2 = clean_genres(str(multidj_db), apply=True, backup=False)
    assert r2["mode"] == "apply"


def test_clean_genres_apply_donk_canonical_case(multidj_db, multidj_db_conn):
    _insert_track(multidj_db_conn, "/music/fixture/11_donk_upper.mp3", "Donk")
    track_id = _insert_track(multidj_db_conn, "/music/fixture/12_donk_lower.mp3", "DONK")

    clean_genres(str(multidj_db), apply=True, backup=False)

    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] == "Donk"


def test_clean_genres_nulls_hebrew_metadata_junk(multidj_db, multidj_db_conn):
    genre = "sarit hadad, meortz hahaim, מירוץ החיים, רמיקס"
    track_id = _insert_track(multidj_db_conn, "/music/fixture/13_hebrew_junk.mp3", genre)

    result = clean_genres(str(multidj_db), apply=False)
    change = next(c for c in result["changes"] if c["track_id"] == track_id)
    assert change["new_genre"] is None
    assert change["reason"] == "hebrew_metadata_junk"

    clean_genres(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] is None


def test_clean_genres_nulls_long_multi_value_junk(multidj_db, multidj_db_conn):
    genre = "Free Download, UKG, NUKG, UK Garage, Garage"
    track_id = _insert_track(multidj_db_conn, "/music/fixture/14_multi_junk.mp3", genre)

    result = clean_genres(str(multidj_db), apply=False)
    change = next(c for c in result["changes"] if c["track_id"] == track_id)
    assert change["new_genre"] is None
    assert change["reason"] in {"suspicious_multi_value", "junk_token"}

    clean_genres(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] is None


def test_clean_genres_keeps_uk_garage(multidj_db, multidj_db_conn):
    track_id = _insert_track(multidj_db_conn, "/music/fixture/15_uk_garage.mp3", "UK Garage")

    clean_genres(str(multidj_db), apply=True, backup=False)

    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] == "UK Garage"


def test_clean_genres_keeps_drum_and_bass(multidj_db, multidj_db_conn):
    track_id = _insert_track(multidj_db_conn, "/music/fixture/16_dnb.mp3", "Drum & Bass")

    clean_genres(str(multidj_db), apply=True, backup=False)

    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] == "Drum & Bass"


def test_clean_genres_keeps_afro_house(multidj_db, multidj_db_conn):
    track_id = _insert_track(multidj_db_conn, "/music/fixture/17_afro_house.mp3", "Afro House")

    clean_genres(str(multidj_db), apply=True, backup=False)

    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] == "Afro House"


def test_clean_genres_nulls_symbol_only(multidj_db, multidj_db_conn):
    track_id = _insert_track(multidj_db_conn, "/music/fixture/18_symbol_only.mp3", ":)")

    result = clean_genres(str(multidj_db), apply=False)
    change = next(c for c in result["changes"] if c["track_id"] == track_id)
    assert change["new_genre"] is None
    assert change["reason"] == "symbol_only"

    clean_genres(str(multidj_db), apply=True, backup=False)
    row = multidj_db_conn.execute("SELECT genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row["genre"] is None
