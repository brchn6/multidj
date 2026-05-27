from __future__ import annotations
import numpy as np
import sqlite3
import pytest
from unittest.mock import patch

from tests.fixtures.multidj_factory import make_multidj_db
from multidj.embed import store_embedding, _blob_to_vec
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
    with connect(str(db), readonly=True) as conn:
        track_id = conn.execute(
            "SELECT id FROM tracks WHERE deleted=0 LIMIT 1"
        ).fetchone()["id"]

    vec2 = np.zeros(512, dtype=np.float32)
    vec2[0] = 9.9

    with connect(str(db), readonly=False) as conn:
        store_embedding(conn, track_id, "model-v1", FIXED_VEC)
        store_embedding(conn, track_id, "model-v2", vec2)
        conn.commit()

    raw = sqlite3.connect(str(db))
    count = raw.execute("SELECT COUNT(*) FROM embeddings WHERE track_id=?", (track_id,)).fetchone()[0]
    row = raw.execute("SELECT vector FROM embeddings WHERE track_id=?", (track_id,)).fetchone()
    raw.close()
    assert count == 1  # upsert, not insert
    np.testing.assert_allclose(_blob_to_vec(row[0])[0], 9.9, rtol=1e-4)
