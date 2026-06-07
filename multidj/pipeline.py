from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .analyze import analyze_bpm, analyze_energy, analyze_key
from .audit import fix_mismatches
from .backup import create_backup
from .clean import clean_genres, clean_text
from .crates import rebuild_crates
from .dedupe import dedupe as _dedupe
from .enrich import enrich_metadata as _enrich_metadata
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
    report_output: str | None = None,
    skip_report: bool = False,
    backup_dir: str | None | bool = None,  # False = suppress backup (sentinel)
    limit: int | None = None,
) -> dict[str, Any]:
    """Run the full MultiDJ pipeline: import → parse → enrich → dedupe → bpm → key → mixxx_blobs → energy → embed → cluster → cues → genres → clean_text → crates → sync → report.

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
    if not cfg.get("pipeline", {}).get("fix_mismatches", True):
        skip = skip | {"fix_mismatches"}
    if not cfg.get("pipeline", {}).get("clean_text", True):
        skip = skip | {"clean_text"}
    if not cfg.get("pipeline", {}).get("cues", True):
        skip = skip | {"cues"}
    if not cfg.get("pipeline", {}).get("mixxx_blobs", True):
        skip = skip | {"mixxx_blobs"}
    if skip_report:
        skip = skip | {"report"}

    # One backup at the start — not per step
    if apply and backup_dir is not False:
        resolved = Path(db_path).expanduser() if db_path else Path("~/.multidj/library.sqlite").expanduser()
        if resolved.exists():
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
            limit=limit,
        ))
    else:
        steps.append({"step": "import", "status": "skipped", "reason": "music_dir not set"})

    # Step 2: Fix artist/title swap mismatches from filename convention
    steps.append(_run_step(
        "fix_mismatches", fix_mismatches,
        db_path=db_path, apply=apply, backup=False,
        limit=limit,
    ))

    # Step 3: Parse filenames
    steps.append(_run_step(
        "parse", parse_library,
        db_path=db_path, apply=apply, backup=False,
        limit=limit,
    ))

    # Step 4: Enrich metadata from file tags + Discogs + MusicBrainz
    # enrich_metadata degrades gracefully if discogs/musicbrainz extras are missing
    from .config import get_enrich_config as _gec
    steps.append(_run_step(
        "enrich", _enrich_metadata,
        db_path=db_path, apply=apply,
        limit=limit,
        enrich_cfg=_gec(cfg),
        backup_dir=False,
    ))

    # Step 5: Deduplicate tracks (before analysis to avoid wasted compute)
    steps.append(_run_step(
        "dedupe", _dedupe,
        db_path=db_path, apply=apply, backup=False,
        limit=limit,
    ))

    # Step 6: Detect BPM
    steps.append(_run_step(
        "bpm", analyze_bpm,
        db_path=db_path, apply=apply, backup_dir=False,
        limit=limit,
    ))

    # Step 7: Detect key
    steps.append(_run_step(
        "key", analyze_key,
        db_path=db_path, apply=apply,
        limit=limit,
    ))

    # Step 7: Write Mixxx analysis BLOBs (BeatGrid + KeyMap) into Mixxx DB
    # Requires mixxx_db_path to be configured.
    if mixxx_db_path:
        from .mixxx_blobs import analyze_mixxx_blobs as _amb
        steps.append(_run_step(
            "mixxx_blobs", _amb,
            multidj_db_path=db_path, mixxx_db_path=mixxx_db_path,
            apply=apply, backup_dir=False,
            limit=limit,
            write_beats=True,
        ))
    else:
        steps.append({
            "step": "mixxx_blobs",
            "status": "skipped",
            "reason": "mixxx_db_path not set",
        })


    # Step 8: Detect energy
    steps.append(_run_step(
        "energy", analyze_energy,
        db_path=db_path, apply=apply, backup_dir=False,
        limit=limit,
    ))

    # Auto-skip embed/cluster if disabled in config
    if not cfg.get("pipeline", {}).get("embed", True):
        skip = skip | {"embed"}
    if not cfg.get("pipeline", {}).get("cluster", True):
        skip = skip | {"cluster"}

    # Step 9: Embed tracks (requires [embeddings] extra)
    def _run_embed(**kwargs):
        try:
            from .embed import analyze_embed as _ae
            return _ae(**kwargs)
        except ImportError:
            raise RuntimeError("embeddings extra not installed; run: uv sync --extra embeddings")

    steps.append(_run_step(
        "embed", _run_embed,
        db_path=db_path, apply=apply, backup_dir=False,
        limit=limit,
    ))

    # Step 10: Cluster into Vibe/ crates
    def _run_cluster(**kwargs):
        try:
            from .cluster import cluster_vibe as _cv
            from .config import get_llm_config as _glc
            return _cv(llm_config=_glc(cfg), **kwargs)
        except ImportError:
            raise RuntimeError("embeddings extra not installed; run: uv sync --extra embeddings")

    steps.append(_run_step(
        "cluster", _run_cluster,
        db_path=db_path, apply=apply, backup_dir=False,
        min_cluster_size=cfg.get("pipeline", {}).get("min_cluster_size", 5),
    ))

    # Step 11: Detect structural cues (requires embeddings extra)
    def _run_cues(**kwargs):
        try:
            from .cues import analyze_cues as _ac
            return _ac(**kwargs)
        except ImportError:
            raise RuntimeError("embeddings extra not installed; run: uv sync --extra embeddings")

    steps.append(_run_step(
        "cues", _run_cues,
        db_path=db_path, apply=apply, backup_dir=False,
        limit=limit,
    ))

    # Step 12: Normalize genres
    steps.append(_run_step(
        "genres", clean_genres,
        db_path=db_path, apply=apply, backup=False,
        limit=limit,
    ))

    # Step 13: Clean artist/title/album text noise
    steps.append(_run_step(
        "clean_text", clean_text,
        db_path=db_path, apply=apply, backup=False,
        limit=limit,
    ))

    # Step 14: Rebuild crates (limit not applicable — full rebuild)
    if limit is not None:
        _log(f"[pipeline:crates] --limit ignored (crates require full rebuild)")
    steps.append(_run_step(
        "crates", rebuild_crates,
        db_path=db_path, apply=apply, backup=False, cfg=cfg,
    ))

    # Step 15: Sync to Mixxx (limit not applicable — full sync)
    if limit is not None and mixxx_db_path:
        _log(f"[pipeline:sync] --limit ignored (sync pushes all dirty tracks)")
    if mixxx_db_path:
        from .adapters.mixxx import MixxxAdapter
        mx_adapter = MixxxAdapter(mixxx_db_path=mixxx_db_path)
        steps.append(_run_step(
            "sync", mx_adapter.full_sync,
            multidj_db_path=db_path, apply=apply,
        ))
    else:
        steps.append({"step": "sync", "status": "skipped", "reason": "mixxx_db_path not set"})

    # Step 16: Generate HTML report (read-only)
    def _report_step() -> dict[str, Any]:
        from .report import write_html_report

        output_path = report_output or "multidj_report.html"
        write_html_report(db_path=db_path, output_path=output_path)
        return {
            "path": output_path,
            "generated": True,
        }

    steps.append(_run_step("report", _report_step))

    # Print report path so user can Ctrl+click it
    report_result = steps[-1]
    if report_result.get("status") == "ok":
        report_path = report_result.get("result", {}).get("path", "")
        if report_path:
            abs_path = Path(report_path).resolve()
            _log(f"\n━━━ Report ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            _log(f"  file://{abs_path}")
            _log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    errors = [s for s in steps if s["status"] == "error"]
    return {
        "mode": mode,
        "steps": steps,
        "total_steps": len(steps),
        "errors": len(errors),
        "error_steps": [s["step"] for s in errors],
    }
