from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest


def _set_genre(db_path, track_id, genre=None, genre_source=None, genre_confidence=None):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE tracks SET genre=?, genre_source=?, genre_confidence=? WHERE id=?",
        (genre, genre_source, genre_confidence, track_id),
    )
    conn.commit()
    conn.close()


def _get_genre_row(db_path, track_id):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT genre, genre_source, genre_confidence FROM tracks WHERE id=?", (track_id,)
    ).fetchone()
    conn.close()
    return row


def _add_discogs_styles(db_path, track_id):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO track_tags (track_id, key, value) VALUES (?, 'discogs_primary_style', 'Tech House')",
        (track_id,),
    )
    conn.commit()
    conn.close()


def test_manual_source_is_never_overwritten(multidj_db):
    """genre_source='manual' must be skipped unconditionally, even with force."""
    _set_genre(multidj_db, 1, genre="My Custom Genre", genre_source="manual")
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True, force=True)
    row = _get_genre_row(multidj_db, 1)
    assert row[0] == "My Custom Genre"
    assert row[1] == "manual"


def test_specific_genre_from_file_tags_gets_source_file(multidj_db):
    """Track with specific genre but no genre_source → source='file'."""
    _set_genre(multidj_db, 8, genre="Techno", genre_source=None)
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    row = _get_genre_row(multidj_db, 8)
    assert row[0] == "Techno"
    assert row[1] == "file"
    assert row[2] == 1.0


def test_specific_genre_with_discogs_styles_gets_source_discogs(multidj_db):
    """Track has specific genre AND discogs_primary_style tag → source='discogs'."""
    _set_genre(multidj_db, 6, genre="House", genre_source=None)
    _add_discogs_styles(multidj_db, 6)
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    row = _get_genre_row(multidj_db, 6)
    assert row[1] == "discogs"


def test_uninformative_genre_triggers_discogs_lookup(multidj_db):
    """Track with genre='Music' (uninformative) → Discogs is queried."""
    _set_genre(multidj_db, 7, genre="Music", genre_source=None)
    discogs_result = {
        "styles": ["Tech House", "Deep House"],
        "release_year": 2003,
        "label": "Kompakt",
        "catalog_number": None,
        "score": 0.92,
        "source": "discogs",
    }
    with patch("multidj.enrich_genre.search_discogs", return_value=discogs_result) as mock_d, \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    mock_d.assert_called()
    row = _get_genre_row(multidj_db, 7)
    assert row[0] == "Tech House"
    assert row[1] == "discogs"
    assert row[2] == 1.0


def test_discogs_miss_falls_through_to_musicbrainz(multidj_db):
    """Discogs returns None → MusicBrainz is queried."""
    _set_genre(multidj_db, 9, genre=None, genre_source=None)
    mb_result = {
        "genre": "House",
        "release_year": 1999,
        "album": "Shades of Rhythm",
        "label": None,
        "score": 0.88,
    }
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=mb_result) as mock_mb:
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    mock_mb.assert_called()
    row = _get_genre_row(multidj_db, 9)
    assert row[0] == "House"
    assert row[1] == "musicbrainz"


def test_no_web_hit_no_embedding_leaves_genre_unchanged(multidj_db):
    """No Discogs/MB hit and no embedding → genre unchanged, genre_source stays NULL."""
    _set_genre(multidj_db, 9, genre=None, genre_source=None)
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    row = _get_genre_row(multidj_db, 9)
    assert row[0] is None
    assert row[1] is None


def test_clap_classifies_track_with_embedding(multidj_db):
    """No web hit but embedding present → CLAP classifies and writes genre."""
    import numpy as np
    _set_genre(multidj_db, 9, genre=None, genre_source=None)
    fake_vec = np.ones(512, dtype=np.float32)
    conn = sqlite3.connect(str(multidj_db))
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (track_id, model_name, vector) VALUES (?, ?, ?)",
        (9, "laion/larger_clap_music", fake_vec.tobytes()),
    )
    conn.commit()
    conn.close()

    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None), \
         patch("multidj.enrich_genre._clap_classify_vec", return_value=("Tech House", 0.42)):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    row = _get_genre_row(multidj_db, 9)
    assert row[0] == "Tech House"
    assert row[1] == "clap"
    assert abs(row[2] - 0.42) < 0.001


def test_clap_below_threshold_leaves_genre_unchanged(multidj_db):
    """CLAP confidence < _CLAP_MIN_CONF → genre not written."""
    import numpy as np
    _set_genre(multidj_db, 9, genre=None, genre_source=None)
    fake_vec = np.ones(512, dtype=np.float32)
    conn = sqlite3.connect(str(multidj_db))
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (track_id, model_name, vector) VALUES (?, ?, ?)",
        (9, "laion/larger_clap_music", fake_vec.tobytes()),
    )
    conn.commit()
    conn.close()

    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None), \
         patch("multidj.enrich_genre._clap_classify_vec", return_value=(None, 0.0)):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True)
    row = _get_genre_row(multidj_db, 9)
    assert row[1] is None


def test_incremental_skips_already_sourced_tracks(multidj_db):
    """genre_source already set → skip unless force=True."""
    _set_genre(multidj_db, 8, genre="Techno", genre_source="file", genre_confidence=1.0)
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True, force=False)
    row = _get_genre_row(multidj_db, 8)
    assert row[1] == "file"  # unchanged


def test_force_re_enriches_sourced_tracks(multidj_db):
    """force=True re-runs enrichment even for tracks with existing genre_source."""
    _set_genre(multidj_db, 8, genre="Techno", genre_source="file", genre_confidence=1.0)
    discogs_result = {
        "styles": ["Minimal Techno"],
        "release_year": 2002,
        "label": "Tresor",
        "catalog_number": None,
        "score": 0.95,
        "source": "discogs",
    }
    with patch("multidj.enrich_genre.search_discogs", return_value=discogs_result), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=True, force=True)
    row = _get_genre_row(multidj_db, 8)
    assert row[1] == "discogs"


def test_dry_run_does_not_write(multidj_db):
    """apply=False → no DB writes."""
    _set_genre(multidj_db, 7, genre="Music", genre_source=None)
    with patch("multidj.enrich_genre.search_discogs", return_value={
        "styles": ["Tech House"], "release_year": 2001, "label": None,
        "catalog_number": None, "score": 0.9, "source": "discogs",
    }), patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=False)
    assert result["mode"] == "dry_run"
    assert result["applied"] == 0
    row = _get_genre_row(multidj_db, 7)
    assert row[1] is None  # not written


def test_returns_expected_summary_keys(multidj_db):
    """enrich_genre always returns a dict with standard summary keys."""
    with patch("multidj.enrich_genre.search_discogs", return_value=None), \
         patch("multidj.enrich_genre.search_musicbrainz", return_value=None):
        from multidj.enrich_genre import enrich_genre
        result = enrich_genre(str(multidj_db), apply=False)
    assert "mode" in result
    assert "total_candidates" in result
    assert "applied" in result
    assert "errors" in result
    assert "would_apply" in result
    assert "error_details" in result
