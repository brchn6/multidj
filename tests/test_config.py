from __future__ import annotations
import pytest
from multidj.config import load_config, save_config, DEFAULT_CONFIG, get_music_dir


def test_load_creates_defaults_if_missing(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg = load_config(cfg_path)
    assert cfg["crates"]["bpm"] is True
    assert cfg["crates"]["key"] is True
    assert cfg["crates"]["energy"] is True
    assert cfg["pipeline"]["music_dir"] == ""
    assert cfg["bpm"]["min_tracks"] == 3
    assert cfg["energy"]["low_max"] == pytest.approx(0.33)
    assert cfg["energy"]["high_min"] == pytest.approx(0.67)


def test_load_creates_file_if_missing(tmp_path):
    cfg_path = tmp_path / "config.toml"
    assert not cfg_path.exists()
    load_config(cfg_path)
    assert cfg_path.exists()


def test_load_reads_existing_values(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "[pipeline]\nmusic_dir = '/my/music'\n\n"
        "[crates]\nbpm = false\nkey = true\ngenre = true\nenergy = true\nlanguage = true\n\n"
        "[bpm]\nmin_tracks = 5\n\n"
        "[energy]\nlow_max = 0.25\nhigh_min = 0.75\n"
    )
    cfg = load_config(cfg_path)
    assert cfg["pipeline"]["music_dir"] == "/my/music"
    assert cfg["crates"]["bpm"] is False
    assert cfg["bpm"]["min_tracks"] == 5
    assert cfg["energy"]["low_max"] == pytest.approx(0.25)


def test_save_config_roundtrip(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg = load_config(cfg_path)
    cfg["pipeline"]["music_dir"] = "/new/path"
    save_config(cfg, cfg_path)
    cfg2 = load_config(cfg_path)
    assert cfg2["pipeline"]["music_dir"] == "/new/path"


def test_get_music_dir_returns_value(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "[pipeline]\nmusic_dir = '/my/music'\n\n"
        "[crates]\nbpm = true\nkey = true\ngenre = true\nenergy = true\nlanguage = true\n\n"
        "[bpm]\nmin_tracks = 3\n\n"
        "[energy]\nlow_max = 0.33\nhigh_min = 0.67\n"
    )
    cfg = load_config(cfg_path)
    assert get_music_dir(cfg) == "/my/music"


def test_get_music_dir_returns_none_when_empty(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg = load_config(cfg_path)
    assert get_music_dir(cfg) is None
