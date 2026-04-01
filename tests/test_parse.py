from multidj.parse import parse_library, parse_filename


def test_parse_filename_high_confidence():
    """Pure function test — no DB.

    Underscore filenames yield low confidence because the format is ambiguous
    (track numbers and other prefixes can't be reliably stripped). Use a proper
    space-dash-space filename to get high confidence + remixer extraction.
    """
    result = parse_filename("DJ Tiesto - Red Lights (Someone Remix).mp3")
    assert result["artist"] == "DJ Tiesto"
    assert result["title"] == "Red Lights (Someone Remix)"
    assert result["remixer"] == "Someone"
    assert result["confidence"] == "high"


def test_parse_filename_underscore_format():
    """Underscore filenames produce low confidence and merged artist."""
    result = parse_filename("03_DJ_Tiesto_-_Red_Lights_Remix.mp3")
    assert result["confidence"] == "low"
    assert "underscore_format" in result["notes"]


def test_parse_skips_already_tagged(multidj_db):
    """Track 1 has artist+title — should be skipped (default min_confidence=medium)."""
    result = parse_library(str(multidj_db))
    change_ids = [c["track_id"] for c in result["changes"]]
    assert 1 not in change_ids


def test_parse_proposes_untagged(multidj_db):
    """Track 3 has no artist/title and a parseable filename.

    The filename uses underscores (low confidence), so min_confidence must be
    lowered to 'low' to include it.
    """
    result = parse_library(str(multidj_db), min_confidence="low")
    change_ids = [c["track_id"] for c in result["changes"]]
    assert 3 in change_ids


def test_parse_dry_run_no_write(multidj_db, multidj_db_conn):
    """dry-run must not mutate DB"""
    before = multidj_db_conn.execute(
        "SELECT artist, title FROM tracks WHERE id=3"
    ).fetchone()
    parse_library(str(multidj_db), apply=False, min_confidence="low")
    after = multidj_db_conn.execute(
        "SELECT artist, title FROM tracks WHERE id=3"
    ).fetchone()
    assert before["artist"] == after["artist"]
    assert before["title"] == after["title"]


def test_parse_apply_writes(multidj_db, multidj_db_conn):
    """--apply must write artist/title to tracks table.

    Track 3 filename uses underscores (low confidence) so min_confidence='low'
    is required to include it.
    """
    parse_library(str(multidj_db), apply=True, backup=False, min_confidence="low")
    row = multidj_db_conn.execute(
        "SELECT artist, title FROM tracks WHERE id=3"
    ).fetchone()
    # filename: "03_DJ_Tiesto_-_Red_Lights_Remix.mp3" -> artist includes number prefix
    assert row["artist"] is not None
    assert row["title"] is not None


def test_parse_force_overwrites(multidj_db, multidj_db_conn):
    """--force should include already-tagged tracks.

    Track 1 filename also uses underscores so min_confidence='low' is needed.
    """
    result = parse_library(str(multidj_db), apply=False, force=True, min_confidence="low")
    change_ids = [c["track_id"] for c in result["changes"]]
    # Track 1 should now be included
    assert 1 in change_ids


def test_parse_mode_field(multidj_db):
    result = parse_library(str(multidj_db), apply=False)
    assert result["mode"] == "dry_run"
    result2 = parse_library(str(multidj_db), apply=True, backup=False)
    assert result2["mode"] == "apply"
