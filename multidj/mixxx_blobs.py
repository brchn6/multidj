"""Generate Mixxx-compatible protobuf BLOBs for pre-analysis.

Writes BeatGrid-2.0 and KeyMap-1.0 protobuf BLOBs directly into the Mixxx
SQLite database so Mixxx opens with BPM/key analysis already done and skips
re-analysis.

All BLOBs are Google Protocol Buffers (proto2, LITE_RUNTIME) encoded manually
— no protobuf dependency required.
"""

from __future__ import annotations

import sqlite3
import struct
import sys
from pathlib import Path
from typing import Any

from .db import connect as multidj_connect
from .db import ensure_not_empty
from .db import MIXXX_DB_PATH

# ── Protobuf helpers ──────────────────────────────────────────────────────────


def varint_encode(value: int) -> bytes:
    """Encode unsigned integer as protobuf varint (7-bit LE groups)."""
    result: list[int] = []
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _wire_tag(field_number: int, wire_type: int) -> bytes:
    """Protobuf field tag: (field_number << 3) | wire_type."""
    return varint_encode((field_number << 3) | wire_type)


def _encode_varint(field_number: int, value: int) -> bytes:
    """Encode a varint (int32/bool/enum) field."""
    return _wire_tag(field_number, 0) + varint_encode(value)


def _encode_double(field_number: int, value: float) -> bytes:
    """Encode a double (fixed64) field."""
    return _wire_tag(field_number, 1) + struct.pack("<d", value)


def _encode_string(field_number: int, s: str) -> bytes:
    """Encode a string (length-delimited) field."""
    encoded = s.encode("utf-8")
    return _wire_tag(field_number, 2) + varint_encode(len(encoded)) + encoded


def _encode_submsg(field_number: int, inner: bytes) -> bytes:
    """Encode a nested sub-message (length-delimited) field."""
    return _wire_tag(field_number, 2) + varint_encode(len(inner)) + inner


# ── Version strings (must match installed Mixxx) ──────────────────────────────

BEATGRID_VERSION = "BeatGrid-2.0"

KEYMAP_VERSION = "KeyMap-1.0"

# ── ChromaticKey enum mapping ────────────────────────────────────────────────

# Maps key strings in MultiDJ format ("Cmaj", "Amin", "F#min", etc.)
# to Mixxx's ChromaticKey enum values (1-24).
# 0 = INVALID (used when key cannot be mapped).
KEY_TO_CHROMATIC: dict[str, int] = {
    # Major keys
    "Cmaj": 1,
    "C#maj": 2,
    "Dbmaj": 2,
    "Dmaj": 3,
    "D#maj": 4,
    "Ebmaj": 4,
    "Emaj": 5,
    "Fmaj": 6,
    "F#maj": 7,
    "Gbmaj": 7,
    "Gmaj": 8,
    "G#maj": 9,
    "Abmaj": 9,
    "Amaj": 10,
    "A#maj": 11,
    "Bbmaj": 11,
    "Bmaj": 12,
    # Minor keys
    "Cmin": 13,
    "C#min": 14,
    "Dbmin": 14,
    "Dmin": 15,
    "D#min": 16,
    "Ebmin": 16,
    "Emin": 17,
    "Fmin": 18,
    "F#min": 19,
    "Gbmin": 19,
    "Gmin": 20,
    "G#min": 21,
    "Abmin": 21,
    "Amin": 22,
    "A#min": 23,
    "Bbmin": 23,
    "Bmin": 24,
}

# ChromaticKey enum value → display key text ("Gmaj", "F#min", etc.)
_CHROMATIC_TO_KEY_TEXT: dict[int, str] = {
    1: "Cmaj", 2: "C#maj", 3: "Dmaj", 4: "D#maj", 5: "Emaj",
    6: "Fmaj", 7: "F#maj", 8: "Gmaj", 9: "G#maj", 10: "Amaj",
    11: "A#maj", 12: "Bmaj",
    13: "Cmin", 14: "C#min", 15: "Dmin", 16: "D#min", 17: "Emin",
    18: "Fmin", 19: "F#min", 20: "Gmin", 21: "G#min", 22: "Amin",
    23: "A#min", 24: "Bmin",
}


def chromatic_to_key_text(chromatic_key: int) -> str:
    """Convert ChromaticKey enum value to display key text."""
    return _CHROMATIC_TO_KEY_TEXT.get(chromatic_key, "")


# ── BLOB packers ─────────────────────────────────────────────────────────────


def pack_beatgrid(bpm: float, first_beat_frame: int = 0) -> bytes:
    """Pack a valid BeatGrid-2.0 protobuf BLOB.

    Schema (mixxx.track.io.BeatGrid, proto2 LITE_RUNTIME):
        message BeatGrid {
            optional Bpm bpm = 1;
            optional Beat first_beat = 2;
        }
        message Bpm   { optional double bpm = 1; }
        message Beat  { optional int32 frame_position = 1; }

    All optional fields with default values (enabled=true, source=ANALYZER=0)
    are omitted to produce the minimal valid message.

    Args:
        bpm: BPM value (double).
        first_beat_frame: Frame position of the first downbeat.
                          Default 0 means "first downbeat at sample 0".

    Returns:
        Valid protobuf bytes (typically 15 bytes for first_beat=0).
    """
    # Inner Bpm: double bpm = 1, wire type 1 (fixed64)
    inner_bpm = _encode_double(1, bpm)
    # Outer: BeatGrid.bpm = 1, wire type 2 (length-delimited sub-message)
    bpm_msg = _encode_submsg(1, inner_bpm)

    # Inner Beat: int32 frame_position = 1, wire type 0 (varint)
    inner_beat = _encode_varint(1, first_beat_frame)
    # Outer: BeatGrid.first_beat = 2, wire type 2 (length-delimited sub-message)
    beat_msg = _encode_submsg(2, inner_beat)

    return bpm_msg + beat_msg


def pack_keymap(
    global_key: int,
    global_key_text: str = "",
    source: int = 0,
    tuning_frequency_hz: float = 0.0,
) -> bytes:
    """Pack a KeyMap-1.0 protobuf BLOB.

    Args:
        global_key: ChromaticKey enum value (1-24). 0=INVALID (won't be
                    written since default is omitted).
        global_key_text: Human-readable key name ("Am", "F#m", "Gmaj").
        source: 0=ANALYZER, 1=FILE_METADATA, 2=USER.
        tuning_frequency_hz: Detected tuning in Hz (0.0 = not detected).

    Returns:
        Raw protobuf bytes for the KeyMap message (typically 6 bytes).

    Proto schema (mixxx.track.io.key.KeyMap):
        optional ChromaticKey global_key = 1 [ default = INVALID ];
        optional string global_key_text = 2;
        optional Source source = 4 [ default = ANALYZER ];
        optional double global_tuning_frequency_hz = 5 [ default = 0.0 ];
    """
    result = b""

    if global_key != 0:
        result += _encode_varint(1, global_key)

    if global_key_text:
        result += _encode_string(2, global_key_text)

    if source != 0:
        result += _encode_varint(4, source)

    if tuning_frequency_hz != 0.0:
        result += _encode_double(5, tuning_frequency_hz)

    return result


# ── Key utility ──────────────────────────────────────────────────────────────


def key_string_to_chromatic(key_str: str) -> int:
    """Convert a MultiDJ key string to Mixxx ChromaticKey enum value.

    Handles "Cmaj", "C#maj", "Dbmaj", "Amin", "F#min", etc.
    Returns 0 (INVALID) if the key string cannot be mapped.

    Also accepts stripped/whitespace-padded strings.
    """
    if not key_str:
        return 0
    cleaned = " ".join(key_str.strip().split())
    return KEY_TO_CHROMATIC.get(cleaned, 0)


# ── Analysis command ─────────────────────────────────────────────────────────


def analyze_mixxx_blobs(
    multidj_db_path: str | None = None,
    mixxx_db_path: str | None = None,
    *,
    apply: bool = False,
    force: bool = False,
    lock_bpm: bool = False,
    write_beats: bool = True,
    limit: int | None = None,
    backup_dir: str | None = None,
) -> dict[str, Any]:
    """Write Mixxx analysis BLOBs from MultiDJ data into the Mixxx DB.

    Reads BPM/key from MultiDJ tracks table, packs corresponding
    BeatGrid-2.0 / KeyMap-1.0 protobuf BLOBs, and writes them
    directly into the Mixxx library table.

    Args:
        multidj_db_path: Path to MultiDJ SQLite DB.
        mixxx_db_path: Path to Mixxx SQLite DB.
        apply: If False (default), dry-run: report what would change.
        lock_bpm: If True, set bpm_lock=1 on written tracks.
        limit: Cap number of tracks processed (for testing).
        backup_dir: Directory for Mixxx DB backup, or False/None to skip.

    Returns:
        Dict with counts and per-track details.
    """
    if not mixxx_db_path:
        mixxx_path = MIXXX_DB_PATH
    else:
        mixxx_path = Path(mixxx_db_path).expanduser()
    if not mixxx_path.exists():
        return {
            "status": "error",
            "reason": f"Mixxx DB not found: {mixxx_path}",
        }

    # Backup Mixxx DB if writing
    if apply and backup_dir is not False:
        from .backup import create_backup
        try:
            create_backup(str(mixxx_path), backup_dir)
        except Exception as exc:
            return {
                "status": "error",
                "reason": f"Backup failed: {exc}",
            }

    try:
        with multidj_connect(multidj_db_path, readonly=True) as multidj_conn:
            ensure_not_empty(multidj_conn)

            # Read tracks with BPM and/or key from MultiDJ DB
            sql = """
                SELECT id, path, bpm, key, artist, title
                FROM tracks
                WHERE deleted = 0
                  AND path IS NOT NULL
                  AND (bpm IS NOT NULL AND bpm > 0 OR key IS NOT NULL AND TRIM(key) != '')
                ORDER BY id
            """
            if limit is not None:
                sql += f" LIMIT {int(limit)}"

            rows = multidj_conn.execute(sql).fetchall()
    except FileNotFoundError as exc:
        return {"status": "error", "reason": str(exc)}
    except RuntimeError as exc:
        return {"status": "error", "reason": str(exc)}

    total_candidates = len(rows)

    # Open Mixxx DB
    try:
        if apply:
            mixxx_conn = sqlite3.connect(str(mixxx_path))
        else:
            mixxx_conn = sqlite3.connect(f"file:{mixxx_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {"status": "error", "reason": f"Cannot open Mixxx DB: {exc}"}

    beat_written = 0
    key_written = 0
    skipped_no_match = 0
    skipped_no_bpm = 0
    skipped_no_key = 0
    skipped_bad_key = 0
    errors: list[dict] = []
    details: list[dict] = []

    try:
        for row in rows:
            track_id = row["id"]
            path = row["path"]
            bpm = row["bpm"]
            key_str = row["key"]
            artist = row["artist"] or ""
            title = row["title"] or ""

            # Look up Mixxx library row by file path; also fetch existing analysis state
            lib_row = mixxx_conn.execute(
                """SELECT l.id, l.beats IS NOT NULL AS has_beats, l.keys IS NOT NULL AS has_keys
                   FROM library l
                   JOIN track_locations tl ON l.location = tl.id
                   WHERE tl.location = ?""",
                (path,),
            ).fetchone()

            if not lib_row:
                skipped_no_match += 1
                continue

            lib_id = lib_row[0]
            has_beats = bool(lib_row[1])
            has_keys = bool(lib_row[2])
            wrote_beat = False
            wrote_key = False
            beat_blob = None
            key_blob = None
            chromatic_key = 0
            key_text = ""

            # BeatGrid BLOB — only when write_beats=True and bpm present
            if bpm and bpm > 0 and write_beats and (force or not has_beats):
                beat_blob = pack_beatgrid(float(bpm))
                wrote_beat = True

            # BPM sync + lock — always apply when bpm present, even without blob
            bpm_sync = bpm is not None and float(bpm) > 0
            bpm_needs_lock = bpm_sync and lock_bpm
            bpm_needs_update = bpm_sync and (force or not has_beats)

            # KeyMap BLOB — skip if Mixxx already has one, unless --force
            if key_str and key_str.strip() and (force or not has_keys):
                chromatic_key = key_string_to_chromatic(key_str)
                if chromatic_key == 0:
                    skipped_bad_key += 1
                else:
                    key_text = key_str.strip()
                    key_blob = pack_keymap(chromatic_key, key_text)

            if not (wrote_beat or key_blob or bpm_needs_update):
                continue

            detail: dict[str, Any] = {
                "track_id": track_id,
                "path": path,
                "artist": artist,
                "title": title,
                "bpm": bpm,
                "wrote_beat": wrote_beat,
                "wrote_key": key_blob is not None,
            }

            if apply:
                try:
                    if wrote_beat and beat_blob is not None:
                        mixxx_conn.execute(
                            """UPDATE library SET
                               beats = ?, beats_version = ?, beats_sub_version = NULL,
                               bpm = ?, bpm_lock = ?
                               WHERE id = ?""",
                            (beat_blob, BEATGRID_VERSION, bpm, 1 if lock_bpm else 0, lib_id),
                        )
                        beat_written += 1
                    elif bpm_needs_update:
                        # Sync BPM + lock without writing BeatGrid blob
                        mixxx_conn.execute(
                            """UPDATE library SET
                               bpm = ?, bpm_lock = ?
                               WHERE id = ?""",
                            (bpm, 1 if bpm_needs_lock else 0, lib_id),
                        )
                        beat_written += 1

                    if key_blob:
                        mixxx_conn.execute(
                            """UPDATE library SET
                               keys = ?, keys_version = ?, keys_sub_version = NULL,
                               key_id = ?, key = ?
                               WHERE id = ?""",
                            (
                                key_blob,
                                KEYMAP_VERSION,
                                chromatic_key,
                                key_text,
                                lib_id,
                            ),
                        )
                        key_written += 1
                except sqlite3.Error as exc:
                    errors.append(
                        {
                            "track_id": track_id,
                            "path": path,
                            "error": str(exc),
                        }
                    )
                    continue
            else:
                # Dry-run: just count
                if wrote_beat:
                    beat_written += 1
                if key_blob:
                    key_written += 1

            details.append(detail)

        if apply:
            mixxx_conn.commit()
    finally:
        mixxx_conn.close()

    return {
        "status": "ok",
        "total_candidates": total_candidates,
        "beat_written": beat_written,
        "key_written": key_written,
        "skipped_no_match": skipped_no_match,
        "skipped_bad_key": skipped_bad_key,
        "errors": errors,
        "details": details,
        "applied": apply,
    }


def _progress(msg: str, end: str = "\n") -> None:
    print(msg, file=sys.stderr, end=end, flush=True)
