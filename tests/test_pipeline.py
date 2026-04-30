from __future__ import annotations
import sqlite3
import pytest
from unittest.mock import patch
from tests.fixtures.multidj_factory import make_multidj_db
from tests.fixtures.mixxx_factory import make_mixxx_db
from multidj.pipeline import run_pipeline
from multidj.config import DEFAULT_CONFIG
import copy


@pytest.fixture()
def multidj_db(tmp_path):
    return make_multidj_db(tmp_path / "library.sqlite")


@pytest.fixture()
def mixxx_db(tmp_path):
    return make_mixxx_db(tmp_path / "mixxxdb.sqlite")


@pytest.fixture()
def cfg():
    return copy.deepcopy(DEFAULT_CONFIG)


def test_dry_run_returns_step_summaries(multidj_db, mixxx_db, cfg, tmp_path):
    result = run_pipeline(
        db_path=str(multidj_db),
        mixxx_db_path=str(mixxx_db),
        cfg=cfg,
        apply=False,
        music_dir=str(tmp_path),
    )
    assert result["mode"] == "dry_run"
    assert "steps" in result
    assert len(result["steps"]) == 10
    step_names = [s["step"] for s in result["steps"]]
    assert "fix_mismatches" in step_names
    assert "clean_text" in step_names


def test_apply_creates_single_backup(multidj_db, mixxx_db, cfg, tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    with patch("multidj.pipeline.analyze_bpm") as mock_bpm, \
         patch("multidj.pipeline.analyze_key") as mock_key, \
         patch("multidj.pipeline.analyze_energy") as mock_energy:
        mock_bpm.return_value = {"succeeded": 0, "errors": 0}
        mock_key.return_value = {"succeeded": 0, "errors": 0}
        mock_energy.return_value = {"succeeded": 0, "errors": 0}

        run_pipeline(
            db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            cfg=cfg,
            apply=True,
            music_dir=str(tmp_path / "music"),
            backup_dir=str(backup_dir),
        )

    backups = list(backup_dir.glob("*.sqlite*"))
    assert len(backups) == 1


def test_skip_flag_omits_step(multidj_db, mixxx_db, cfg, tmp_path):
    with patch("multidj.pipeline.analyze_energy") as mock_energy:
        run_pipeline(
            db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            cfg=cfg,
            apply=False,
            music_dir=str(tmp_path),
            skip={"energy"},
        )
        mock_energy.assert_not_called()


def test_step_failure_does_not_abort_pipeline(multidj_db, mixxx_db, cfg, tmp_path):
    with patch("multidj.pipeline.analyze_bpm", side_effect=RuntimeError("bpm failed")), \
         patch("multidj.pipeline.analyze_key") as mock_key, \
         patch("multidj.pipeline.analyze_energy") as mock_energy:
        mock_key.return_value = {"succeeded": 0, "errors": 0}
        mock_energy.return_value = {"succeeded": 0, "errors": 0}

        result = run_pipeline(
            db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            cfg=cfg,
            apply=False,
            music_dir=str(tmp_path),
        )

    step_names = [s["step"] for s in result["steps"]]
    assert "bpm" in step_names
    assert "key" in step_names
    bpm_step = next(s for s in result["steps"] if s["step"] == "bpm")
    assert bpm_step["status"] == "error"


def test_config_disables_energy_step(multidj_db, mixxx_db, cfg, tmp_path):
    cfg["crates"]["energy"] = False
    with patch("multidj.pipeline.analyze_energy") as mock_energy:
        run_pipeline(
            db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            cfg=cfg,
            apply=False,
            music_dir=str(tmp_path),
        )
        mock_energy.assert_not_called()


def test_pipeline_fix_mismatches_step_runs(multidj_db, mixxx_db, cfg, tmp_path):
    """fix_mismatches step should appear in pipeline result and run without error."""
    result = run_pipeline(
        db_path=str(multidj_db),
        mixxx_db_path=str(mixxx_db),
        cfg=cfg,
        apply=False,
        music_dir=str(tmp_path),
    )
    fix_step = next(s for s in result["steps"] if s["step"] == "fix_mismatches")
    assert fix_step["status"] == "ok"
    assert "total_fixed" in fix_step["result"]


def test_pipeline_clean_text_step_runs(multidj_db, mixxx_db, cfg, tmp_path):
    """clean_text step should appear in pipeline result and run without error."""
    result = run_pipeline(
        db_path=str(multidj_db),
        mixxx_db_path=str(mixxx_db),
        cfg=cfg,
        apply=False,
        music_dir=str(tmp_path),
    )
    clean_step = next(s for s in result["steps"] if s["step"] == "clean_text")
    assert clean_step["status"] == "ok"


def test_pipeline_skip_fix_mismatches(multidj_db, mixxx_db, cfg, tmp_path):
    result = run_pipeline(
        db_path=str(multidj_db),
        mixxx_db_path=str(mixxx_db),
        cfg=cfg,
        apply=False,
        music_dir=str(tmp_path),
        skip={"fix_mismatches"},
    )
    fix_step = next(s for s in result["steps"] if s["step"] == "fix_mismatches")
    assert fix_step["status"] == "skipped"


def test_pipeline_fix_mismatches_corrects_swapped_rows(multidj_db, mixxx_db, cfg, tmp_path):
    """With apply=True, swapped artist/title rows should be corrected after pipeline runs."""
    conn = sqlite3.connect(str(multidj_db))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        INSERT INTO tracks (path, artist, title, deleted)
        VALUES ('/music/Blue Monday - New Order.mp3', 'Blue Monday', 'New Order', 0)
    """)
    conn.commit()
    conn.close()

    with patch("multidj.pipeline.analyze_bpm") as m_bpm, \
         patch("multidj.pipeline.analyze_key") as m_key, \
         patch("multidj.pipeline.analyze_energy") as m_energy:
        m_bpm.return_value = {"succeeded": 0, "errors": 0, "variable_bpm_tracks": 0, "variable_bpm_details": []}
        m_key.return_value = {"succeeded": 0, "errors": 0}
        m_energy.return_value = {"succeeded": 0, "errors": 0}
        run_pipeline(
            db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            cfg=cfg,
            apply=True,
            music_dir=None,   # skip directory import so removed-files sweep doesn't delete test rows
            skip={"import"},
            backup_dir=False,
        )

    conn2 = sqlite3.connect(str(multidj_db))
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT artist, title FROM tracks WHERE path='/music/Blue Monday - New Order.mp3'"
    ).fetchone()
    conn2.close()
    assert row["artist"] == "New Order"
    assert row["title"] == "Blue Monday"
