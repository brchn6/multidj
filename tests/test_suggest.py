"""Tests for multidj.suggest — DJ next-track suggestion."""
from __future__ import annotations

import numpy as np
import pytest

from tests.fixtures.multidj_factory import make_multidj_db
from multidj.db import connect
from multidj.embed import store_embedding
from multidj.suggest import _parse_camelot, _key_compat_score, _bpm_compat_score, suggest_next


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


def _seed_embeddings(db, rng=None):
    """Store random 512-dim CLAP embeddings for all non-deleted tracks."""
    if rng is None:
        rng = np.random.default_rng(42)
    with connect(str(db), readonly=True) as conn:
        rows = conn.execute("SELECT id FROM tracks WHERE deleted=0").fetchall()
    track_ids = [r["id"] for r in rows]
    with connect(str(db), readonly=False) as conn:
        for tid in track_ids:
            vec = rng.random(512).astype(np.float32)
            store_embedding(conn, tid, "laion/larger_clap_music", vec)
        conn.commit()
    return track_ids


# ---------------------------------------------------------------------------
# Unit tests: Camelot parsing
# ---------------------------------------------------------------------------

def test_parse_camelot_direct():
    assert _parse_camelot("8B") == (8, "B")
    assert _parse_camelot("1A") == (1, "A")
    assert _parse_camelot("12B") == (12, "B")


def test_parse_camelot_musical_notation():
    # C major = 8B
    result = _parse_camelot("Cmaj")
    assert result == (8, "B")
    # A minor = 8A
    result = _parse_camelot("Amin")
    assert result == (8, "A")


def test_parse_camelot_none_on_garbage():
    assert _parse_camelot(None) is None
    assert _parse_camelot("") is None
    assert _parse_camelot("ZZZ") is None


# ---------------------------------------------------------------------------
# Unit tests: key compatibility
# ---------------------------------------------------------------------------

def test_key_compat_same_key():
    assert _key_compat_score("8B", "8B") == 1.0


def test_key_compat_adjacent_ring():
    # 8B → 9B: adjacent on B ring
    assert _key_compat_score("8B", "9B") == 0.75
    assert _key_compat_score("9B", "8B") == 0.75


def test_key_compat_relative_minor_major():
    # 8B (C major) ↔ 8A (A minor): same number, different letter
    assert _key_compat_score("8B", "8A") == 0.75


def test_key_compat_incompatible():
    assert _key_compat_score("1A", "7B") == 0.0


def test_key_compat_missing():
    assert _key_compat_score(None, "8B") == 0.5
    assert _key_compat_score("8B", None) == 0.5
    assert _key_compat_score(None, None) == 0.5


# ---------------------------------------------------------------------------
# Unit tests: BPM compatibility
# ---------------------------------------------------------------------------

def test_bpm_compat_same():
    assert _bpm_compat_score(128.0, 128.0) == 1.0


def test_bpm_compat_within_window():
    score = _bpm_compat_score(128.0, 135.0, window=15.0)
    assert 0.0 < score < 1.0
    assert abs(score - (1 - 7 / 15)) < 0.01


def test_bpm_compat_outside_window():
    assert _bpm_compat_score(100.0, 120.0, window=15.0) == 0.0


def test_bpm_compat_missing():
    assert _bpm_compat_score(None, 128.0) == 0.5
    assert _bpm_compat_score(128.0, None) == 0.5


# ---------------------------------------------------------------------------
# Integration tests: suggest_next
# ---------------------------------------------------------------------------

def test_suggest_next_returns_results(db):
    _seed_embeddings(db)
    with connect(str(db), readonly=True) as conn:
        row = conn.execute("SELECT id, artist, title FROM tracks WHERE deleted=0 LIMIT 1").fetchone()
    query = f"{row['artist'] or ''} - {row['title'] or ''}"

    result = suggest_next(db_path=str(db), track_ref=query, top_n=5, any_cluster=True)

    assert result["query_track"]["id"] == row["id"]
    assert len(result["suggestions"]) <= 5
    # Should not include the query track itself
    ids = [s["id"] for s in result["suggestions"]]
    assert row["id"] not in ids


def test_suggest_next_excludes_query_from_results(db):
    _seed_embeddings(db)
    with connect(str(db), readonly=True) as conn:
        row = conn.execute("SELECT id, artist, title FROM tracks WHERE deleted=0 LIMIT 1").fetchone()
    query = f"{row['artist'] or ''} - {row['title'] or ''}"

    result = suggest_next(db_path=str(db), track_ref=query, top_n=20, any_cluster=True)
    ids = [s["id"] for s in result["suggestions"]]
    assert row["id"] not in ids


def test_suggest_next_results_sorted_by_score(db):
    _seed_embeddings(db)
    with connect(str(db), readonly=True) as conn:
        row = conn.execute("SELECT id, artist, title FROM tracks WHERE deleted=0 LIMIT 1").fetchone()
    query = f"{row['artist'] or ''} - {row['title'] or ''}"

    result = suggest_next(db_path=str(db), track_ref=query, top_n=5, any_cluster=True)
    scores = [s["score"] for s in result["suggestions"]]
    assert scores == sorted(scores, reverse=True)


def test_suggest_next_missing_track_raises(db):
    _seed_embeddings(db)
    with pytest.raises(RuntimeError, match="Track not found"):
        suggest_next(db_path=str(db), track_ref="zzz nonexistent track xyz", any_cluster=True)


def test_suggest_next_no_embedding_raises(db):
    # No embeddings seeded
    with connect(str(db), readonly=True) as conn:
        row = conn.execute("SELECT id, artist, title FROM tracks WHERE deleted=0 LIMIT 1").fetchone()
    query = f"{row['artist'] or ''} - {row['title'] or ''}"

    with pytest.raises(RuntimeError, match="no embedding"):
        suggest_next(db_path=str(db), track_ref=query, any_cluster=True)


def test_suggest_next_model_field_in_result(db):
    _seed_embeddings(db)
    with connect(str(db), readonly=True) as conn:
        row = conn.execute("SELECT id, artist, title FROM tracks WHERE deleted=0 LIMIT 1").fetchone()
    query = f"{row['artist'] or ''} - {row['title'] or ''}"

    result = suggest_next(db_path=str(db), track_ref=query, top_n=3, any_cluster=True)
    assert result["model"] == "laion/larger_clap_music"


def test_suggest_next_any_cluster_flag_defaults_to_full_library(db):
    """With no Vibe/ crates, any_cluster=False should still return results."""
    _seed_embeddings(db)
    with connect(str(db), readonly=True) as conn:
        row = conn.execute("SELECT id, artist, title FROM tracks WHERE deleted=0 LIMIT 1").fetchone()
    query = f"{row['artist'] or ''} - {row['title'] or ''}"

    result = suggest_next(db_path=str(db), track_ref=query, top_n=5, any_cluster=False)
    # cluster_name should be None (no Vibe/ crates in test DB)
    assert result["cluster"] is None
    assert len(result["suggestions"]) > 0
