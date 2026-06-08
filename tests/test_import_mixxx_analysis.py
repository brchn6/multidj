"""Tests for import_mixxx_analysis() — reading Mixxx analysis into MultiDJ."""

from __future__ import annotations

import sqlite3


class TestImportMixxxAnalysis:
    """Tests for importing Mixxx BPM/key analysis into MultiDJ tracks."""

    def test_dry_run_reports_candidates(self, mixxx_db, multidj_db):
        """Dry-run lists tracks from Mixxx that have BPM values."""
        from multidj.import_mixxx_analysis import import_mixxx_analysis

        result = import_mixxx_analysis(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=False,
        )
        assert result["status"] == "ok"
        # 9 active tracks in Mixxx fixture, all have BPM values
        assert result["total_candidates"] == 9
        assert result["applied"] is False

    def test_dry_run_no_write(self, mixxx_db, multidj_db):
        """Dry-run must not modify the MultiDJ DB."""
        from multidj.import_mixxx_analysis import import_mixxx_analysis

        # First, ensure some tracks have no BPM (reset them)
        conn = sqlite3.connect(str(multidj_db))
        conn.execute("UPDATE tracks SET bpm = NULL WHERE id IN (2, 3, 5, 7, 9)")
        conn.commit()
        conn.close()

        result = import_mixxx_analysis(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=False,
        )

        assert result["applied"] is False
        # Verify no BPM was written
        conn = sqlite3.connect(str(multidj_db))
        null_count = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE bpm IS NULL"
        ).fetchone()[0]
        conn.close()
        assert null_count >= 5, "Dry-run should not have written any BPM values"

    def test_apply_writes_bpm(self, mixxx_db, multidj_db):
        """Apply mode writes Mixxx BPM into MultiDJ tracks."""
        from multidj.import_mixxx_analysis import import_mixxx_analysis

        # Reset some tracks to have no BPM
        conn = sqlite3.connect(str(multidj_db))
        conn.execute("UPDATE tracks SET bpm = NULL WHERE id IN (2, 5, 7)")
        conn.commit()
        conn.close()

        result = import_mixxx_analysis(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=True,
            backup_dir=False,
        )

        assert result["status"] == "ok"
        assert result["applied"] is True
        assert result["written_bpm"] >= 3  # At least our 3 reset tracks

        # Verify BPM was written
        conn = sqlite3.connect(str(multidj_db))
        for track_id in [2, 5, 7]:
            row = conn.execute(
                "SELECT bpm FROM tracks WHERE id = ?", (track_id,)
            ).fetchone()
            assert row is not None, f"Track {track_id} not found"
            assert row[0] is not None, f"Track {track_id} BPM still NULL"
            assert row[0] > 0, f"Track {track_id} BPM is zero"
        conn.close()

    def test_force_overwrites_existing_bpm(self, mixxx_db, multidj_db):
        """--force overwrites tracks that already have BPM values."""
        from multidj.import_mixxx_analysis import import_mixxx_analysis

        # Track 1 has BPM=128.0 in both fixtures, but Mixxx might have different
        # value. We'll just verify force writes.
        result = import_mixxx_analysis(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=True,
            force=True,
            backup_dir=False,
            limit=1,
        )

        assert result["status"] == "ok"
        assert result["written_bpm"] == 1  # Should overwrite even if had BPM

    def test_skip_tracks_already_have_bpm(self, mixxx_db, multidj_db):
        """By default, tracks that already have BPM are skipped."""
        from multidj.import_mixxx_analysis import import_mixxx_analysis

        # All fixture tracks already have BPM from the initial import
        result = import_mixxx_analysis(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=True,
            force=False,
            backup_dir=False,
        )

        assert result["status"] == "ok"
        assert result["written_bpm"] == 0  # Everything already has BPM
        assert result["skipped_has_bpm"] == 9  # All 9 tracks already set

    def test_limit_caps_processing(self, mixxx_db, multidj_db):
        """--limit restricts the number of tracks processed."""
        from multidj.import_mixxx_analysis import import_mixxx_analysis

        conn = sqlite3.connect(str(multidj_db))
        conn.execute("UPDATE tracks SET bpm = NULL")  # Reset all BPM
        conn.commit()
        conn.close()

        result = import_mixxx_analysis(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=True,
            limit=3,
            backup_dir=False,
        )

        assert result["total_candidates"] == 3
        assert result["written_bpm"] == 3

    def test_no_mixxx_db_found(self, multidj_db, tmp_path):
        """Graceful error when Mixxx DB doesn't exist."""
        from multidj.import_mixxx_analysis import import_mixxx_analysis

        non_existent = tmp_path / "nonexistent.sqlite"
        result = import_mixxx_analysis(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(non_existent),
            apply=False,
        )

        assert result["status"] == "error"
        assert "not found" in result["reason"].lower()

    def test_import_writes_key(self, mixxx_db, multidj_db):
        """Key values from Mixxx are also written to MultiDJ when present."""
        from multidj.import_mixxx_analysis import import_mixxx_analysis

        # Populate the key text column in the Mixxx fixture (it only has key_id by default)
        conn = sqlite3.connect(str(mixxx_db))
        conn.execute("UPDATE library SET key = '8B' WHERE id = 1")
        conn.execute("UPDATE library SET key = '9A' WHERE id = 6")
        conn.commit()
        conn.close()

        # Reset keys on all tracks in MultiDJ
        conn = sqlite3.connect(str(multidj_db))
        conn.execute("UPDATE tracks SET key = NULL")
        conn.commit()
        conn.close()

        result = import_mixxx_analysis(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=True,
            backup_dir=False,
        )

        assert result["written_key"] >= 1  # At least the tracks with key set

    def test_mode_field_correct(self, mixxx_db, multidj_db):
        """Result mode field reflects apply state."""
        from multidj.import_mixxx_analysis import import_mixxx_analysis

        dry = import_mixxx_analysis(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=False,
        )
        assert dry["mode"] == "dry_run"

        apply_result = import_mixxx_analysis(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=True,
            backup_dir=False,
        )
        assert apply_result["mode"] == "apply"
