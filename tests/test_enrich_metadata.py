import sqlite3
import pytest


def test_migration_006_adds_release_year_and_label(multidj_db):
    """Migration 006 must add release_year and label to tracks."""
    conn = sqlite3.connect(str(multidj_db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT release_year, label FROM tracks LIMIT 1").fetchone()
        assert row is not None
        assert row["release_year"] is None  # new column starts NULL
        assert row["label"] is None
    finally:
        conn.close()
