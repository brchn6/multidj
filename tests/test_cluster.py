from __future__ import annotations
import numpy as np
import sqlite3
import pytest
from unittest.mock import patch

from tests.fixtures.multidj_factory import make_multidj_db
from multidj.embed import store_embedding
from multidj.cluster import cluster_embeddings, cluster_vibe
from multidj.db import connect


@pytest.fixture()
def db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


@pytest.fixture()
def db_with_embeddings(tmp_path):
    db = make_multidj_db(tmp_path / "library.sqlite")
    with connect(str(db), readonly=True) as conn:
        track_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM tracks WHERE deleted=0 ORDER BY id"
        ).fetchall()]

    half = len(track_ids) // 2
    with connect(str(db), readonly=False) as conn:
        for i, tid in enumerate(track_ids):
            vec = np.zeros(512, dtype=np.float32)
            if i < half:
                vec[0] = 1.0    # cluster A: all energy in dim 0
            else:
                vec[256] = 1.0  # cluster B: all energy in dim 256
            store_embedding(conn, tid, "test-model", vec)
        conn.commit()
    return db, track_ids


def test_cluster_embeddings_returns_labels(db_with_embeddings):
    db, track_ids = db_with_embeddings
    with connect(str(db), readonly=True) as conn:
        from multidj.embed import load_embeddings_from_db
        _, vectors = load_embeddings_from_db(conn)
    labels = cluster_embeddings(vectors, min_cluster_size=2)
    assert labels.shape == (len(track_ids),)
    assert any(l >= 0 for l in labels)


def test_too_few_tracks_raises(db):
    with pytest.raises(RuntimeError, match="Too few embedded tracks"):
        cluster_vibe(db_path=str(db), apply=False, min_cluster_size=5)


from multidj.cluster import name_cluster


def test_name_cluster_calls_llm():
    samples = [
        {"artist": "Ben Klock", "title": "Subzero", "genre": "Techno", "bpm": 135, "key": "6A"},
        {"artist": "Blawan", "title": "Getting Me Down", "genre": "Techno", "bpm": 136, "key": "8A"},
    ]
    llm_config = {"base_url": "http://fake", "api_key": "test", "model": "fake-model"}

    from unittest.mock import MagicMock

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = " Dark Techno "

    with patch("multidj.cluster.OpenAI", return_value=mock_client):
        result = name_cluster(samples, llm_config)

    assert result == "Dark Techno"
    mock_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_client.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "fake-model"
    assert call_kwargs["max_tokens"] == 20


def test_dry_run_returns_cluster_info(db_with_embeddings):
    db, _ = db_with_embeddings
    result = cluster_vibe(db_path=str(db), apply=False, min_cluster_size=2)
    assert result["mode"] == "dry_run"
    assert result["clusters_found"] >= 1
    assert result["crates_written"] == 0
    assert isinstance(result["clusters"], list)


def test_apply_creates_vibe_crates(db_with_embeddings, tmp_path):
    db, _ = db_with_embeddings
    result = cluster_vibe(db_path=str(db), apply=True, min_cluster_size=2, backup_dir=str(tmp_path))
    assert result["mode"] == "apply"
    assert result["crates_written"] >= 1
    raw = sqlite3.connect(str(db))
    count = raw.execute("SELECT COUNT(*) FROM crates WHERE name LIKE 'Vibe/%'").fetchone()[0]
    raw.close()
    assert count >= 1


def test_rebuild_clears_stale_crates(db_with_embeddings, tmp_path):
    db, _ = db_with_embeddings
    cluster_vibe(db_path=str(db), apply=True, min_cluster_size=2, backup_dir=str(tmp_path))
    cluster_vibe(db_path=str(db), apply=True, min_cluster_size=2, backup_dir=str(tmp_path))
    raw = sqlite3.connect(str(db))
    total = raw.execute("SELECT COUNT(*) FROM crates WHERE name LIKE 'Vibe/%'").fetchone()[0]
    unique = raw.execute("SELECT COUNT(DISTINCT name) FROM crates WHERE name LIKE 'Vibe/%'").fetchone()[0]
    raw.close()
    assert total == unique


def test_llm_naming_applied_when_config_present(db_with_embeddings, tmp_path):
    db, _ = db_with_embeddings
    llm_config = {"base_url": "http://fake", "api_key": "fake", "model": "fake"}
    with patch("multidj.cluster.name_cluster", return_value="Dark Techno Peaks"):
        result = cluster_vibe(
            db_path=str(db), apply=True, min_cluster_size=2,
            llm_config=llm_config, backup_dir=str(tmp_path),
        )
    raw = sqlite3.connect(str(db))
    names = [r[0] for r in raw.execute(
        "SELECT name FROM crates WHERE name LIKE 'Vibe/%' AND name != 'Vibe/Unclassified'"
    ).fetchall()]
    raw.close()
    assert any("Dark Techno Peaks" in n for n in names)


def test_numbered_fallback_when_no_llm(db_with_embeddings, tmp_path):
    db, _ = db_with_embeddings
    result = cluster_vibe(db_path=str(db), apply=True, min_cluster_size=2, backup_dir=str(tmp_path))
    raw = sqlite3.connect(str(db))
    names = [r[0] for r in raw.execute("SELECT name FROM crates WHERE name LIKE 'Vibe/%'").fetchall()]
    raw.close()
    for n in names:
        assert n.startswith("Vibe/")


from multidj.cli import main as cli_main


def test_cli_cluster_vibe_too_few_tracks(db):
    ret = cli_main(["--db", str(db), "cluster", "vibe"])
    assert ret == 1


def test_cli_cluster_vibe_dry_run(db_with_embeddings):
    db, _ = db_with_embeddings
    ret = cli_main(["--db", str(db), "cluster", "vibe", "--min-cluster-size", "2"])
    assert ret == 0
