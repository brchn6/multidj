import pytest
from pathlib import Path
from multidj.triage import build_triage_queue, write_m3u


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
