from __future__ import annotations

import sys
from typing import Any

from .analyze import analyze_bpm, analyze_energy, analyze_key
from .backup import create_backup
from .clean import clean_genres
from .crates import rebuild_crates
from .parse import parse_library


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def run_pipeline(
    db_path: str | None = None,
    mixxx_db_path: str | None = None,
    cfg: dict[str, Any] | None = None,
    apply: bool = False,
    music_dir: str | None = None,
    skip: set[str] | None = None,
    backup_dir: str | None | bool = None,  # False = suppress backup (sentinel)
) -> dict[str, Any]:
    """Run the full MultiDJ pipeline: import → parse → bpm → key → energy → genres → crates → sync.

    Steps run sequentially. One failure does not abort remaining steps.
    Config toggles auto-skip analysis steps for disabled crate dimensions.
    One backup is taken at the start of an apply run, not per step.
    """
    cfg = cfg or {}
    skip = skip or set()
    steps: list[dict[str, Any]] = []
    mode = "apply" if apply else "dry_run"

    # Auto-skip analysis steps for disabled crate dimensions
    if not cfg.get("crates", {}).get("energy", True):
        skip = skip | {"energy"}
    if not cfg.get("crates", {}).get("bpm", True):
        skip = skip | {"bpm"}
    if not cfg.get("crates", {}).get("key", True):
        skip = skip | {"key"}

    # One backup at the start — not per step
    if apply and backup_dir is not False:
        create_backup(db_path, backup_dir=backup_dir)

    def _run_step(name: str, fn, **kwargs) -> dict[str, Any]:
        if name in skip:
            _log(f"[pipeline:{name}] skipped")
            return {"step": name, "status": "skipped"}
        _log(f"[pipeline:{name}] starting...")
        try:
            result = fn(**kwargs)
            _log(f"[pipeline:{name}] done")
            return {"step": name, "status": "ok", "result": result}
        except ImportError:
            raise
        except Exception as exc:
            _log(f"[pipeline:{name}] ERROR: {exc}")
            return {"step": name, "status": "error", "error": str(exc)}

    # Step 1: Import new tracks from music_dir
    if music_dir:
        from .adapters.directory import DirectoryAdapter
        adapter = DirectoryAdapter()
        steps.append(_run_step(
            "import", adapter.import_all,
            multidj_db_path=db_path, apply=apply, paths=[music_dir],
        ))
    else:
        steps.append({"step": "import", "status": "skipped", "reason": "music_dir not set"})

    # Step 2: Parse filenames
    steps.append(_run_step(
        "parse", parse_library,
        db_path=db_path, apply=apply, backup=False,
    ))

    # Step 3: Detect BPM
    steps.append(_run_step(
        "bpm", analyze_bpm,
        db_path=db_path, apply=apply, backup_dir=False,
    ))

    # Step 4: Detect key (analyze_key has no backup_dir param — it never creates backups)
    steps.append(_run_step(
        "key", analyze_key,
        db_path=db_path, apply=apply,
    ))

    # Step 5: Detect energy
    steps.append(_run_step(
        "energy", analyze_energy,
        db_path=db_path, apply=apply, backup_dir=False,
    ))

    # Step 6: Normalize genres
    steps.append(_run_step(
        "genres", clean_genres,
        db_path=db_path, apply=apply, backup=False,
    ))

    # Step 7: Rebuild crates
    steps.append(_run_step(
        "crates", rebuild_crates,
        db_path=db_path, apply=apply, backup=False, cfg=cfg,
    ))

    # Step 8: Sync to Mixxx
    if mixxx_db_path:
        from .adapters.mixxx import MixxxAdapter
        mx_adapter = MixxxAdapter(mixxx_db_path=mixxx_db_path)
        steps.append(_run_step(
            "sync", mx_adapter.full_sync,
            multidj_db_path=db_path, apply=apply,
        ))
    else:
        steps.append({"step": "sync", "status": "skipped", "reason": "mixxx_db_path not set"})

    errors = [s for s in steps if s["status"] == "error"]
    return {
        "mode": mode,
        "steps": steps,
        "total_steps": len(steps),
        "errors": len(errors),
        "error_steps": [s["step"] for s in errors],
    }
