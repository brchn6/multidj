from __future__ import annotations
import numpy as np
import sqlite3
import pytest
from unittest.mock import patch

from tests.fixtures.multidj_factory import make_multidj_db
from multidj.embed import store_embedding, _blob_to_vec, analyze_embed
from multidj.db import connect


@pytest.fixture()
def db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


FIXED_VEC = np.ones(512, dtype=np.float32) * 0.1


def test_store_and_retrieve_embedding(db):
    with connect(str(db), readonly=True) as conn:
        track_id = conn.execute(
            "SELECT id FROM tracks WHERE deleted=0 LIMIT 1"
        ).fetchone()["id"]

    with connect(str(db), readonly=False) as conn:
        store_embedding(conn, track_id, "test-model", FIXED_VEC)
        conn.commit()

    raw = sqlite3.connect(str(db))
    row = raw.execute("SELECT vector, model_name FROM embeddings WHERE track_id=?", (track_id,)).fetchone()
    raw.close()
    assert row is not None
    recovered = _blob_to_vec(row[0])
    assert recovered.shape == (512,)
    np.testing.assert_allclose(recovered, FIXED_VEC, rtol=1e-5)
    assert row[1] == "test-model"


def test_store_embedding_upserts(db):
    """Re-storing the same (track_id, model_name) replaces the vector (upsert)."""
    with connect(str(db), readonly=True) as conn:
        track_id = conn.execute(
            "SELECT id FROM tracks WHERE deleted=0 LIMIT 1"
        ).fetchone()["id"]

    vec2 = np.zeros(512, dtype=np.float32)
    vec2[0] = 9.9

    with connect(str(db), readonly=False) as conn:
        store_embedding(conn, track_id, "model-v1", FIXED_VEC)
        store_embedding(conn, track_id, "model-v1", vec2)  # same model → upsert
        conn.commit()

    raw = sqlite3.connect(str(db))
    count = raw.execute(
        "SELECT COUNT(*) FROM embeddings WHERE track_id=? AND model_name=?",
        (track_id, "model-v1"),
    ).fetchone()[0]
    row = raw.execute(
        "SELECT vector FROM embeddings WHERE track_id=? AND model_name=?",
        (track_id, "model-v1"),
    ).fetchone()
    raw.close()
    assert count == 1  # upsert, not insert
    np.testing.assert_allclose(_blob_to_vec(row[0])[0], 9.9, rtol=1e-4)


def test_two_models_create_two_rows(db):
    """Different model names create separate embedding rows for the same track."""
    with connect(str(db), readonly=True) as conn:
        track_id = conn.execute(
            "SELECT id FROM tracks WHERE deleted=0 LIMIT 1"
        ).fetchone()["id"]

    with connect(str(db), readonly=False) as conn:
        store_embedding(conn, track_id, "model-v1", FIXED_VEC)
        store_embedding(conn, track_id, "model-v2", FIXED_VEC)
        conn.commit()

    raw = sqlite3.connect(str(db))
    count = raw.execute("SELECT COUNT(*) FROM embeddings WHERE track_id=?", (track_id,)).fetchone()[0]
    raw.close()
    assert count == 2


def _stub_load_clap():
    return object(), object(), "cpu"


def _stub_encode(path, model, processor, device):
    return FIXED_VEC.copy()


def test_dry_run_returns_candidate_count(db):
    result = analyze_embed(db_path=str(db), apply=False)
    assert result["mode"] == "dry_run"
    assert result["total_candidates"] > 0
    assert result["processed"] == 0
    assert result["succeeded"] == 0


def test_dry_run_does_not_write(db):
    analyze_embed(db_path=str(db), apply=False)
    raw = sqlite3.connect(str(db))
    count = raw.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    raw.close()
    assert count == 0


def test_apply_stores_embeddings(db, tmp_path):
    with patch("multidj.embed.load_clap_model", _stub_load_clap), \
         patch("multidj.embed._encode_audio_file", _stub_encode):
        result = analyze_embed(db_path=str(db), apply=True, backup_dir=str(tmp_path))
    assert result["mode"] == "apply"
    assert result["succeeded"] > 0
    raw = sqlite3.connect(str(db))
    count = raw.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    raw.close()
    assert count == result["succeeded"]


def test_incremental_skips_already_embedded(db, tmp_path):
    with patch("multidj.embed.load_clap_model", _stub_load_clap), \
         patch("multidj.embed._encode_audio_file", _stub_encode):
        analyze_embed(db_path=str(db), apply=True, backup_dir=str(tmp_path))
    result2 = analyze_embed(db_path=str(db), apply=False)
    assert result2["total_candidates"] == 0


def test_force_re_embeds_existing(db, tmp_path):
    with patch("multidj.embed.load_clap_model", _stub_load_clap), \
         patch("multidj.embed._encode_audio_file", _stub_encode):
        analyze_embed(db_path=str(db), apply=True, backup_dir=str(tmp_path))
    result3 = analyze_embed(db_path=str(db), apply=False, force=True)
    assert result3["total_candidates"] > 0


def test_per_track_error_isolation(db, tmp_path):
    call_count = 0

    def flaky(path, model, processor, device):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("bad audio")
        return FIXED_VEC.copy()

    with patch("multidj.embed.load_clap_model", _stub_load_clap), \
         patch("multidj.embed._encode_audio_file", flaky):
        result = analyze_embed(db_path=str(db), apply=True, backup_dir=str(tmp_path))
    assert result["errors"] >= 1
    assert result["succeeded"] >= 1


def test_limit_restricts_processed(db, tmp_path):
    with patch("multidj.embed.load_clap_model", _stub_load_clap), \
         patch("multidj.embed._encode_audio_file", _stub_encode):
        result = analyze_embed(db_path=str(db), apply=True, limit=1, backup_dir=str(tmp_path))
    assert result["processed"] == 1


from multidj.cli import main as cli_main


def test_cli_analyze_embed_dry_run(db):
    ret = cli_main(["--db", str(db), "analyze", "embed"])
    assert ret == 0


def test_cli_analyze_embed_apply(db, tmp_path):
    with patch("multidj.embed.load_clap_model", _stub_load_clap), \
         patch("multidj.embed._encode_audio_file", _stub_encode):
        ret = cli_main(["--db", str(db), "analyze", "embed", "--apply"])
    assert ret == 0


from multidj.embed import find_similar


def test_find_similar_returns_ordered_results(tmp_path):
    db = make_multidj_db(tmp_path / "library.sqlite")
    with connect(str(db), readonly=True) as conn:
        track_rows = conn.execute(
            "SELECT id, path FROM tracks WHERE deleted=0 ORDER BY id"
        ).fetchall()
    track_ids = [r["id"] for r in track_rows]
    track_paths = [r["path"] for r in track_rows]

    query_vec = np.zeros(512, dtype=np.float32)
    query_vec[0] = 1.0

    similar_vec = np.zeros(512, dtype=np.float32)
    similar_vec[0] = 0.9
    similar_vec[1] = 0.1

    dissimilar_vec = np.zeros(512, dtype=np.float32)
    dissimilar_vec[256] = 1.0

    with connect(str(db), readonly=False) as conn:
        store_embedding(conn, track_ids[0], "test", query_vec)
        store_embedding(conn, track_ids[1], "test", similar_vec)
        for tid in track_ids[2:]:
            store_embedding(conn, tid, "test", dissimilar_vec)
        conn.commit()

    result = find_similar(db_path=str(db), track_ref=track_paths[0], top_n=3)

    assert result["query_track"]["id"] == track_ids[0]
    assert len(result["similar"]) == 3
    assert result["similar"][0]["id"] == track_ids[1]
    distances = [r["distance"] for r in result["similar"]]
    assert distances == sorted(distances)


def test_find_similar_raises_when_no_embedding(db):
    with connect(str(db), readonly=True) as conn:
        path = conn.execute("SELECT path FROM tracks WHERE deleted=0 LIMIT 1").fetchone()["path"]
    with pytest.raises(RuntimeError, match="no embedding"):
        find_similar(db_path=str(db), track_ref=path)
