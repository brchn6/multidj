"""
pytest configuration and shared fixtures for MultiDJ tests.
"""

from __future__ import annotations

import sqlite3

import pytest
from pathlib import Path

from tests.fixtures.mixxx_factory import make_mixxx_db
from tests.fixtures.multidj_factory import make_multidj_db


@pytest.fixture
def mixxx_db(tmp_path) -> Path:
    """Return path to a fresh Mixxx-schema SQLite DB populated with fixture data."""
    return make_mixxx_db(tmp_path / "mixxxdb.sqlite")


@pytest.fixture
def multidj_db(tmp_path) -> Path:
    """Return path to a fresh MultiDJ-schema SQLite DB in post-import state."""
    return make_multidj_db(tmp_path / "library.sqlite")


@pytest.fixture
def multidj_db_conn(multidj_db):
    """Open sqlite3 connection to the multidj_db fixture with Row factory."""
    conn = sqlite3.connect(str(multidj_db))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()
