from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

DEFAULT_CONFIG: dict[str, Any] = {
    "pipeline": {
        "music_dir": "",
    },
    "crates": {
        "bpm":      True,
        "key":      True,
        "genre":    True,
        "energy":   True,
        "language": True,
    },
    "bpm": {
        "min_tracks": 3,
    },
    "energy": {
        "low_max":  0.33,
        "high_min": 0.67,
    },
}

_DEFAULT_CONFIG_PATH = Path.home() / ".multidj" / "config.toml"


def _serialize(cfg: dict[str, Any]) -> str:
    lines: list[str] = []
    for section, values in cfg.items():
        lines.append(f"[{section}]")
        for key, val in values.items():
            if isinstance(val, bool):
                lines.append(f"{key} = {'true' if val else 'false'}")
            elif isinstance(val, str):
                escaped = val.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{key} = "{escaped}"')
            else:
                if not isinstance(val, (int, float)):
                    raise TypeError(f"Unsupported config value type {type(val).__name__} for key {key!r}")
                lines.append(f"{key} = {val}")
        lines.append("")
    return "\n".join(lines)


def load_config(path: Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not p.exists():
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_serialize(cfg), encoding="utf-8")
        return cfg

    with open(p, "rb") as f:
        raw = tomllib.load(f)

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for section, defaults in cfg.items():
        if section in raw:
            defaults.update(raw[section])
    # Preserve any extra sections in the user's file not in DEFAULT_CONFIG
    for section, values in raw.items():
        if section not in cfg:
            cfg[section] = dict(values)
    return cfg


def save_config(cfg: dict[str, Any], path: Path | None = None) -> None:
    p = Path(path) if path else _DEFAULT_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_serialize(cfg), encoding="utf-8")


def get_music_dir(cfg: dict[str, Any]) -> str | None:
    val = cfg.get("pipeline", {}).get("music_dir", "")
    return val.strip() or None
