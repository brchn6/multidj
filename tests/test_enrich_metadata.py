import sqlite3
import pytest

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


def test_get_enrich_config_returns_none_without_discogs(tmp_path):
    """Returns None for discogs when [discogs] token is absent."""
    cfg = {}
    result = get_enrich_config(cfg)
    assert result["discogs"] is None
    assert result["musicbrainz"]["user_agent"] == "multidj/1.0 (bar.cohen@weizmann.ac.il)"


def test_get_enrich_config_returns_discogs_when_token_set(tmp_path):
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


def test_get_enrich_config_musicbrainz_custom_agent(tmp_path):
    """Custom MusicBrainz user_agent is respected."""
    cfg = {"musicbrainz": {"user_agent": "myapp/2.0 (custom@example.com)"}}
    result = get_enrich_config(cfg)
    assert result["musicbrainz"]["user_agent"] == "myapp/2.0 (custom@example.com)"
