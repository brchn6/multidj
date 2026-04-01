import sqlite3
from multidj.clean import clean_genres, clean_text


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
