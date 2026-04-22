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
    assert len(result["steps"]) == 8


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
