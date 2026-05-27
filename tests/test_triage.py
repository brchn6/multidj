import os
import sqlite3
import pytest
from pathlib import Path
from multidj.triage import build_triage_queue, write_m3u

# tag_track will be imported in the tag_track test section
try:
    from multidj.triage import tag_track
except ImportError:
    # tag_track not yet implemented; tests will handle the import error
    pass


# ── build_triage_queue ────────────────────────────────────────────────────────

def test_queue_library_wide_returns_unrated(multidj_db):
    """No crate: returns tracks with rating IS NULL or rating=0, deleted=0."""
    tracks = build_triage_queue(str(multidj_db))
    ids = [t["id"] for t in tracks]
    # Fixture unrated active tracks: 3, 5, 7, 9
    assert sorted(ids) == [3, 5, 7, 9]


def test_queue_library_wide_excludes_rated(multidj_db):
    """Rated tracks (1, 2, 4, 6, 8) must not appear in library-wide queue."""
    tracks = build_triage_queue(str(multidj_db))
    ids = {t["id"] for t in tracks}
    for rated_id in (1, 2, 4, 6, 8):
        assert rated_id not in ids


def test_queue_library_wide_excludes_deleted(multidj_db):
    """Track 10 (deleted=1) must never appear."""
    tracks = build_triage_queue(str(multidj_db))
    assert 10 not in {t["id"] for t in tracks}


def test_queue_crate_scoped(multidj_db):
    """--crate returns all non-deleted tracks in that crate, including rated ones."""
    tracks = build_triage_queue(str(multidj_db), crate="Genre: House")
    ids = sorted(t["id"] for t in tracks)
    # Genre: House crate has tracks 1, 4, 6 (all rated but re-triage is OK)
    assert ids == [1, 4, 6]


def test_queue_crate_unknown_returns_empty(multidj_db):
    """Unknown crate name returns empty list, no exception."""
    tracks = build_triage_queue(str(multidj_db), crate="No Such Crate")
    assert tracks == []


def test_queue_limit(multidj_db):
    """--limit caps the result count."""
    tracks = build_triage_queue(str(multidj_db), limit=2)
    assert len(tracks) == 2


def test_queue_track_has_required_fields(multidj_db):
    """Each returned track dict has id, path, artist, title, bpm, key, energy."""
    tracks = build_triage_queue(str(multidj_db))
    assert len(tracks) > 0
    required = {"id", "path", "artist", "title", "bpm", "key", "energy"}
    for t in tracks:
        assert required.issubset(t.keys())


# ── write_m3u ────────────────────────────────────────────────────────────────

def test_write_m3u_creates_file(tmp_path, multidj_db):
    tracks = build_triage_queue(str(multidj_db))
    out = tmp_path / "playlist.m3u"
    write_m3u(tracks, str(out))
    assert out.exists()


def test_write_m3u_header(tmp_path, multidj_db):
    tracks = build_triage_queue(str(multidj_db))
    out = tmp_path / "playlist.m3u"
    write_m3u(tracks, str(out))
    lines = out.read_text().splitlines()
    assert lines[0] == "#EXTM3U"


def test_write_m3u_contains_paths(tmp_path, multidj_db):
    tracks = build_triage_queue(str(multidj_db))
    out = tmp_path / "playlist.m3u"
    write_m3u(tracks, str(out))
    content = out.read_text()
    for t in tracks:
        assert t["path"] in content


# ── tag_track ─────────────────────────────────────────────────────────────────

def test_tag_track_sets_rating(multidj_db, multidj_db_conn):
    """rating 1-5 writes to tracks.rating; deleted stays 0."""
    path = "/music/fixture/01_DJ_Tiesto_-_Red_Lights.mp3"
    tag_track(str(multidj_db), path, rating=3)
    row = multidj_db_conn.execute(
        "SELECT rating, deleted FROM tracks WHERE path=?", (path,)
    ).fetchone()
    assert row["rating"] == 3
    assert row["deleted"] == 0


def test_tag_track_zero_soft_deletes(multidj_db, multidj_db_conn):
    """rating=0 sets deleted=1 and does NOT change rating field."""
    path = "/music/fixture/01_DJ_Tiesto_-_Red_Lights.mp3"
    original_rating = multidj_db_conn.execute(
        "SELECT rating FROM tracks WHERE path=?", (path,)
    ).fetchone()["rating"]

    tag_track(str(multidj_db), path, rating=0)

    row = multidj_db_conn.execute(
        "SELECT rating, deleted FROM tracks WHERE path=?", (path,)
    ).fetchone()
    assert row["deleted"] == 1
    assert row["rating"] == original_rating  # rating unchanged; deleted=1 is the signal


def test_tag_track_unknown_path_noop(multidj_db, multidj_db_conn):
    """Unknown file path is a silent no-op — no exception, no DB change."""
    count_before = multidj_db_conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE deleted=1"
    ).fetchone()[0]

    tag_track(str(multidj_db), "/nonexistent/ghost.mp3", rating=3)

    count_after = multidj_db_conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE deleted=1"
    ).fetchone()[0]
    assert count_after == count_before


def test_tag_track_hard_delete_removes_file(multidj_db, multidj_db_conn, tmp_path):
    """hard_delete=True removes the audio file from disk AND sets deleted=1 in DB."""
    fake_file = tmp_path / "fake_track.mp3"
    fake_file.write_bytes(b"fake audio")

    multidj_db_conn.execute(
        "INSERT INTO tracks (path, artist, title, deleted) VALUES (?, 'A', 'T', 0)",
        (str(fake_file),),
    )
    multidj_db_conn.commit()

    tag_track(str(multidj_db), str(fake_file), rating=0, hard_delete=True)

    assert not fake_file.exists()
    row = multidj_db_conn.execute(
        "SELECT deleted FROM tracks WHERE path=?", (str(fake_file),)
    ).fetchone()
    assert row["deleted"] == 1


def test_tag_track_hard_delete_missing_file_noop(multidj_db, multidj_db_conn):
    """hard_delete=True with file already gone: DB write succeeds, no exception."""
    path = "/music/fixture/01_DJ_Tiesto_-_Red_Lights.mp3"  # in DB but not on disk
    tag_track(str(multidj_db), path, rating=0, hard_delete=True)  # must not raise
    row = multidj_db_conn.execute(
        "SELECT deleted FROM tracks WHERE path=?", (path,)
    ).fetchone()
    assert row["deleted"] == 1


def test_tag_track_marks_dirty(multidj_db):
    """Tagging a track triggers the sync_state dirty flag."""
    path = "/music/fixture/01_DJ_Tiesto_-_Red_Lights.mp3"

    conn = sqlite3.connect(str(multidj_db))
    conn.row_factory = sqlite3.Row
    track_id = conn.execute("SELECT id FROM tracks WHERE path=?", (path,)).fetchone()["id"]
    conn.execute("UPDATE sync_state SET dirty=0 WHERE track_id=?", (track_id,))
    conn.commit()
    conn.close()

    tag_track(str(multidj_db), path, rating=4)

    conn2 = sqlite3.connect(str(multidj_db))
    conn2.row_factory = sqlite3.Row
    dirty = conn2.execute(
        "SELECT dirty FROM sync_state WHERE track_id=?", (track_id,)
    ).fetchone()["dirty"]
    conn2.close()
    assert dirty == 1
