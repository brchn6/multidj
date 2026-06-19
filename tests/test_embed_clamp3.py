"""Tests for the CLaMP3 embedding backend.

All tests mock out the heavy model dependencies (MERT, CLaMP3) so the suite
runs without the ``clamp3`` extra installed and without GPU access.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests.fixtures.multidj_factory import make_multidj_db
from multidj.embed import store_embedding, _blob_to_vec, analyze_embed, find_similar, MODEL_CLAMP3
from multidj.db import connect


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


CLAMP3_VEC = np.ones(768, dtype=np.float32) * 0.5
CLAP_VEC   = np.ones(512, dtype=np.float32) * 0.1


# ---------------------------------------------------------------------------
# DB storage — multi-model coexistence
# ---------------------------------------------------------------------------

def test_multi_model_embeddings_coexist(db):
    """CLAP and CLaMP3 embeddings can be stored for the same track."""
    with connect(str(db), readonly=True) as conn:
        track_id = conn.execute(
            "SELECT id FROM tracks WHERE deleted=0 LIMIT 1"
        ).fetchone()["id"]

    with connect(str(db), readonly=False) as conn:
        store_embedding(conn, track_id, "laion/larger_clap_music", CLAP_VEC)
        store_embedding(conn, track_id, MODEL_CLAMP3, CLAMP3_VEC)
        conn.commit()

    raw = sqlite3.connect(str(db))
    count = raw.execute(
        "SELECT COUNT(*) FROM embeddings WHERE track_id=?", (track_id,)
    ).fetchone()[0]
    raw.close()
    assert count == 2


def test_clamp3_vector_roundtrip(db):
    """768-dim CLAMP3 vector survives a blob round-trip."""
    with connect(str(db), readonly=True) as conn:
        track_id = conn.execute(
            "SELECT id FROM tracks WHERE deleted=0 LIMIT 1"
        ).fetchone()["id"]

    with connect(str(db), readonly=False) as conn:
        store_embedding(conn, track_id, MODEL_CLAMP3, CLAMP3_VEC)
        conn.commit()

    raw = sqlite3.connect(str(db))
    row = raw.execute(
        "SELECT vector FROM embeddings WHERE track_id=? AND model_name=?",
        (track_id, MODEL_CLAMP3),
    ).fetchone()
    raw.close()

    recovered = _blob_to_vec(row[0])
    assert recovered.shape == (768,)
    np.testing.assert_allclose(recovered, CLAMP3_VEC, rtol=1e-5)


def test_upsert_same_model(db):
    """Upserting the same (track_id, model_name) replaces the vector."""
    with connect(str(db), readonly=True) as conn:
        track_id = conn.execute(
            "SELECT id FROM tracks WHERE deleted=0 LIMIT 1"
        ).fetchone()["id"]

    vec2 = np.zeros(768, dtype=np.float32)
    vec2[0] = 7.7

    with connect(str(db), readonly=False) as conn:
        store_embedding(conn, track_id, MODEL_CLAMP3, CLAMP3_VEC)
        store_embedding(conn, track_id, MODEL_CLAMP3, vec2)
        conn.commit()

    raw = sqlite3.connect(str(db))
    count = raw.execute(
        "SELECT COUNT(*) FROM embeddings WHERE track_id=? AND model_name=?",
        (track_id, MODEL_CLAMP3),
    ).fetchone()[0]
    row = raw.execute(
        "SELECT vector FROM embeddings WHERE track_id=? AND model_name=?",
        (track_id, MODEL_CLAMP3),
    ).fetchone()
    raw.close()

    assert count == 1
    np.testing.assert_allclose(_blob_to_vec(row[0])[0], 7.7, rtol=1e-4)


# ---------------------------------------------------------------------------
# analyze_embed with --model clamp3
# ---------------------------------------------------------------------------

def _stub_load_clamp3():
    return object(), object(), object(), "cpu"


def _stub_encode_clamp3(path, mm, mp, cm, dev):
    return CLAMP3_VEC.copy()


def test_dry_run_clamp3(db):
    result = analyze_embed(db_path=str(db), apply=False, model="clamp3")
    assert result["mode"] == "dry_run"
    assert result["model"] == MODEL_CLAMP3
    assert result["total_candidates"] > 0
    assert result["succeeded"] == 0


def test_apply_clamp3_stores_768d_embeddings(db, tmp_path):
    with patch("multidj.embed_clamp3.load_clamp3_model", _stub_load_clamp3), \
         patch("multidj.embed_clamp3.encode_audio_clamp3", _stub_encode_clamp3):
        result = analyze_embed(
            db_path=str(db), apply=True, model="clamp3",
            backup_dir=str(tmp_path),
        )

    assert result["mode"] == "apply"
    assert result["succeeded"] > 0
    assert result["model"] == MODEL_CLAMP3

    raw = sqlite3.connect(str(db))
    rows = raw.execute(
        "SELECT vector FROM embeddings WHERE model_name=?", (MODEL_CLAMP3,)
    ).fetchall()
    raw.close()

    assert len(rows) == result["succeeded"]
    for row in rows:
        vec = _blob_to_vec(row[0])
        assert vec.shape == (768,)


def test_clamp3_skips_already_embedded(db, tmp_path):
    """Incremental embedding skips tracks already embedded with the same model."""
    with patch("multidj.embed_clamp3.load_clamp3_model", _stub_load_clamp3), \
         patch("multidj.embed_clamp3.encode_audio_clamp3", _stub_encode_clamp3):
        analyze_embed(db_path=str(db), apply=True, model="clamp3",
                      backup_dir=str(tmp_path))

    result = analyze_embed(db_path=str(db), apply=False, model="clamp3")
    assert result["total_candidates"] == 0


def test_clamp3_force_reembeds(db, tmp_path):
    with patch("multidj.embed_clamp3.load_clamp3_model", _stub_load_clamp3), \
         patch("multidj.embed_clamp3.encode_audio_clamp3", _stub_encode_clamp3):
        analyze_embed(db_path=str(db), apply=True, model="clamp3",
                      backup_dir=str(tmp_path))

    result = analyze_embed(db_path=str(db), apply=False, model="clamp3", force=True)
    assert result["total_candidates"] > 0


def test_clap_and_clamp3_independent_incremental(db, tmp_path):
    """Embedding with CLAP does not mark tracks as embedded for CLAMP3 and vice-versa."""
    clap_vec = np.ones(512, dtype=np.float32) * 0.3

    def stub_load_clap():
        return object(), object(), "cpu"

    def stub_encode_clap(path, m, p, d):
        return clap_vec.copy()

    with patch("multidj.embed.load_clap_model", stub_load_clap), \
         patch("multidj.embed._encode_audio_file", stub_encode_clap):
        analyze_embed(db_path=str(db), apply=True, model="clap",
                      backup_dir=str(tmp_path))

    # All tracks should still be candidates for clamp3
    result = analyze_embed(db_path=str(db), apply=False, model="clamp3")
    assert result["total_candidates"] > 0


def test_per_track_error_isolation_clamp3(db, tmp_path):
    call_count = 0

    def flaky(path, mm, mp, cm, dev):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("mert failure")
        return CLAMP3_VEC.copy()

    with patch("multidj.embed_clamp3.load_clamp3_model", _stub_load_clamp3), \
         patch("multidj.embed_clamp3.encode_audio_clamp3", flaky):
        result = analyze_embed(db_path=str(db), apply=True, model="clamp3",
                               backup_dir=str(tmp_path))

    assert result["errors"] >= 1
    assert result["succeeded"] >= 1


# ---------------------------------------------------------------------------
# MERT feature extraction helpers (unit-level, no real model)
# ---------------------------------------------------------------------------

def test_encode_mert_features_with_clamp3_shape():
    """_encode_mert_features_with_clamp3 returns a 768-dim numpy float32 array."""
    from multidj.embed_clamp3 import _encode_mert_features_with_clamp3

    # Create a mock CLaMP3 model
    mock_model = MagicMock()
    mock_model.device = "cpu"

    import torch

    def fake_get_audio_features(audio_inputs, audio_masks, get_global):
        batch = audio_inputs.shape[0]
        return torch.ones(batch, 768, dtype=torch.float32)

    mock_model.get_audio_features.side_effect = fake_get_audio_features

    # 10 MERT chunks → 10 × 768 features
    mert_feats = np.random.rand(10, 768).astype(np.float32)
    result = _encode_mert_features_with_clamp3(mert_feats, mock_model, "cpu")

    assert result.shape == (768,)
    assert result.dtype == np.float32


def test_encode_mert_features_segmentation():
    """Long MERT sequences (> MAX_AUDIO_LENGTH chunks) are split into segments."""
    from multidj.embed_clamp3 import _encode_mert_features_with_clamp3, _CLAMP3_MAX_AUDIO_LEN

    mock_model = MagicMock()
    mock_model.device = "cpu"

    call_count = 0
    import torch

    def counting_get_audio_features(audio_inputs, audio_masks, get_global):
        nonlocal call_count
        call_count += 1
        return torch.ones(1, 768, dtype=torch.float32)

    mock_model.get_audio_features.side_effect = counting_get_audio_features

    # 200 chunks → 2 segments (128 + 200%128=72 → but last segment = last 128)
    n_chunks = 200
    mert_feats = np.random.rand(n_chunks, 768).astype(np.float32)
    result = _encode_mert_features_with_clamp3(mert_feats, mock_model, "cpu")

    assert result.shape == (768,)
    # With 200+2 sentinel = 202 tokens, ceil(202/128)=2 segments
    assert call_count == 2


# ---------------------------------------------------------------------------
# find_similar with model param
# ---------------------------------------------------------------------------

def test_find_similar_with_clamp3_model(tmp_path):
    db = make_multidj_db(tmp_path / "library.sqlite")

    with connect(str(db), readonly=True) as conn:
        track_rows = conn.execute(
            "SELECT id, path FROM tracks WHERE deleted=0 ORDER BY id"
        ).fetchall()
    track_ids = [r["id"] for r in track_rows]
    track_paths = [r["path"] for r in track_rows]

    query_vec = np.zeros(768, dtype=np.float32)
    query_vec[0] = 1.0

    similar_vec = np.zeros(768, dtype=np.float32)
    similar_vec[0] = 0.9

    dissimilar_vec = np.zeros(768, dtype=np.float32)
    dissimilar_vec[400] = 1.0

    with connect(str(db), readonly=False) as conn:
        store_embedding(conn, track_ids[0], MODEL_CLAMP3, query_vec)
        store_embedding(conn, track_ids[1], MODEL_CLAMP3, similar_vec)
        for tid in track_ids[2:]:
            store_embedding(conn, tid, MODEL_CLAMP3, dissimilar_vec)
        conn.commit()

    result = find_similar(db_path=str(db), track_ref=track_paths[0], top_n=3,
                          model="clamp3")

    assert result["query_track"]["id"] == track_ids[0]
    assert len(result["similar"]) == 3
    assert result["similar"][0]["id"] == track_ids[1]
    assert result["model"] == MODEL_CLAMP3
    distances = [r["distance"] for r in result["similar"]]
    assert distances == sorted(distances)


def test_find_similar_auto_detects_model(tmp_path):
    """With model=None, find_similar uses the most recently stored embedding."""
    db = make_multidj_db(tmp_path / "library.sqlite")

    with connect(str(db), readonly=True) as conn:
        track_rows = conn.execute(
            "SELECT id, path FROM tracks WHERE deleted=0 ORDER BY id"
        ).fetchall()
    track_ids = [r["id"] for r in track_rows]
    track_paths = [r["path"] for r in track_rows]

    vec = np.ones(768, dtype=np.float32) * 0.5

    with connect(str(db), readonly=False) as conn:
        for tid in track_ids:
            store_embedding(conn, tid, MODEL_CLAMP3, vec)
        conn.commit()

    result = find_similar(db_path=str(db), track_ref=track_paths[0], top_n=3, model=None)
    assert result["model"] == MODEL_CLAMP3


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

from multidj.cli import main as cli_main


def test_cli_embed_clamp3_dry_run(db):
    ret = cli_main(["--db", str(db), "analyze", "embed", "--model", "clamp3"])
    assert ret == 0


def test_cli_embed_clamp3_apply(db, tmp_path):
    with patch("multidj.embed_clamp3.load_clamp3_model", _stub_load_clamp3), \
         patch("multidj.embed_clamp3.encode_audio_clamp3", _stub_encode_clamp3):
        ret = cli_main(["--db", str(db), "analyze", "embed", "--apply", "--model", "clamp3"])
    assert ret == 0


def test_cli_cluster_vibe_model_flag_parsed(db):
    """--model flag on cluster vibe is accepted without error (will fail on too-few-tracks)."""
    ret = cli_main(["--db", str(db), "cluster", "vibe", "--model", "clamp3"])
    # Returns 1 because there are no embeddings yet — that's fine, CLI still parsed OK
    assert ret in (0, 1)
