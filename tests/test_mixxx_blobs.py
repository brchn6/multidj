"""Tests for pack_beatgrid() protobuf encoding fix.

Verifies that pack_beatgrid() produces valid BeatGrid-2.0 protobuf BLOBs
matching Mixxx's own output bit-for-bit.
"""

from __future__ import annotations

import struct

from multidj.mixxx_blobs import pack_beatgrid


class TestPackBeatgrid:
    """Tests for the corrected pack_beatgrid() function."""

    def test_produces_valid_protobuf_header(self):
        """First byte must be 0x0A (BeatGrid.bpm field tag, wire type 2)."""
        blob = pack_beatgrid(128.0, 0)
        assert blob[0] == 0x0A, (
            f"Expected first byte 0x0A (proto sub-message tag), "
            f"got 0x{blob[0]:02X}"
        )

    def test_matches_real_mixxx_blob_155bpm(self):
        """Bit-for-bit match with a real Mixxx-produced BLOB at 155 BPM."""
        blob = pack_beatgrid(155.0, 3128)
        expected = bytes.fromhex("0A09090000000000606340120308B818")
        assert blob == expected, (
            f"BLOB mismatch:\n"
            f"  got:      {blob.hex().upper()}\n"
            f"  expected: {expected.hex().upper()}"
        )

    def test_matches_real_mixxx_blob_142bpm(self):
        """Bit-for-bit match with a real Mixxx-produced BLOB at 142 BPM."""
        blob = pack_beatgrid(142.0, 12721)
        expected = bytes.fromhex("0A09090000000000C06140120308B163")
        assert blob == expected

    def test_matches_real_mixxx_blob_140bpm(self):
        """Bit-for-bit match with a real Mixxx-produced BLOB at 140 BPM."""
        blob = pack_beatgrid(140.0, 15396)
        expected = bytes.fromhex("0A09090000000000806140120308A478")
        assert blob == expected

    def test_zero_first_beat_frames(self):
        """First beat at frame 0 produces minimal valid message."""
        blob = pack_beatgrid(128.0, 0)
        # Should be: bpm sub-message + first_beat sub-message (frame=0)
        assert len(blob) == 15, f"Expected 15 bytes, got {len(blob)}"
        # The first_beat sub-message should encode frame_position=0 as varint 0x00
        # tag 0x12, length 0x02, inner tag 0x08, value 0x00
        assert blob.endswith(b"\x12\x02\x08\x00"), (
            f"Expected trailing bytes 12020800, got {blob[-4:].hex()}"
        )

    def test_large_first_beat_frame(self):
        """Large frame positions use multi-byte varint correctly."""
        blob = pack_beatgrid(100.0, 100000)
        # Frame 100000 requires 3-byte varint encoding
        # varint(100000) = 0xA0 0x8D 0x06
        assert b"\xA0\x8D\x06" in blob, (
            f"Expected varint for 100000 not found in {blob.hex()}"
        )

    def test_not_legacy_struct(self):
        """Must NOT produce the old legacy 16-byte raw struct format."""
        blob = pack_beatgrid(128.0, 0)
        old_style = struct.pack("<dd", 128.0, 0.0)
        assert blob != old_style, (
            "pack_beatgrid is still producing legacy raw struct, not protobuf!"
        )
        assert len(blob) != 16, (
            "Legacy format was exactly 16 bytes; protobuf should differ"
        )

    def test_different_bpm_values(self):
        """Verify encoding is correct across a range of BPM values."""
        for bpm in [60.0, 90.5, 128.0, 140.0, 174.0]:
            blob = pack_beatgrid(bpm, 0)
            assert blob[0] == 0x0A, f"BPM={bpm}: invalid header"
            # Decode the BPM from the protobuf to verify round-trip
            bpm_len = blob[1]
            inner = blob[2 : 2 + bpm_len]
            assert inner[0] == 0x09  # Bpm.bpm field tag (fixed64)
            parsed = struct.unpack("<d", inner[1:9])[0]
            assert abs(parsed - bpm) < 0.01, (
                f"BPM round-trip failed: {bpm} → {parsed}"
            )


def test_new_track_gets_blob_only_after_sync(tmp_path, multidj_db, mixxx_db):
    """Regression test: new tracks (dir-imported, not yet in Mixxx) only get a
    BeatGrid-2.0 BLOB once sync has pushed them to Mixxx first.

    This is the bug fixed by moving mixxx_blobs to Phase 4 (after sync):
      1. mixxx_blobs before sync → no BLOB (track not in Mixxx yet)
      2. sync → track in Mixxx with raw BPM float, beats=NULL
      3. mixxx_blobs after sync → BeatGrid-2.0 BLOB written ✓
    """
    import sqlite3
    from multidj.adapters.mixxx import MixxxAdapter
    from multidj.mixxx_blobs import analyze_mixxx_blobs

    NEW_PATH = str(tmp_path / "new_dir_import_track.mp3")
    NEW_BPM = 128.0

    # Insert the new track into MultiDJ (simulates directory import — NOT in Mixxx yet)
    mdj = sqlite3.connect(str(multidj_db))
    mdj.execute(
        "INSERT INTO tracks (path, artist, title, bpm, duration, filesize, deleted)"
        " VALUES (?, 'New Artist', 'New Track', ?, 240.0, 1000000, 0)",
        (NEW_PATH, NEW_BPM),
    )
    track_id = mdj.execute("SELECT id FROM tracks WHERE path=?", (NEW_PATH,)).fetchone()[0]
    mdj.execute(
        "INSERT INTO sync_state (track_id, adapter, dirty) VALUES (?, 'mixxx', 1)",
        (track_id,),
    )
    mdj.commit()
    mdj.close()

    def _mixxx_row():
        conn = sqlite3.connect(str(mixxx_db))
        row = conn.execute(
            "SELECT l.bpm, l.beats, l.beats_version"
            " FROM library l JOIN track_locations tl ON l.location = tl.id"
            " WHERE tl.location = ?",
            (NEW_PATH,),
        ).fetchone()
        conn.close()
        return row

    # ── Step 1: mixxx_blobs BEFORE sync → track not in Mixxx, BLOB not written ──
    analyze_mixxx_blobs(
        multidj_db_path=str(multidj_db),
        mixxx_db_path=str(mixxx_db),
        apply=True,
    )
    assert _mixxx_row() is None, "Track must not be in Mixxx before sync"

    # ── Step 2: sync → track added to Mixxx with raw BPM, beats=NULL ─────────
    MixxxAdapter(mixxx_db_path=str(mixxx_db)).full_sync(
        multidj_db_path=str(multidj_db), apply=True
    )
    row = _mixxx_row()
    assert row is not None, "Track must be in Mixxx after sync"
    assert row[0] == NEW_BPM, f"Expected BPM {NEW_BPM}, got {row[0]}"
    assert row[1] is None, "beats BLOB must be NULL immediately after sync"

    # ── Step 3: mixxx_blobs AFTER sync → BeatGrid-2.0 BLOB written ───────────
    analyze_mixxx_blobs(
        multidj_db_path=str(multidj_db),
        mixxx_db_path=str(mixxx_db),
        apply=True,
    )
    row = _mixxx_row()
    assert row[1] is not None, "BeatGrid BLOB must be written when mixxx_blobs runs after sync"
    assert row[1][0] == 0x0A, "BLOB must start with 0x0A (BeatGrid-2.0 protobuf header)"
    assert row[2] == "BeatGrid-2.0", f"beats_version must be 'BeatGrid-2.0', got {row[2]}"


def test_mixxx_blobs_logs_skipped_for_existing_beats(multidj_db, mixxx_db, tmp_path):
    """analyze_mixxx_blobs logs SKIPPED when Mixxx already has a BeatGrid."""
    import sqlite3
    import sys
    from io import StringIO

    conn = sqlite3.connect(str(mixxx_db))
    conn.execute("UPDATE library SET beats = ? WHERE id = 1", (b"fake_beats_blob",))
    conn.commit()
    conn.close()

    captured = StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured
    try:
        from multidj.mixxx_blobs import analyze_mixxx_blobs
        analyze_mixxx_blobs(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=False,
        )
    finally:
        sys.stderr = old_stderr
    output = captured.getvalue()
    assert "SKIPPED" in output


def test_mixxx_blobs_logs_wrote_for_new_track(multidj_db, mixxx_db, tmp_path):
    """analyze_mixxx_blobs logs WROTE when writing a new BeatGrid."""
    import sys
    from io import StringIO

    captured = StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured
    try:
        from multidj.mixxx_blobs import analyze_mixxx_blobs
        analyze_mixxx_blobs(
            multidj_db_path=str(multidj_db),
            mixxx_db_path=str(mixxx_db),
            apply=True,
        )
    finally:
        sys.stderr = old_stderr
    output = captured.getvalue()
    assert "WROTE" in output
