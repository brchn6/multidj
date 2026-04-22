from __future__ import annotations

import sys
from typing import Any

from .db import connect, table_exists, ensure_not_empty


def _progress(msg: str, end: str = "\n") -> None:
    print(msg, file=sys.stderr, end=end, flush=True)


def detect_key(filepath: str) -> str:
    try:
        import librosa  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        raise ImportError("Key analysis requires: pip install librosa mutagen")

    KS_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
    KS_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
    KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

    y, sr = librosa.load(filepath, sr=22050, mono=True, duration=60)
    chroma = np.mean(librosa.feature.chroma_cqt(y=y, sr=sr), axis=1)
    best, best_key, best_mode = -np.inf, "C", "maj"
    for i in range(12):
        c = np.roll(chroma, -i)
        for tmpl, mode in [(KS_MAJOR, "maj"), (KS_MINOR, "min")]:
            r = np.corrcoef(c, tmpl)[0, 1]
            if r > best:
                best, best_key, best_mode = r, KEYS[i], mode
    return f"{best_key}{best_mode}"


def _write_tag(filepath: str, key: str) -> None:
    try:
        import mutagen  # type: ignore  # noqa: F401
    except ImportError:
        raise ImportError("Key analysis requires: pip install librosa mutagen")

    import mutagen.id3 as id3  # type: ignore
    import mutagen.flac as flac_mod  # type: ignore
    import mutagen.mp4 as mp4_mod  # type: ignore

    lower = filepath.lower()
    if lower.endswith(".mp3"):
        audio = id3.ID3(filepath)
        audio.delall("TKEY")
        audio.add(id3.TKEY(encoding=3, text=key))
        audio.save()
    elif lower.endswith(".flac"):
        audio = flac_mod.FLAC(filepath)
        audio["key"] = [key]
        audio.save()
    elif lower.endswith((".m4a", ".mp4")):
        audio = mp4_mod.MP4(filepath)
        audio["----:com.apple.iTunes:initialkey"] = [mp4_mod.MP4FreeForm(key.encode())]
        audio.save()
    else:
        raise ValueError(f"Unsupported file type for tag writing: {filepath}")


def detect_energy(filepath: str) -> float:
    """Return raw energy proxy: mean RMS × mean spectral centroid (before normalization)."""
    try:
        import librosa  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        raise ImportError("Energy analysis requires: pip install librosa")

    y, sr = librosa.load(filepath, sr=22050, mono=True, duration=60)
    rms = float(np.mean(librosa.feature.rms(y=y)))
    centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
    return rms * centroid


def detect_bpm(filepath: str) -> float:
    """Detect tempo in BPM from audio file using librosa beat tracker."""
    try:
        import librosa  # type: ignore
    except ImportError:
        raise ImportError("BPM analysis requires: pip install librosa")
    y, sr = librosa.load(filepath, sr=22050, mono=True, duration=30)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    # librosa may return an array for tempo in newer versions
    if hasattr(tempo, '__len__'):
        tempo = float(tempo[0])
    return float(tempo)


def analyze_bpm(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    backup_dir: str | None = None,
) -> dict[str, Any]:
    """Detect BPM for tracks where bpm is NULL or 0."""
    from .backup import create_backup

    with connect(db_path, readonly=True) as _guard:
        if table_exists(_guard, "library") and not table_exists(_guard, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(_guard)

    where = "1=1" if force else "(bpm IS NULL OR bpm = 0)"
    sql = f"""
        SELECT id, artist, title, path AS filepath
        FROM tracks WHERE {where} AND deleted = 0
        ORDER BY artist, title
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    count_sql = f"SELECT COUNT(*) FROM tracks WHERE {where} AND deleted = 0"

    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(sql).fetchall()
        total_candidates = conn.execute(count_sql).fetchone()[0]

    mode = "apply" if apply else "dry_run"

    if not apply:
        _progress(f"Dry-run: {total_candidates:,} tracks would be analyzed (run with --apply to process)")
        return {
            "mode": mode,
            "total_candidates": total_candidates,
            "processed": 0,
            "succeeded": 0,
            "errors": 0,
            "error_details": [],
        }

    if backup_dir is not False:
        create_backup(db_path, backup_dir=backup_dir)

    db_updates: list[tuple[float, int]] = []
    error_details: list[dict] = []
    succeeded = 0
    total = len(rows)

    _progress(f"Analyzing BPM for {total:,} tracks...")

    for i, row in enumerate(rows, 1):
        label = f"{row['artist'] or ''} - {row['title'] or ''}".strip(" -") or row["filepath"]
        _progress(f"[{i:{len(str(total))}}/{total}] {label[:60]}", end="")
        try:
            bpm = detect_bpm(row["filepath"])
            db_updates.append((bpm, row["id"]))
            succeeded += 1
            _progress(f"  → {bpm:.1f}")
        except ImportError:
            raise
        except Exception as exc:
            _progress(f"  ERROR: {exc}")
            error_details.append({"track_id": row["id"], "error": str(exc)})

    if db_updates:
        _progress(f"Writing {len(db_updates):,} BPM values to DB...")
        with connect(db_path, readonly=False) as wconn:
            wconn.executemany("UPDATE tracks SET bpm = ? WHERE id = ?", db_updates)
            wconn.commit()

    return {
        "mode": mode,
        "total_candidates": total_candidates,
        "processed": total,
        "succeeded": succeeded,
        "errors": len(error_details),
        "error_details": error_details,
    }


def analyze_energy(
    db_path: str | None = None,
    apply: bool = False,
    force: bool = False,
    limit: int | None = None,
    backup_dir: str | None = None,
) -> dict[str, Any]:
    """Detect energy level (0.0–1.0) for tracks where energy IS NULL."""
    from .backup import create_backup

    with connect(db_path, readonly=True) as _guard:
        if table_exists(_guard, "library") and not table_exists(_guard, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(_guard)

    where = "1=1" if force else "energy IS NULL"
    sql = f"""
        SELECT id, artist, title, path AS filepath
        FROM tracks WHERE {where} AND deleted = 0
        ORDER BY artist, title
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    count_sql = f"SELECT COUNT(*) FROM tracks WHERE {where} AND deleted = 0"

    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(sql).fetchall()
        total_candidates = conn.execute(count_sql).fetchone()[0]

    mode = "apply" if apply else "dry_run"

    if not apply:
        _progress(f"Dry-run: {total_candidates:,} tracks would be analyzed (run with --apply to process)")
        return {
            "mode": mode,
            "total_candidates": total_candidates,
            "processed": 0,
            "succeeded": 0,
            "errors": 0,
            "error_details": [],
        }

    if backup_dir is not False:
        create_backup(db_path, backup_dir=backup_dir)

    raw_scores: list[tuple[int, float]] = []  # (track_id, raw_score)
    error_details: list[dict] = []
    total = len(rows)

    _progress(f"Analyzing energy for {total:,} tracks...")

    for i, row in enumerate(rows, 1):
        label = f"{row['artist'] or ''} - {row['title'] or ''}".strip(" -") or row["filepath"]
        _progress(f"[{i:{len(str(total))}}/{total}] {label[:60]}", end="")
        try:
            score = detect_energy(row["filepath"])
            raw_scores.append((row["id"], score))
            _progress("  ✓")
        except ImportError:
            raise
        except Exception as exc:
            _progress(f"  ERROR: {exc}")
            error_details.append({"track_id": row["id"], "error": str(exc)})

    # succeeded is set after batch normalization — energy requires all raw scores
    # before any can be stored, so we cannot count per-track like analyze_bpm does.
    succeeded = 0
    if raw_scores:
        scores = [s[1] for s in raw_scores]
        lo, hi = min(scores), max(scores)

        def _norm(v: float) -> float:
            return (v - lo) / (hi - lo) if hi > lo else 0.5

        db_updates: list[tuple[float, int]] = [(_norm(score), tid) for tid, score in raw_scores]

        _progress(f"Writing {len(db_updates):,} energy values to DB...")
        with connect(db_path, readonly=False) as wconn:
            wconn.executemany("UPDATE tracks SET energy = ? WHERE id = ?", db_updates)
            wconn.commit()
        succeeded = len(db_updates)

    return {
        "mode": mode,
        "total_candidates": total_candidates,
        "processed": total,
        "succeeded": succeeded,
        "errors": len(error_details),
        "error_details": error_details,
    }


def analyze_key(
    db_path: str | None = None,
    apply: bool = False,
    write_tags: bool = False,
    sync_db: bool = True,
    limit: int | None = None,
    force: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    with connect(db_path, readonly=True) as _guard_conn:
        if table_exists(_guard_conn, "library") and not table_exists(_guard_conn, "tracks"):
            raise RuntimeError("Pointed at a Mixxx DB. Run 'multidj import mixxx' first.")
        ensure_not_empty(_guard_conn)

    mode = "apply" if apply else "dry_run"
    where_clause = "1=1" if force else "(key IS NULL OR TRIM(key) = '')"

    candidate_sql = f"""
        SELECT id, artist, title, key, path AS filepath
        FROM tracks
        WHERE {where_clause}
          AND deleted = 0
        ORDER BY artist, title
    """
    if limit is not None:
        candidate_sql += f" LIMIT {int(limit)}"

    count_sql = f"""
        SELECT COUNT(*) FROM tracks
        WHERE {where_clause}
          AND deleted = 0
    """

    with connect(db_path, readonly=True) as conn:
        rows = conn.execute(candidate_sql).fetchall()
        total_candidates = conn.execute(count_sql).fetchone()[0]

    results = []
    error_details = []
    succeeded = 0

    total = len(rows)

    if not apply:
        # Dry-run: list candidates without loading audio (no deps needed).
        _progress(f"Dry-run: {total:,} tracks would be analyzed (run with --apply to process)")
        for row in rows:
            results.append({
                "track_id": row["id"],
                "artist": row["artist"],
                "title": row["title"],
                "filepath": row["filepath"],
                "current_key": row["key"],
                "detected_key": None,
                "file_written": False,
                "db_written": False,
            })
        succeeded = len(results)
    else:
        # Collect (detected_key, track_id) pairs for a single batched DB write.
        db_updates: list[tuple[str, int]] = []

        _progress(f"Analyzing {total:,} tracks — this will take a while...")

        for i, row in enumerate(rows, 1):
            track_id = row["id"]
            filepath = row["filepath"]
            artist = row["artist"] or ""
            title = row["title"] or ""
            label = f"{artist} - {title}".strip(" -") or filepath

            # Always show a compact progress line; verbose shows detected key after
            _progress(f"[{i:{len(str(total))}}/{total}] {label[:60]}", end="")

            entry: dict[str, Any] = {
                "track_id": track_id,
                "artist": row["artist"],
                "title": row["title"],
                "filepath": filepath,
                "detected_key": None,
                "file_written": False,
                "db_written": False,
            }
            try:
                detected = detect_key(filepath)
                entry["detected_key"] = detected

                if write_tags:
                    try:
                        _write_tag(filepath, detected)
                        entry["file_written"] = True
                    except (ValueError, Exception) as exc:
                        entry["file_write_error"] = str(exc)

                if sync_db:
                    db_updates.append((detected, track_id))
                    entry["db_written"] = True

                succeeded += 1
                results.append(entry)

                if verbose:
                    _progress(f"  → {detected}")
                else:
                    _progress("")  # newline after the track label

            except ImportError:
                raise
            except Exception as exc:
                _progress(f"  ERROR: {exc}")
                error_details.append({"track_id": track_id, "error": str(exc)})

        # Single connection for all DB writes.
        if sync_db and db_updates:
            _progress(f"Writing {len(db_updates):,} keys to DB...")
            with connect(db_path, readonly=False) as wconn:
                wconn.executemany("UPDATE tracks SET key = ? WHERE id = ?", db_updates)
                wconn.commit()
            _progress("DB updated.")

    return {
        "mode": mode,
        "total_candidates": total_candidates,
        "processed": len(rows),
        "succeeded": succeeded,
        "errors": len(error_details),
        "results": results,
        "error_details": error_details,
    }
