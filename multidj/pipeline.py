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
from .enrich_genre import enrich_genre as _enrich_genre
from .parse import parse_library


PHASES: dict[str, set[str]] = {
    "ingest":  {"import", "dedupe", "fix_mismatches", "parse"},
    "analyze": {"mixxx_import", "bpm", "key", "mixxx_blobs", "energy", "embed", "cues"},
    "enrich":  {"clean_text", "enrich_meta", "enrich_genre", "clean_genres"},
    "sync":    {"cluster", "crates", "sync", "report"},
}

_ALL_STEPS: set[str] = set().union(*PHASES.values())


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def run_pipeline(
    db_path: str | None = None,
    mixxx_db_path: str | None = None,
    cfg: dict[str, Any] | None = None,
    apply: bool = False,
    music_dir: str | None = None,
    skip: set[str] | None = None,
    phase: str | None = None,
    report_output: str | None = None,
    skip_report: bool = False,
    backup_dir: str | None | bool = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run the MultiDJ pipeline in four phases: ingest → analyze → enrich → sync.

    Phase 1 INGEST:   import, dedupe, fix_mismatches, parse
    Phase 2 ANALYZE:  mixxx_import, bpm, key, mixxx_blobs, energy, embed, cues
    Phase 3 ENRICH:   clean_text, enrich_meta, enrich_genre, clean_genres
    Phase 4 SYNC:     cluster, crates, sync, report

    Pass phase='ingest'|'analyze'|'enrich'|'sync' to run a single phase.
    """
    cfg = cfg or {}
    skip = set(skip or set())
    mode = "apply" if apply else "dry_run"

    # Phase filter: skip all steps not in the requested phase
    if phase is not None:
        phase_steps = PHASES.get(phase, set())
        skip = skip | (_ALL_STEPS - phase_steps)

    # Config-driven auto-skips
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

    steps: list[dict[str, Any]] = []

    # ── Phase 1: INGEST ───────────────────────────────────────────────────────

    if music_dir:
        from .adapters.directory import DirectoryAdapter
        adapter = DirectoryAdapter()
        steps.append(_run_step(
            "import", adapter.import_all,
            multidj_db_path=db_path, apply=apply, paths=[music_dir], limit=limit,
        ))
    else:
        steps.append({"step": "import", "status": "skipped", "reason": "music_dir not set"})

    steps.append(_run_step(
        "dedupe", _dedupe,
        db_path=db_path, apply=apply, backup=False, limit=limit,
    ))

    steps.append(_run_step(
        "fix_mismatches", fix_mismatches,
        db_path=db_path, apply=apply, backup=False, limit=limit,
    ))

    steps.append(_run_step(
        "parse", parse_library,
        db_path=db_path, apply=apply, backup=False, limit=limit,
    ))

    # ── Phase 2: ANALYZE ──────────────────────────────────────────────────────

    if mixxx_db_path:
        from .import_mixxx_analysis import import_mixxx_analysis as _ima
        steps.append(_run_step(
            "mixxx_import", _ima,
            multidj_db_path=db_path, mixxx_db_path=mixxx_db_path,
            apply=apply, backup_dir=False, limit=limit,
        ))
    else:
        steps.append({"step": "mixxx_import", "status": "skipped", "reason": "mixxx_db_path not set"})

    steps.append(_run_step(
        "bpm", analyze_bpm,
        db_path=db_path, apply=apply, backup_dir=False, limit=limit,
    ))

    steps.append(_run_step(
        "key", analyze_key,
        db_path=db_path, apply=apply, limit=limit,
    ))

    if mixxx_db_path:
        from .mixxx_blobs import analyze_mixxx_blobs as _amb
        steps.append(_run_step(
            "mixxx_blobs", _amb,
            multidj_db_path=db_path, mixxx_db_path=mixxx_db_path,
            apply=apply, backup_dir=False, limit=limit, write_beats=True,
        ))
    else:
        steps.append({"step": "mixxx_blobs", "status": "skipped", "reason": "mixxx_db_path not set"})

    steps.append(_run_step(
        "energy", analyze_energy,
        db_path=db_path, apply=apply, backup_dir=False, limit=limit,
    ))

    # Auto-skip embed/cluster if disabled in config
    if not cfg.get("pipeline", {}).get("embed", True):
        skip = skip | {"embed"}
    if not cfg.get("pipeline", {}).get("cluster", True):
        skip = skip | {"cluster"}

    def _run_embed(**kwargs):
        try:
            from .embed import analyze_embed as _ae
            return _ae(**kwargs)
        except ImportError:
            raise RuntimeError("embeddings extra not installed; run: uv sync --extra embeddings")

    steps.append(_run_step(
        "embed", _run_embed,
        db_path=db_path, apply=apply, backup_dir=False, limit=limit,
    ))

    def _run_cues(**kwargs):
        try:
            from .cues import analyze_cues as _ac
            return _ac(**kwargs)
        except ImportError:
            raise RuntimeError("embeddings extra not installed; run: uv sync --extra embeddings")

    steps.append(_run_step(
        "cues", _run_cues,
        db_path=db_path, apply=apply, backup_dir=False, limit=limit,
    ))

    # ── Phase 3: ENRICH ───────────────────────────────────────────────────────

    steps.append(_run_step(
        "clean_text", clean_text,
        db_path=db_path, apply=apply, backup=False, limit=limit,
    ))

    from .config import get_enrich_config as _gec
    steps.append(_run_step(
        "enrich_meta", _enrich_metadata,
        db_path=db_path, apply=apply, limit=limit,
        enrich_cfg=_gec(cfg), backup_dir=False,
    ))

    steps.append(_run_step(
        "enrich_genre", _enrich_genre,
        db_path=db_path, apply=apply, limit=limit,
        enrich_cfg=_gec(cfg),
    ))

    steps.append(_run_step(
        "clean_genres", clean_genres,
        db_path=db_path, apply=apply, backup=False, limit=limit,
    ))

    # ── Phase 4: SYNC ─────────────────────────────────────────────────────────

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

    if limit is not None:
        _log("[pipeline:crates] --limit ignored (crates require full rebuild)")
    steps.append(_run_step(
        "crates", rebuild_crates,
        db_path=db_path, apply=apply, backup=False, cfg=cfg,
    ))

    if mixxx_db_path:
        from .adapters.mixxx import MixxxAdapter
        mx_adapter = MixxxAdapter(mixxx_db_path=mixxx_db_path)
        steps.append(_run_step(
            "sync", mx_adapter.full_sync,
            multidj_db_path=db_path, apply=apply,
        ))
    else:
        steps.append({"step": "sync", "status": "skipped", "reason": "mixxx_db_path not set"})

    def _report_step() -> dict[str, Any]:
        from .report import write_html_report
        output_path = report_output or "multidj_report.html"
        write_html_report(db_path=db_path, output_path=output_path)
        return {"path": output_path, "generated": True}

    steps.append(_run_step("report", _report_step))

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
