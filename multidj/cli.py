from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .adapters.mixxx import MixxxAdapter
from .analyze import analyze_key
from .audit import audit_genres, audit_metadata, audit_mismatches
from .backup import create_backup
from .clean import clean_genres, clean_text
from .config import load_config, save_config, get_music_dir
from .crates import audit_crates, delete_crates, hide_crates, show_crates, rebuild_crates
from .db import resolve_db_path
from .dedupe import dedupe
from .enrich import enrich_language
from .parse import parse_library
from .pipeline import run_pipeline
from .report import write_dashboard_report
from .scan import format_scan, scan_library
from .utils import emit


def _hoist_global_flags(argv: list[str]) -> list[str]:
    """Move --json, --db <val>, and --dry-run before the first positional (subcommand) token."""
    global_flags: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--json":
            global_flags.append(argv[i])
        elif argv[i] == "--db" and i + 1 < len(argv):
            global_flags += [argv[i], argv[i + 1]]
            i += 1
        elif argv[i] == "--dry-run":
            global_flags.append(argv[i])
        else:
            rest.append(argv[i])
        i += 1
    return global_flags + rest


def _format_parse(data: dict) -> str:
    lines = [
        f"Parse — {data['total_changes']} proposed changes "
        f"({data['skipped_already_tagged']} already tagged, "
        f"{data['skipped_low_confidence']} below confidence threshold)",
        "",
    ]
    for c in data["changes"][:50]:
        conf = c["confidence"].upper()
        fp = c["filepath"].split("/")[-1][:55]
        lines.append(f"  [{conf}] {fp}")
        if "new_artist" in c:
            old_a = c.get("old_artist") or "(empty)"
            lines.append(f"         artist: {old_a!r} → {c['new_artist']!r}")
        if "new_title" in c:
            old_t = c.get("old_title") or "(empty)"
            lines.append(f"         title:  {old_t!r} → {c['new_title']!r}")
        if c.get("remixer"):
            lines.append(f"         remixer: {c['remixer']}")
        if c.get("featuring"):
            lines.append(f"         feat:    {c['featuring']}")
    if data["total_changes"] > 50:
        lines.append(f"  ... and {data['total_changes'] - 50} more (use --json for full list)")
    return "\n".join(lines)


def _format_enrich_language(data: dict) -> str:
    total = data["total_active_tracks"]
    n = data["hebrew_tracks"]
    pct = data["hebrew_pct"]
    bar_w = 20
    filled = round(n / total * bar_w) if total else 0
    bar = "█" * filled + "░" * (bar_w - filled)
    lines = [
        f"Language detection — {total:,} active tracks",
        "",
        f"  Hebrew  {n:>5,} / {total:,}  {pct:>5.1f}%  {bar}",
        "",
    ]
    sample = data["tracks"][:10]
    if sample:
        lines.append("  Sample Hebrew tracks:")
        for t in sample:
            lines.append(f"    [{t['track_id']}] {t['artist'] or ''} — {t['title'] or ''}")
        if n > 10:
            lines.append(f"    ... and {n - 10} more (use --json for full list)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multidj",
        description="MultiDJ — DJ music library manager with SQLite, LLM integration, and multi-app sync.",
    )
    parser.add_argument("--db", help="Path to MultiDJ SQLite database (default: ~/.multidj/library.sqlite)")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing (default behavior)")

    sub = parser.add_subparsers(dest="command", required=True)

    # ── config ────────────────────────────────────────────────────────────────
    config_p = sub.add_parser("config", help="View or set persistent configuration")
    config_sub = config_p.add_subparsers(dest="config_target", required=True)
    p = config_sub.add_parser("set-db", help="Save default DB path to config (~/.multidj/config.toml)")
    p.add_argument("db_path_value", metavar="PATH", help="Path to the MultiDJ SQLite database")
    p = config_sub.add_parser("set-music-dir", help="Save default music directory to config")
    p.add_argument("music_dir_value", metavar="PATH", help="Path to your main music folder")
    config_sub.add_parser("show", help="Print current config")

    # ── scan ─────────────────────────────────────────────────────────────────
    p = sub.add_parser("scan", help="Library and schema summary")
    p.add_argument("--verbose", action="store_true", help="Also list all DB tables")

    # ── parse ─────────────────────────────────────────────────────────────────
    p = sub.add_parser("parse", help="Propose metadata from filenames (artist, title, remixer)")
    p.add_argument("--apply",          action="store_true", help="Write proposed changes to DB")
    p.add_argument("--force",          action="store_true", help="Overwrite non-empty fields")
    p.add_argument("--limit",          type=int,            help="Cap number of changes")
    p.add_argument("--no-backup",      action="store_true", help="Skip backup before apply")
    p.add_argument("--min-confidence", choices=["high", "medium", "low"], default="medium",
                   help="Minimum confidence to include (default: medium)")

    # ── backup ───────────────────────────────────────────────────────────────
    p = sub.add_parser("backup", help="Create a timestamped DB backup")
    p.add_argument("--backup-dir", help="Destination directory override")

    # ── audit ────────────────────────────────────────────────────────────────
    audit_p = sub.add_parser("audit", help="Audit library metadata")
    audit_sub = audit_p.add_subparsers(dest="audit_target", required=True)

    p = audit_sub.add_parser("genres", help="Genre distribution and quality report")
    p.add_argument("--top", type=int, default=100, help="Top N genres to include (default: 100)")

    audit_sub.add_parser("metadata", help="Field coverage percentages")

    p = audit_sub.add_parser("mismatches", help="Detect artist/title swap mismatches vs filenames")
    p.add_argument("--limit", type=int, default=100, help="Max mismatches to report (default: 100)")

    # ── enrich ────────────────────────────────────────────────────────────────
    enrich_p = sub.add_parser("enrich", help="Enrich track metadata from external signals")
    enrich_sub = enrich_p.add_subparsers(dest="enrich_target", required=True)
    enrich_sub.add_parser("language", help="Detect Hebrew tracks (Unicode range check)")

    # ── clean ────────────────────────────────────────────────────────────────
    clean_p = sub.add_parser("clean", help="Normalize metadata in bulk")
    clean_sub = clean_p.add_subparsers(dest="clean_target", required=True)

    for name, help_text in [
        ("genres", "Collapse case variants, null uninformative genres, fix whitespace"),
        ("text",   "Strip/collapse whitespace and remove known title video/download suffix noise"),
    ]:
        p = clean_sub.add_parser(name, help=help_text)
        p.add_argument("--apply",     action="store_true", help="Write changes (default: dry-run)")
        p.add_argument("--limit",     type=int,            help="Cap number of changes")
        p.add_argument("--no-backup", action="store_true", help="Skip backup before apply")

    # ── analyze ──────────────────────────────────────────────────────────────
    analyze_p = sub.add_parser("analyze", help="Analysis and tagging actions")
    analyze_sub = analyze_p.add_subparsers(dest="analyze_target", required=True)

    p_bpm = analyze_sub.add_parser("bpm", help="Detect BPM from audio for tracks where BPM is 0 or missing")
    p_bpm.add_argument("--apply",     action="store_true")
    p_bpm.add_argument("--force",     action="store_true", help="Re-analyze even tracks with existing BPM")
    p_bpm.add_argument("--limit",     type=int, default=None)
    p_bpm.add_argument("--no-backup", action="store_true", dest="no_backup")

    p_analyze_energy = analyze_sub.add_parser("energy", help="Detect energy level from audio")
    p_analyze_energy.add_argument("--apply", action="store_true")
    p_analyze_energy.add_argument("--force", action="store_true", help="Reprocess tracks that already have energy")
    p_analyze_energy.add_argument("--limit", type=int, default=None)
    p_analyze_energy.add_argument("--no-backup", action="store_true", dest="no_backup")

    p = analyze_sub.add_parser("key", help="Detect and tag musical key (requires librosa mutagen)")
    p.add_argument("--apply",       action="store_true", help="Write changes (default: dry-run)")
    p.add_argument("--write-tags",  action="store_true", help="Write key to audio file tags")
    p.add_argument("--no-sync-db",  action="store_true", help="Skip DB update (file tags only)")
    p.add_argument("--limit",       type=int,            help="Cap number of tracks to process")
    p.add_argument("--force",       action="store_true", help="Overwrite existing key values")
    p.add_argument("--verbose", "-v", action="store_true", help="Show detected key for each track")

    # ── crates ───────────────────────────────────────────────────────────────
    crates_p = sub.add_parser("crates", help="Crate management")
    crates_sub = crates_p.add_subparsers(dest="crates_target", required=True)

    p = crates_sub.add_parser("audit", help="Crate health report")
    p.add_argument("--min-tracks",    type=int, default=5, help="Minimum track threshold (default: 5)")
    p.add_argument("--summary",       action="store_true", help="Counts only — omit full crate lists")

    for name, help_text in [
        ("hide",   "Hide auto-generated crates below threshold (sets show=0, reversible)"),
        ("delete", "Permanently delete auto-generated crates below threshold"),
    ]:
        p = crates_sub.add_parser(name, help=help_text)
        p.add_argument("--min-tracks",          type=int, default=5)
        p.add_argument("--apply",               action="store_true")
        p.add_argument("--no-backup",           action="store_true")
        p.add_argument("--include-hand-curated", action="store_true",
                       help="Also affect hand-curated crates (protected by default)")

    p = crates_sub.add_parser("show", help="Restore hidden crates")
    p.add_argument("--min-tracks", type=int, default=None,
                   help="Only restore crates now meeting this threshold")
    p.add_argument("--apply",     action="store_true")
    p.add_argument("--no-backup", action="store_true")

    p = crates_sub.add_parser("rebuild", help="Delete old auto-crates and rebuild Genre: + Lang: crates")
    p.add_argument("--min-tracks", type=int, default=5)
    p.add_argument("--apply",      action="store_true")
    p.add_argument("--no-backup",  action="store_true")

    # ── dedupe ───────────────────────────────────────────────────────────────
    p = sub.add_parser("dedupe", help="Find and remove duplicate tracks")
    p.add_argument("--by", choices=["both", "artist-title", "filesize"], default="both",
                   help="Detection strategy (default: both)")
    p.add_argument("--apply",     action="store_true",
                   help="Soft-delete duplicates (sets mixxx_deleted=1, reversible)")
    p.add_argument("--no-backup", action="store_true", help="Skip backup before apply")

    # ── import ────────────────────────────────────────────────────────────────
    import_p = sub.add_parser("import", help="Import tracks from external sources")
    import_sub = import_p.add_subparsers(dest="import_target", required=True)
    p = import_sub.add_parser("mixxx", help="One-time import from Mixxx DB into MultiDJ library")
    p.add_argument("--mixxx-db", help="Path to Mixxx DB (default: ~/.mixxx/mixxxdb.sqlite)")
    p.add_argument("--apply", action="store_true", help="Write to MultiDJ DB (default: dry-run)")
    p.add_argument("--no-backup", action="store_true", help="Skip backup before import")

    p_dir = import_sub.add_parser("directory", help="Import tracks from filesystem directories")
    p_dir.add_argument("paths", nargs="+", metavar="PATH",
                       help="Directories to scan recursively")
    p_dir.add_argument("--apply",     action="store_true")
    p_dir.add_argument("--no-backup", action="store_true", dest="no_backup")

    # ── sync ─────────────────────────────────────────────────────────────────
    sync_p = sub.add_parser("sync", help="Push dirty tracks to DJ software")
    sync_sub = sync_p.add_subparsers(dest="sync_target", required=True)
    p = sync_sub.add_parser("mixxx", help="Push dirty tracks to Mixxx")
    p.add_argument("--mixxx-db", help="Path to Mixxx DB (default: ~/.mixxx/mixxxdb.sqlite)")
    p.add_argument("--apply", action="store_true", help="Write to Mixxx (default: dry-run)")
    p.add_argument("--no-backup", action="store_true", help="Skip backup of Mixxx DB")

    # ── pipeline ─────────────────────────────────────────────────────────────
    p_pipeline = sub.add_parser("pipeline", help="Run full import → analyze → crates → sync pipeline")
    p_pipeline.add_argument("--apply",        action="store_true")
    p_pipeline.add_argument("--mixxx-db",     default=None, dest="mixxx_db")
    p_pipeline.add_argument("--music-dir",    default=None, dest="music_dir")
    p_pipeline.add_argument("--skip-import",          action="store_true", dest="skip_import")
    p_pipeline.add_argument("--skip-fix-mismatches",  action="store_true", dest="skip_fix_mismatches")
    p_pipeline.add_argument("--skip-parse",            action="store_true", dest="skip_parse")
    p_pipeline.add_argument("--skip-bpm",              action="store_true", dest="skip_bpm")
    p_pipeline.add_argument("--skip-key",              action="store_true", dest="skip_key")
    p_pipeline.add_argument("--skip-energy",           action="store_true", dest="skip_energy")
    p_pipeline.add_argument("--skip-genres",           action="store_true", dest="skip_genres")
    p_pipeline.add_argument("--skip-clean-text",       action="store_true", dest="skip_clean_text")
    p_pipeline.add_argument("--skip-crates",           action="store_true", dest="skip_crates")
    p_pipeline.add_argument("--skip-sync",             action="store_true", dest="skip_sync")
    p_pipeline.add_argument("--report-output",         default=None, dest="report_output",
                            help="Output path for HTML report (default: ./multidj_report.html)")
    p_pipeline.add_argument("--skip-report",           action="store_true", dest="skip_report",
                            help="Disable HTML report generation")

    # ── report ───────────────────────────────────────────────────────────────
    report_p = sub.add_parser("report", help="Generate library reports")
    report_sub = report_p.add_subparsers(dest="report_target", required=True)
    p = report_sub.add_parser("dashboard", help="Generate standalone interactive HTML dashboard")
    p.add_argument("--output", default="multidj_report.html",
                   help="Output HTML path (default: ./multidj_report.html)")

    return parser


def main(argv: list[str] | None = None) -> int:
    raw = _hoist_global_flags(list(argv) if argv is not None else sys.argv[1:])
    parser = build_parser()
    args = parser.parse_args(raw)

    result: Any

    if args.command == "config":
        cfg_data = load_config()
        if args.config_target == "set-db":
            db_val = str(Path(args.db_path_value).expanduser())
            cfg_data.setdefault("db", {})["path"] = db_val
            save_config(cfg_data)
            emit(f"Default DB path set to: {db_val}", as_json=args.json)
        elif args.config_target == "set-music-dir":
            music_val = str(Path(args.music_dir_value).expanduser())
            cfg_data.setdefault("pipeline", {})["music_dir"] = music_val
            save_config(cfg_data)
            emit(f"Default music dir set to: {music_val}", as_json=args.json)
        else:  # show
            import json as _json
            emit(_json.dumps(cfg_data, indent=2) if args.json else
                 "\n".join(f"[{s}]\n" + "\n".join(f"  {k} = {v}" for k, v in vals.items())
                           for s, vals in cfg_data.items()),
                 as_json=False)
        return 0

    if args.command == "scan":
        data = scan_library(args.db, verbose=args.verbose)
        emit(format_scan(data) if not args.json else data, as_json=args.json)
        return 0

    elif args.command == "parse":
        data = parse_library(
            args.db,
            apply=args.apply,
            force=args.force,
            limit=args.limit,
            min_confidence=args.min_confidence,
            backup=not args.no_backup,
        )
        emit(_format_parse(data) if not args.json else data, as_json=args.json)
        return 0

    elif args.command == "backup":
        result = create_backup(args.db, args.backup_dir).to_dict()

    elif args.command == "audit":
        if args.audit_target == "genres":
            result = audit_genres(args.db, top_n=args.top)
        elif args.audit_target == "mismatches":
            result = audit_mismatches(args.db, limit=args.limit)
        else:
            result = audit_metadata(args.db)

    elif args.command == "enrich":
        if args.enrich_target == "language":
            data = enrich_language(args.db)
            emit(_format_enrich_language(data) if not args.json else data, as_json=args.json)
            return 0

    elif args.command == "clean":
        kwargs = dict(db_path=args.db, apply=args.apply, limit=args.limit, backup=not args.no_backup)
        if args.clean_target == "genres":
            result = clean_genres(**kwargs)
        else:
            result = clean_text(**kwargs)

    elif args.command == "analyze":
        if args.analyze_target == "bpm":
            from .analyze import analyze_bpm
            result = analyze_bpm(
                db_path=args.db,
                apply=args.apply,
                force=args.force,
                limit=args.limit,
                backup_dir=False if args.no_backup else None,
            )
        elif args.analyze_target == "energy":
            from .analyze import analyze_energy
            result = analyze_energy(
                db_path=args.db,
                apply=args.apply,
                force=args.force,
                limit=args.limit,
                backup_dir=False if args.no_backup else None,
            )
        else:
            result = analyze_key(
                args.db,
                apply=args.apply,
                write_tags=args.write_tags,
                sync_db=not args.no_sync_db,
                limit=args.limit,
                force=args.force,
                verbose=args.verbose,
            )

    elif args.command == "crates":
        if args.crates_target == "audit":
            result = audit_crates(args.db, min_tracks=args.min_tracks, summary_only=args.summary)
        elif args.crates_target == "hide":
            result = hide_crates(
                args.db,
                min_tracks=args.min_tracks,
                apply=args.apply,
                backup=not args.no_backup,
                include_hand_curated=args.include_hand_curated,
            )
        elif args.crates_target == "show":
            result = show_crates(
                args.db,
                min_tracks=args.min_tracks,
                apply=args.apply,
                backup=not args.no_backup,
            )
        elif args.crates_target == "rebuild":
            result = rebuild_crates(
                args.db,
                min_tracks=args.min_tracks,
                apply=args.apply,
                backup=not args.no_backup,
            )
        elif args.crates_target == "delete":
            result = delete_crates(
                args.db,
                min_tracks=args.min_tracks,
                apply=args.apply,
                backup=not args.no_backup,
                include_hand_curated=args.include_hand_curated,
            )

    elif args.command == "dedupe":
        result = dedupe(args.db, by=args.by, apply=args.apply, backup=not args.no_backup)

    elif args.command == "import":
        if args.import_target == "mixxx":
            adapter = MixxxAdapter(mixxx_db_path=args.mixxx_db)
            if args.apply and not args.no_backup:
                create_backup(args.db)
            result = adapter.import_all(
                multidj_db_path=resolve_db_path(args.db),
                apply=args.apply,
            )
        elif args.import_target == "directory":
            from .adapters.directory import DirectoryAdapter
            adapter = DirectoryAdapter()
            result = adapter.import_all(
                multidj_db_path=resolve_db_path(args.db),
                apply=args.apply,
                paths=args.paths,
                backup_dir=False if args.no_backup else None,
            )

    elif args.command == "sync":
        if args.sync_target == "mixxx":
            adapter = MixxxAdapter(mixxx_db_path=args.mixxx_db)
            result = adapter.full_sync(
                multidj_db_path=resolve_db_path(args.db),
                apply=args.apply,
            )

    elif args.command == "pipeline":
        cfg = load_config()
        music_dir = args.music_dir or get_music_dir(cfg)
        if music_dir is None and not args.skip_import:
            music_dir_input = input(
                "MultiDJ music directory not set.\n"
                "Enter the path to your main music folder: "
            ).strip()
            if music_dir_input:
                music_dir = os.path.expanduser(music_dir_input)
                cfg["pipeline"]["music_dir"] = music_dir
                save_config(cfg)
                print("Saved to ~/.multidj/config.toml")

        skip: set[str] = set()
        if args.skip_import:          skip.add("import")
        if args.skip_fix_mismatches:  skip.add("fix_mismatches")
        if args.skip_parse:           skip.add("parse")
        if args.skip_bpm:             skip.add("bpm")
        if args.skip_key:             skip.add("key")
        if args.skip_energy:          skip.add("energy")
        if args.skip_genres:          skip.add("genres")
        if args.skip_clean_text:      skip.add("clean_text")
        if args.skip_crates:          skip.add("crates")
        if args.skip_sync:            skip.add("sync")
        if args.skip_report:          skip.add("report")

        result = run_pipeline(
            db_path=args.db,
            mixxx_db_path=args.mixxx_db,
            cfg=cfg,
            apply=args.apply,
            music_dir=music_dir,
            skip=skip,
            report_output=args.report_output,
            skip_report=args.skip_report,
        )
        emit(result, as_json=args.json)
        return 0

    elif args.command == "report":
        if args.report_target == "dashboard":
            write_dashboard_report(args.db, args.output)
            result = {
                "mode": "read_only",
                "report": {
                    "path": args.output,
                    "generated": True,
                    "type": "dashboard",
                },
            }
        else:
            parser.error("Unknown report command.")
            return 2

    else:
        parser.error("Unknown command.")
        return 2

    emit(result, as_json=args.json)
    return 0
