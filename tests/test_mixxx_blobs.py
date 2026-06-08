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
