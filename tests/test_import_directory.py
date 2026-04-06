from __future__ import annotations
import os
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from tests.fixtures.multidj_factory import make_multidj_db


def test_directory_import_dry_run(tmp_path):
    """Dry-run returns found tracks without writing to DB."""
    db_path = tmp_path / "library.sqlite"
    audio_dir = tmp_path / "music"
    audio_dir.mkdir()
    (audio_dir / "01_Artist_-_Title.mp3").write_bytes(b"")

    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda k, d=None: {
        "artist": ["Test Artist"],
        "title": ["Test Title"],
        "album": [],
        "genre": ["House"],
        "bpm": ["128.0"],
    }.get(k, d or [])
    fake_tags.info.length = 240.0

    from multidj.adapters.directory import DirectoryAdapter
    adapter = DirectoryAdapter()

    with patch("multidj.adapters.directory.MutagenFile", return_value=fake_tags):
        result = adapter.import_all(
            multidj_db_path=db_path,
            paths=[str(audio_dir)],
            apply=False,
        )

    assert result["mode"] == "dry_run"
    assert result["total_found"] == 1
    assert not db_path.exists()


def test_directory_import_apply_inserts_tracks(tmp_path):
    """Apply mode inserts discovered tracks into the DB."""
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    audio_dir = tmp_path / "music"
    audio_dir.mkdir()
    track_path = audio_dir / "Artist_-_Title.mp3"
    track_path.write_bytes(b"")

    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda k, d=None: {
        "artist": ["New Artist"],
        "title": ["New Title"],
        "album": [],
        "genre": ["Techno"],
        "bpm": ["135.0"],
    }.get(k, d or [])
    fake_tags.info.length = 300.0

    from multidj.adapters.directory import DirectoryAdapter
    adapter = DirectoryAdapter()

    with patch("multidj.adapters.directory.MutagenFile", return_value=fake_tags):
        result = adapter.import_all(
            multidj_db_path=db_path,
            paths=[str(audio_dir)],
            apply=True,
        )

    assert result["mode"] == "apply"
    assert result["new_tracks"] >= 1

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT artist, title, genre, bpm FROM tracks WHERE path = ?",
        (str(track_path),),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "New Artist"
    assert row[2] == "Techno"
    assert row[3] == pytest.approx(135.0)


def test_directory_import_idempotent(tmp_path):
    """Running import twice does not create duplicate tracks."""
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    audio_dir = tmp_path / "music"
    audio_dir.mkdir()
    (audio_dir / "track.mp3").write_bytes(b"")

    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda k, d=None: {"artist": ["X"], "title": ["Y"]}.get(k, d or [])
    fake_tags.info.length = 200.0

    from multidj.adapters.directory import DirectoryAdapter
    adapter = DirectoryAdapter()

    with patch("multidj.adapters.directory.MutagenFile", return_value=fake_tags):
        adapter.import_all(multidj_db_path=db_path, paths=[str(audio_dir)], apply=True)
        result2 = adapter.import_all(multidj_db_path=db_path, paths=[str(audio_dir)], apply=True)

    assert result2["new_tracks"] == 0
    assert result2["unchanged_tracks"] == 1

    track_file = str(audio_dir / "track.mp3")
    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM tracks WHERE path = ?", (track_file,)).fetchone()[0]
    conn.close()
    assert count == 1


def test_directory_import_skips_unsupported_extensions(tmp_path):
    """Files with non-audio extensions are ignored."""
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "cover.jpg").write_bytes(b"")
    (music_dir / "notes.txt").write_bytes(b"")
    (music_dir / "track.mp3").write_bytes(b"")

    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda k, d=None: []
    fake_tags.info.length = 100.0

    from multidj.adapters.directory import DirectoryAdapter
    adapter = DirectoryAdapter()

    with patch("multidj.adapters.directory.MutagenFile", return_value=fake_tags):
        result = adapter.import_all(multidj_db_path=db_path, paths=[str(music_dir)], apply=True)

    assert result["total_found"] == 1


def test_directory_import_recurses_subdirectories(tmp_path):
    """Subdirectories are walked recursively."""
    db_path = make_multidj_db(tmp_path / "library.sqlite")
    deep = tmp_path / "music" / "house" / "2024"
    deep.mkdir(parents=True)
    (deep / "track.flac").write_bytes(b"")

    fake_tags = MagicMock()
    fake_tags.get.side_effect = lambda k, d=None: []
    fake_tags.info.length = 180.0

    from multidj.adapters.directory import DirectoryAdapter
    adapter = DirectoryAdapter()

    with patch("multidj.adapters.directory.MutagenFile", return_value=fake_tags):
        result = adapter.import_all(multidj_db_path=db_path, paths=[str(tmp_path / "music")], apply=True)

    assert result["total_found"] == 1
