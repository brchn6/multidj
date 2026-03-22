from __future__ import annotations

import argparse
import sys
from typing import Any

from . import __version__
from .analyze import analyze_key
from .audit import audit_genres, audit_metadata
from .backup import create_backup
from .clean import clean_genres, clean_text
from .crates import audit_crates, delete_crates, hide_crates, show_crates, rebuild_crates
from .dedupe import dedupe
from .enrich import enrich_language
from .parse import parse_library
from .scan import format_scan, scan_library
from .utils import emit


def _hoist_global_flags(argv: list[str]) -> list[str]:
    """Move --json and --db <val> before the first positional (subcommand) token."""
    global_flags: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--json":
            global_flags.append(argv[i])
        elif argv[i] == "--db" and i + 1 < len(argv):
            global_flags += [argv[i], argv[i + 1]]
            i += 1
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

    sub = parser.add_subparsers(dest="command", required=True)

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

    # ── enrich ────────────────────────────────────────────────────────────────
    enrich_p = sub.add_parser("enrich", help="Enrich track metadata from external signals")
    enrich_sub = enrich_p.add_subparsers(dest="enrich_target", required=True)
    enrich_sub.add_parser("language", help="Detect Hebrew tracks (Unicode range check)")

    # ── clean ────────────────────────────────────────────────────────────────
    clean_p = sub.add_parser("clean", help="Normalize metadata in bulk")
    clean_sub = clean_p.add_subparsers(dest="clean_target", required=True)

    for name, help_text in [
        ("genres", "Collapse case variants, null uninformative genres, fix whitespace"),
        ("text",   "Strip and collapse whitespace in artist / title / album"),
    ]:
        p = clean_sub.add_parser(name, help=help_text)
        p.add_argument("--apply",     action="store_true", help="Write changes (default: dry-run)")
        p.add_argument("--limit",     type=int,            help="Cap number of changes")
        p.add_argument("--no-backup", action="store_true", help="Skip backup before apply")

    # ── analyze ──────────────────────────────────────────────────────────────
    analyze_p = sub.add_parser("analyze", help="Analysis and tagging actions")
    analyze_sub = analyze_p.add_subparsers(dest="analyze_target", required=True)

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

    return parser


def main(argv: list[str] | None = None) -> int:
    raw = _hoist_global_flags(list(argv) if argv is not None else sys.argv[1:])
    parser = build_parser()
    args = parser.parse_args(raw)

    result: Any

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

    else:
        parser.error("Unknown command.")
        return 2

    emit(result, as_json=args.json)
    return 0
