import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from multidj.config import get_enrich_config


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


def test_get_enrich_config_returns_none_without_discogs():
    """Returns None for discogs when [discogs] token is absent."""
    cfg = {}
    result = get_enrich_config(cfg)
    assert result["discogs"] is None
    assert result["musicbrainz"]["user_agent"] == "multidj/1.0 (bar.cohen@weizmann.ac.il)"


def test_get_enrich_config_returns_discogs_when_token_set():
    """Returns discogs dict when token is configured."""
    cfg = {
        "discogs": {
            "token": "mytoken",
            "user_agent": "multidj/1.0",
        }
    }
    result = get_enrich_config(cfg)
    assert result["discogs"] is not None
    assert result["discogs"]["token"] == "mytoken"
    assert result["discogs"]["user_agent"] == "multidj/1.0"


def test_get_enrich_config_musicbrainz_custom_agent():
    """Custom MusicBrainz user_agent is respected."""
    cfg = {"musicbrainz": {"user_agent": "myapp/2.0 (custom@example.com)"}}
    result = get_enrich_config(cfg)
    assert result["musicbrainz"]["user_agent"] == "myapp/2.0 (custom@example.com)"


def _make_id3_mock(tdrc=None, talb=None, tpub=None, tcon=None):
    """Build a mock mutagen ID3 object with the given tag values."""
    tags_dict = {}
    if tdrc:
        t = MagicMock()
        t.text = [tdrc]
        tags_dict["TDRC"] = t
    if talb:
        t = MagicMock()
        t.text = [talb]
        tags_dict["TALB"] = t
    if tpub:
        t = MagicMock()
        t.text = [tpub]
        tags_dict["TPUB"] = t
    if tcon:
        t = MagicMock()
        t.text = [tcon]
        tags_dict["TCON"] = t

    # MagicMock auto-creates 'getall', so hasattr(tags, 'getall') is True — ID3 branch
    mock_tags = MagicMock()
    mock_tags.get.side_effect = lambda k, d=None: tags_dict.get(k, d)
    mock_file = MagicMock()
    mock_file.tags = mock_tags
    return mock_file


def test_read_file_tags_extracts_id3_year():
    from multidj.enrich import read_file_tags
    with patch("mutagen.File", return_value=_make_id3_mock(tdrc="2003-05-12")):
        result = read_file_tags("/fake/track.mp3")
    assert result["release_year"] == 2003


def test_read_file_tags_extracts_id3_label():
    from multidj.enrich import read_file_tags
    with patch("mutagen.File", return_value=_make_id3_mock(tpub="Warp Records")):
        result = read_file_tags("/fake/track.mp3")
    assert result["label"] == "Warp Records"


def test_read_file_tags_extracts_id3_album_and_genre():
    from multidj.enrich import read_file_tags
    with patch("mutagen.File", return_value=_make_id3_mock(talb="Mezzanine", tcon="Trip Hop")):
        result = read_file_tags("/fake/track.mp3")
    assert result["album"] == "Mezzanine"
    assert result["genre"] == "Trip Hop"


def test_read_file_tags_returns_empty_on_no_file():
    from multidj.enrich import read_file_tags
    with patch("mutagen.File", return_value=None):
        result = read_file_tags("/fake/missing.mp3")
    assert result == {}


def test_read_file_tags_skips_bad_year():
    from multidj.enrich import read_file_tags
    with patch("mutagen.File", return_value=_make_id3_mock(tdrc="not-a-year")):
        result = read_file_tags("/fake/track.mp3")
    assert "release_year" not in result


def _make_flac_mock(date=None, album=None, organization=None, genre=None):
    """Build a mock mutagen FLAC/Vorbis object."""
    tags_dict = {}
    if date:
        tags_dict["date"] = [date]
    if album:
        tags_dict["album"] = [album]
    if organization:
        tags_dict["organization"] = [organization]
    if genre:
        tags_dict["genre"] = [genre]

    # spec=["get"] means hasattr(tags, "getall") == False — FLAC/Vorbis branch
    mock_tags = MagicMock(spec=["get"])
    mock_tags.get.side_effect = lambda k, d=None: tags_dict.get(k, d)
    mock_file = MagicMock()
    mock_file.tags = mock_tags
    return mock_file


def test_read_file_tags_extracts_flac_fields():
    from multidj.enrich import read_file_tags
    with patch("mutagen.File", return_value=_make_flac_mock(
        date="1998", album="Selected Ambient Works", organization="R&S Records", genre="Ambient"
    )):
        result = read_file_tags("/fake/track.flac")
    assert result["release_year"] == 1998
    assert result["album"] == "Selected Ambient Works"
    assert result["label"] == "R&S Records"
    assert result["genre"] == "Ambient"
