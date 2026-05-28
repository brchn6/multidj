"""
Comprehensive tests covering every code change made in the 2026-04-30 session:

  1. clean.py   — artist trailing/leading free/dl/download marker removal,
                  empty-bracket cleanup, inline token stripping
  2. analyze.py — detect_bpm_profile multi-window structure + variable BPM reporting
  3. audit.py   — detect_title_artist_swap_mismatch + audit_mismatches
  4. adapters/directory.py — import-time auto-swap counter
  5. cli.py     — audit mismatches subcommand routing
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tests.fixtures.multidj_factory import make_multidj_db


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _insert_track(conn, path, artist, title):
    conn.execute(
        """INSERT INTO tracks (path, artist, title, deleted)
           VALUES (?, ?, ?, 0)""",
        (path, artist, title),
    )
    conn.commit()
    return conn.execute("SELECT id FROM tracks WHERE path=?", (path,)).fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────────
# 1. clean.py — artist noise removal
# ─────────────────────────────────────────────────────────────────────────────

class TestCleanArtistNoise:
    """Unit tests for clean_artist_noise() directly."""

    def test_trailing_free_removed(self):
        from multidj.clean import clean_artist_noise
        assert clean_artist_noise("Some Artist - FREE") == "Some Artist"

    def test_trailing_dl_in_brackets_removed(self):
        from multidj.clean import clean_artist_noise
        assert clean_artist_noise("Another Artist (DL)") == "Another Artist"

    def test_trailing_download_with_colon_removed(self):
        from multidj.clean import clean_artist_noise
        assert clean_artist_noise("Third Artist : download") == "Third Artist"

    def test_leading_free_download_prefix_removed(self):
        from multidj.clean import clean_artist_noise
        assert clean_artist_noise("FREE DOWNLOAD: Artist Name") == "Artist Name"

    def test_leading_free_dl_colon_removed(self):
        from multidj.clean import clean_artist_noise
        assert clean_artist_noise("FREE DL ： Elohay") == "Elohay"

    def test_leading_free_download_fullwidth_colon(self):
        from multidj.clean import clean_artist_noise
        assert clean_artist_noise("Free Download： Assaf Amdurski") == "Assaf Amdurski"

    def test_inline_dl_token_stripped(self):
        from multidj.clean import clean_artist_noise
        result = clean_artist_noise("Artist DL Name")
        assert "dl" not in result.lower()
        assert "Artist" in result

    def test_empty_brackets_cleaned_after_token_removal(self):
        from multidj.clean import clean_artist_noise
        result = clean_artist_noise("Artist (DL)")
        assert "()" not in result
        assert "Artist" in result

    def test_leading_00_prefix_still_removed(self):
        from multidj.clean import clean_artist_noise
        assert clean_artist_noise("00 ABBA") == "ABBA"

    def test_legitimate_name_preserved(self):
        from multidj.clean import clean_artist_noise
        assert clean_artist_noise("The Chemical Brothers") == "The Chemical Brothers"

    def test_double_space_collapsed(self):
        from multidj.clean import clean_artist_noise
        assert clean_artist_noise("Daft  Punk") == "Daft Punk"


class TestCleanTextArtistViaDB:
    """Integration tests running clean_text() against a DB fixture."""

    def test_clean_text_removes_trailing_free_in_artist(self, multidj_db, multidj_db_conn):
        _insert_track(multidj_db_conn, "/x/t1.mp3", "Genie In A Bottle (Edit) FREE DL", "Some Title")
        from multidj.clean import clean_text
        clean_text(str(multidj_db), apply=True, backup=False)
        row = multidj_db_conn.execute(
            "SELECT artist FROM tracks WHERE path='/x/t1.mp3'"
        ).fetchone()
        assert "free" not in row["artist"].lower()
        assert "dl" not in row["artist"].lower()

    def test_clean_text_removes_leading_free_download_prefix(self, multidj_db, multidj_db_conn):
        _insert_track(multidj_db_conn, "/x/t2.mp3", "FREE DOWNLOAD: Real Artist", "Track")
        from multidj.clean import clean_text
        clean_text(str(multidj_db), apply=True, backup=False)
        row = multidj_db_conn.execute(
            "SELECT artist FROM tracks WHERE path='/x/t2.mp3'"
        ).fetchone()
        assert row["artist"] == "Real Artist"

    def test_clean_text_preserves_unrelated_artist(self, multidj_db, multidj_db_conn):
        _insert_track(multidj_db_conn, "/x/t3.mp3", "David Broza", "Track")
        from multidj.clean import clean_text
        clean_text(str(multidj_db), apply=True, backup=False)
        row = multidj_db_conn.execute(
            "SELECT artist FROM tracks WHERE path='/x/t3.mp3'"
        ).fetchone()
        assert row["artist"] == "David Broza"


# ─────────────────────────────────────────────────────────────────────────────
# 2. analyze.py — detect_bpm_profile structure and variable BPM flag
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectBpmProfile:

    def _make_fake_librosa(self, samples: list[float], duration: float = 120.0):
        """Return a context-managed mock for librosa that yields the given per-window BPM samples."""
        sample_iter = iter(samples)

        import numpy as np

        def fake_beat_track(y=None, sr=None):
            bpm_val = next(sample_iter)
            return np.array([bpm_val]), None

        librosa_mock = MagicMock()
        librosa_mock.get_duration.return_value = duration
        librosa_mock.load.return_value = (MagicMock(), 22050)
        librosa_mock.beat.beat_track.side_effect = fake_beat_track
        return librosa_mock

    def test_profile_returns_required_keys(self, tmp_path):
        fake_audio = tmp_path / "track.mp3"
        fake_audio.write_bytes(b"")

        lm = self._make_fake_librosa([128.0, 128.0, 128.0])
        with patch.dict("sys.modules", {"librosa": lm}):
            from importlib import reload
            import multidj.analyze as m
            reload(m)
            result = m.detect_bpm_profile(str(fake_audio))

        assert "bpm" in result
        assert "bpm_samples" in result
        assert "bpm_range" in result
        assert "is_variable" in result
        assert "sample_offsets" in result

    def test_stable_tempo_not_variable(self, tmp_path):
        fake_audio = tmp_path / "track.mp3"
        fake_audio.write_bytes(b"")

        lm = self._make_fake_librosa([128.0, 128.0, 128.0])
        with patch.dict("sys.modules", {"librosa": lm}):
            from importlib import reload
            import multidj.analyze as m
            reload(m)
            result = m.detect_bpm_profile(str(fake_audio))

        assert result["is_variable"] is False

    def test_variable_tempo_flagged(self, tmp_path):
        fake_audio = tmp_path / "track.mp3"
        fake_audio.write_bytes(b"")

        # Large BPM spread between windows → should be flagged as variable
        lm = self._make_fake_librosa([80.0, 120.0, 160.0])
        with patch.dict("sys.modules", {"librosa": lm}):
            from importlib import reload
            import multidj.analyze as m
            reload(m)
            result = m.detect_bpm_profile(str(fake_audio))

        assert result["is_variable"] is True
        assert result["bpm_range"] > 0


class TestAnalyzeBpmVariableReport:
    """analyze_bpm() should record variable-BPM tracks in output."""

    def test_variable_bpm_reported(self, multidj_db):
        conn = sqlite3.connect(str(multidj_db))
        conn.execute("UPDATE tracks SET bpm = 0 WHERE id = 1")
        conn.commit()
        conn.close()

        from multidj.analyze import analyze_bpm
        with patch(
            "multidj.analyze.detect_bpm_profile",
            return_value={
                "bpm": 90.0,
                "bpm_samples": [80.0, 90.0, 100.0],
                "sample_offsets": [0.0, 45.0, 90.0],
                "bpm_range": 20.0,
                "is_variable": True,
            },
        ):
            result = analyze_bpm(str(multidj_db), apply=True, backup_dir=False)

        assert result["mode"] == "apply"
        assert result["variable_bpm_tracks"] >= 1
        variable_ids = [r["track_id"] for r in result.get("variable_bpm_details", [])]
        assert 1 in variable_ids

    def test_stable_bpm_not_in_variable_list(self, multidj_db):
        conn = sqlite3.connect(str(multidj_db))
        conn.execute("UPDATE tracks SET bpm = 0 WHERE id = 1")
        conn.commit()
        conn.close()

        from multidj.analyze import analyze_bpm
        with patch(
            "multidj.analyze.detect_bpm_profile",
            return_value={
                "bpm": 128.0,
                "bpm_samples": [128.0, 128.0, 128.0],
                "sample_offsets": [0.0, 30.0, 60.0],
                "bpm_range": 0.0,
                "is_variable": False,
            },
        ):
            result = analyze_bpm(str(multidj_db), apply=True, backup_dir=False)

        assert result["variable_bpm_tracks"] == 0
        variable_ids = [r["track_id"] for r in result.get("variable_bpm_details", [])]
        assert 1 not in variable_ids


# ─────────────────────────────────────────────────────────────────────────────
# 3. audit.py — detect_title_artist_swap_mismatch + audit_mismatches
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectTitleArtistSwapMismatch:

    def test_detects_swapped_pair(self):
        from multidj.audit import detect_title_artist_swap_mismatch
        result = detect_title_artist_swap_mismatch(
            "/music/Somebody That I Used to Know - Gotye.mp3",
            artist="Somebody That I Used to Know",
            title="Gotye",
        )
        assert result is not None
        assert result["suggested_artist"] == "Gotye"
        assert result["suggested_title"] == "Somebody That I Used to Know"

    def test_no_mismatch_when_correct(self):
        # Filename: "Title - Artist" convention with correctly assigned tags (artist=2nd part)
        from multidj.audit import detect_title_artist_swap_mismatch
        result = detect_title_artist_swap_mismatch(
            "/music/Somebody That I Used to Know - Gotye.mp3",
            artist="Gotye",   # matches group 2 — correct
            title="Somebody That I Used to Know",  # matches group 1 — correct
        )
        assert result is None

    def test_no_mismatch_when_no_dash_in_filename(self):
        from multidj.audit import detect_title_artist_swap_mismatch
        result = detect_title_artist_swap_mismatch(
            "/music/SomeTrackNoSeparator.mp3",
            artist="Artist",
            title="Title",
        )
        assert result is None

    def test_none_artist_returns_none(self):
        from multidj.audit import detect_title_artist_swap_mismatch
        result = detect_title_artist_swap_mismatch(
            "/music/Title - Artist.mp3",
            artist=None,
            title="Artist",
        )
        assert result is None

    def test_case_insensitive_match(self):
        from multidj.audit import detect_title_artist_swap_mismatch
        result = detect_title_artist_swap_mismatch(
            "/music/TRACK NAME - ARTIST NAME.mp3",
            artist="track name",
            title="artist name",
        )
        assert result is not None


class TestAuditMismatches:

    def test_finds_swapped_row(self, multidj_db, multidj_db_conn):
        _insert_track(
            multidj_db_conn,
            "/music/Blue Monday - New Order.mp3",
            "Blue Monday",
            "New Order",
        )
        from multidj.audit import audit_mismatches
        result = audit_mismatches(str(multidj_db))
        paths = [m["path"] for m in result["mismatches"]]
        assert "/music/Blue Monday - New Order.mp3" in paths

    def test_no_false_positives_for_correct_metadata(self, multidj_db, multidj_db_conn):
        # Filename follows "Title - Artist" convention; tags are correctly assigned
        _insert_track(
            multidj_db_conn,
            "/music/Blue Monday - New Order.mp3",
            "New Order",   # artist = 2nd filename part
            "Blue Monday",  # title  = 1st filename part
        )
        from multidj.audit import audit_mismatches
        result = audit_mismatches(str(multidj_db))
        paths = [m["path"] for m in result["mismatches"]]
        assert "/music/Blue Monday - New Order.mp3" not in paths

    def test_returns_suggested_fields(self, multidj_db, multidj_db_conn):
        _insert_track(
            multidj_db_conn,
            "/music/Blue Monday - New Order.mp3",
            "Blue Monday",
            "New Order",
        )
        from multidj.audit import audit_mismatches
        result = audit_mismatches(str(multidj_db))
        m = next(x for x in result["mismatches"] if "Blue Monday - New Order" in x["path"])
        assert m["suggested_artist"] == "New Order"
        assert m["suggested_title"] == "Blue Monday"

    def test_limit_parameter_respected(self, multidj_db, multidj_db_conn):
        for i in range(5):
            _insert_track(
                multidj_db_conn,
                f"/music/Track {i} - Artist {i}.mp3",
                f"Track {i}",
                f"Artist {i}",
            )
        from multidj.audit import audit_mismatches
        result = audit_mismatches(str(multidj_db), limit=2)
        assert len(result["mismatches"]) <= 2

    def test_total_candidates_count(self, multidj_db, multidj_db_conn):
        from multidj.audit import audit_mismatches
        result = audit_mismatches(str(multidj_db))
        assert result["total_candidates"] >= 0
        assert result["total_mismatches"] <= result["total_candidates"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. adapters/directory.py — import-time auto-swap counter
# ─────────────────────────────────────────────────────────────────────────────

class TestDirectoryImportAutoSwap:

    def _make_fake_tags(self, artist, title):
        fake = MagicMock()
        fake.get.side_effect = lambda k, d=None: {
            "artist": [artist],
            "title": [title],
            "album": [],
            "genre": ["House"],
            "bpm": ["128.0"],
        }.get(k, d or [])
        fake.info.length = 300.0
        return fake

    def test_auto_swap_increments_counter(self, tmp_path):
        """When filename is Title - Artist but tags have them swapped, counter should be 1."""
        db_path = make_multidj_db(tmp_path / "lib.sqlite")
        audio_dir = tmp_path / "music"
        audio_dir.mkdir()
        # filename: "Blue Monday - New Order" → title=Blue Monday, artist=New Order
        track_file = audio_dir / "Blue Monday - New Order.mp3"
        track_file.write_bytes(b"")

        # Tags have artist/title reversed: artist=Blue Monday, title=New Order
        fake = self._make_fake_tags("Blue Monday", "New Order")

        from multidj.adapters.directory import DirectoryAdapter
        adapter = DirectoryAdapter()

        with patch("multidj.adapters.directory.MutagenFile", return_value=fake):
            result = adapter.import_all(
                multidj_db_path=db_path,
                paths=[str(audio_dir)],
                apply=True,
            )

        assert result["auto_swapped_artist_title"] == 1

    def test_no_swap_when_correct(self, tmp_path):
        db_path = make_multidj_db(tmp_path / "lib.sqlite")
        audio_dir = tmp_path / "music"
        audio_dir.mkdir()
        # filename: "Blue Monday - New Order" (Title - Artist convention)
        # tags: artist=New Order (group2), title=Blue Monday (group1) → no swap needed
        track_file = audio_dir / "Blue Monday - New Order.mp3"
        track_file.write_bytes(b"")

        fake = self._make_fake_tags("New Order", "Blue Monday")

        from multidj.adapters.directory import DirectoryAdapter
        adapter = DirectoryAdapter()

        with patch("multidj.adapters.directory.MutagenFile", return_value=fake):
            result = adapter.import_all(
                multidj_db_path=db_path,
                paths=[str(audio_dir)],
                apply=True,
            )

        assert result["auto_swapped_artist_title"] == 0

    def test_swapped_values_written_to_db(self, tmp_path):
        """After auto-swap, the DB should contain the corrected artist/title."""
        db_path = make_multidj_db(tmp_path / "lib.sqlite")
        audio_dir = tmp_path / "music"
        audio_dir.mkdir()
        track_file = audio_dir / "Blue Monday - New Order.mp3"
        track_file.write_bytes(b"")

        fake = self._make_fake_tags("Blue Monday", "New Order")

        from multidj.adapters.directory import DirectoryAdapter
        adapter = DirectoryAdapter()

        with patch("multidj.adapters.directory.MutagenFile", return_value=fake):
            adapter.import_all(
                multidj_db_path=db_path,
                paths=[str(audio_dir)],
                apply=True,
            )

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT artist, title FROM tracks WHERE path=?", (str(track_file),)
        ).fetchone()
        conn.close()
        assert row[0] == "New Order"
        assert row[1] == "Blue Monday"


# ─────────────────────────────────────────────────────────────────────────────
# 5. cli.py — audit mismatches subcommand routing
# ─────────────────────────────────────────────────────────────────────────────

class TestCLIAuditMismatches:

    def test_audit_mismatches_subcommand_exists(self, multidj_db, multidj_db_conn):
        _insert_track(
            multidj_db_conn,
            "/music/Track - Artist.mp3",
            "Track",
            "Artist",
        )
        from multidj.cli import main
        rc = main(["--db", str(multidj_db), "--json", "audit", "mismatches"])
        assert rc == 0

    def test_audit_mismatches_limit_flag(self, multidj_db):
        from multidj.cli import main
        # should not raise
        rc = main(["--db", str(multidj_db), "--json", "audit", "mismatches", "--limit", "5"])
        assert rc == 0

    def test_audit_mismatches_json_output(self, multidj_db, multidj_db_conn, capsys):
        _insert_track(
            multidj_db_conn,
            "/music/Blue Monday - New Order.mp3",
            "Blue Monday",
            "New Order",
        )
        from multidj.cli import main
        main(["--db", str(multidj_db), "--json", "audit", "mismatches"])
        out = capsys.readouterr().out
        import json
        data = json.loads(out)
        assert "mismatches" in data
        assert "total_mismatches" in data
