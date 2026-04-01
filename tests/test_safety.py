"""
Cross-cutting safety invariants for MultiDJ.

These tests verify that:
  1. Dry-run mode NEVER mutates the database.
  2. --apply mode DOES mutate the database.
  3. A backup file is created before any write operation.

Tests that depend on command modules not yet ported to the MultiDJ schema are
marked @pytest.mark.skip so they show up clearly as pending work in CI output.

Once Layer 2 agents port the relevant commands, remove the skip decorators.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _genres(db_path: Path) -> list[tuple]:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT genre FROM tracks ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# clean_genres safety
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="multidj.clean not yet ported to MultiDJ schema (Layer 2D)")
def test_clean_genres_dry_run_does_not_write(multidj_db):
    """Dry-run must never mutate the DB."""
    from multidj.clean import clean_genres  # type: ignore[import]

    before = _genres(multidj_db)
    clean_genres(str(multidj_db), apply=False)
    after = _genres(multidj_db)
    assert before == after


@pytest.mark.skip(reason="multidj.clean not yet ported to MultiDJ schema (Layer 2D)")
def test_clean_genres_apply_writes(multidj_db):
    """--apply must actually mutate the DB.

    Fixture track 7 has genre='Music' which is uninformative.
    After apply, it should become NULL.
    """
    from multidj.clean import clean_genres  # type: ignore[import]

    result = clean_genres(str(multidj_db), apply=True, backup=False)
    assert result["total_changes"] > 0

    conn = sqlite3.connect(str(multidj_db))
    try:
        row = conn.execute(
            "SELECT genre FROM tracks WHERE id = 7"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] is None


@pytest.mark.skip(reason="multidj.clean not yet ported to MultiDJ schema (Layer 2D)")
def test_backup_created_before_write(multidj_db, tmp_path):
    """A backup file must exist after --apply.

    TODO: complete once backup.py backup_dir is configurable in tests.
    The result dict should contain a 'backup_path' key that points to an
    existing file.
    """
    from multidj.clean import clean_genres  # type: ignore[import]

    result = clean_genres(str(multidj_db), apply=True, backup=True)
    backup_path = result.get("backup_path")
    assert backup_path is not None, "result must include backup_path"
    assert Path(backup_path).exists(), f"backup file not found: {backup_path}"


# ---------------------------------------------------------------------------
# Fixture integrity — always run, no skip
# ---------------------------------------------------------------------------

def test_mixxx_fixture_track_count(mixxx_db):
    """Mixxx fixture must have 10 rows total, 9 active."""
    conn = sqlite3.connect(str(mixxx_db))
    try:
        total = conn.execute("SELECT COUNT(*) FROM library").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM library WHERE mixxx_deleted = 0"
        ).fetchone()[0]
    finally:
        conn.close()
    assert total == 10
    assert active == 9


def test_multidj_fixture_track_count(multidj_db):
    """MultiDJ fixture must have exactly 9 active (non-deleted) tracks."""
    conn = sqlite3.connect(str(multidj_db))
    try:
        total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE deleted = 0"
        ).fetchone()[0]
    finally:
        conn.close()
    assert total == 9
    assert active == 9


def test_multidj_fixture_deleted_track_absent(multidj_db):
    """Track id=10 (soft-deleted in Mixxx) must NOT be imported into MultiDJ."""
    conn = sqlite3.connect(str(multidj_db))
    try:
        row = conn.execute(
            "SELECT id FROM tracks WHERE id = 10"
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_multidj_fixture_crates(multidj_db):
    """MultiDJ fixture must have 3 crates with correct types."""
    conn = sqlite3.connect(str(multidj_db))
    try:
        rows = conn.execute(
            "SELECT id, name, type FROM crates ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 3
    types = {name: ctype for (_id, name, ctype) in rows}
    assert types["Genre: House"] == "auto"
    assert types["My Favorites"] == "hand-curated"
    assert types["New Crate"] == "catch-all"


def test_multidj_fixture_crate_tracks(multidj_db):
    """MultiDJ fixture crate_tracks must match spec."""
    conn = sqlite3.connect(str(multidj_db))
    try:
        rows = conn.execute(
            "SELECT crate_id, track_id FROM crate_tracks ORDER BY crate_id, track_id"
        ).fetchall()
    finally:
        conn.close()
    assert set(rows) == {(1, 1), (1, 4), (1, 6), (2, 1), (2, 8)}


def test_multidj_fixture_sync_state(multidj_db):
    """Every active track must have a sync_state row for adapter='mixxx'."""
    conn = sqlite3.connect(str(multidj_db))
    try:
        synced_ids = {
            row[0]
            for row in conn.execute(
                "SELECT track_id FROM sync_state WHERE adapter = 'mixxx'"
            ).fetchall()
        }
        track_ids = {
            row[0]
            for row in conn.execute(
                "SELECT id FROM tracks WHERE deleted = 0"
            ).fetchall()
        }
    finally:
        conn.close()
    assert synced_ids == track_ids


def test_multidj_fixture_keys(multidj_db):
    """Tracks with keys in TRACK_KEY_IDS must have correct key values."""
    conn = sqlite3.connect(str(multidj_db))
    try:
        rows = conn.execute(
            "SELECT id, key FROM tracks WHERE key IS NOT NULL ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    key_map = {row[0]: row[1] for row in rows}
    assert key_map[1] == "8B"
    assert key_map[4] == "8B"
    assert key_map[6] == "9A"
    assert key_map[8] == "5A"


def test_mixxx_fixture_soft_deleted_track(mixxx_db):
    """Track id=10 must be present in Mixxx library with mixxx_deleted=1."""
    conn = sqlite3.connect(str(mixxx_db))
    try:
        row = conn.execute(
            "SELECT mixxx_deleted FROM library WHERE id = 10"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == 1


def test_mixxx_fixture_hebrew_track(mixxx_db):
    """Track id=5 must have Hebrew artist name in Mixxx fixture."""
    conn = sqlite3.connect(str(mixxx_db))
    try:
        row = conn.execute(
            "SELECT artist FROM library WHERE id = 5"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "עידן רייכל"


def test_mixxx_fixture_genre_whitespace(mixxx_db):
    """Track id=6 must have genre ' House ' (with surrounding spaces) in Mixxx."""
    conn = sqlite3.connect(str(mixxx_db))
    try:
        row = conn.execute(
            "SELECT genre FROM library WHERE id = 6"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == " House "


def test_multidj_fixture_uninformative_genre_present(multidj_db):
    """Track id=7 must have genre='Music' before any clean operation."""
    conn = sqlite3.connect(str(multidj_db))
    try:
        row = conn.execute(
            "SELECT genre FROM tracks WHERE id = 7"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "Music"
