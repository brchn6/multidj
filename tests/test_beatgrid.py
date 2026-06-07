"""Tests for BeatGrid-2.0 protobuf encoding."""
import struct
import unittest

from multidj.mixxx_blobs import pack_beatgrid, varint_encode


class TestVarintEncode(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(varint_encode(0), b"\x00")

    def test_small(self):
        self.assertEqual(varint_encode(9), b"\x09")
        self.assertEqual(varint_encode(2), b"\x02")
        self.assertEqual(varint_encode(127), b"\x7f")

    def test_medium(self):
        # 128 → 0x80 0x01
        self.assertEqual(varint_encode(128), b"\x80\x01")
        # 17454 → 0xAE 0x88 0x01
        self.assertEqual(varint_encode(17454), b"\xae\x88\x01")

    def test_large(self):
        # 300 → 0xAC 0x02
        self.assertEqual(varint_encode(300), b"\xac\x02")


class TestPackBeatgrid(unittest.TestCase):
    # Known-good Mixxx blobs verified against the user's DB
    # BPM 144.0, first_beat_frame=0
    MIXED_BLOB_144 = bytes.fromhex("0a0909000000000000624012020800")
    # BPM 125.333, first_beat_frame=17454
    MIXED_BLOB_125_333 = bytes.fromhex("0a09090000006055555f40120408ae8801")

    def test_bpm_144_matches_mixxx(self):
        """Our encoding matches a known-good Mixxx blob byte-for-byte."""
        blob = pack_beatgrid(144.0, 0)
        self.assertEqual(blob, self.MIXED_BLOB_144)

    def test_first_beat_frame_encoding(self):
        """Non-zero first_beat_frame encodes correctly in varint."""
        blob = pack_beatgrid(125.33333587646484, 17454)
        self.assertEqual(blob, self.MIXED_BLOB_125_333)

    def test_blob_has_correct_structure(self):
        """Every blob must have correct protobuf field tags and lengths."""
        for bpm in [128.0, 100.0, 75.5, 175.2, 99.38, 33.3333]:
            blob = pack_beatgrid(bpm, 0)
            self.assertEqual(len(blob), 15,
                             f"BPM={bpm} produced {len(blob)} bytes, expected 15")
            # First byte: 0x0A = field 1, wire type 2 (length-delimited)
            self.assertEqual(blob[0], 0x0A)
            # Second byte: 0x09 = length 9 (for inner Bpm message)
            self.assertEqual(blob[1], 0x09)
            # Bytes 11-12: 0x12 0x02 = field 2, wire type 2, length 2
            self.assertEqual(blob[11], 0x12)
            self.assertEqual(blob[12], 0x02)
            # Byte 13: 0x08 = field 1, wire type 0 (varint) in Beat message
            self.assertEqual(blob[13], 0x08)
            # Byte 14: 0x00 = varint 0 (frame_position=0)
            self.assertEqual(blob[14], 0x00)

    def test_roundtrip_bpm(self):
        """The BPM stored in the blob decodes back to the same value."""
        for bpm in [128.0, 144.0, 100.0, 75.5, 175.2, 99.38]:
            blob = pack_beatgrid(bpm, 0)
            # Extract the 8-byte double from bytes 3-10
            double_bytes = blob[3:11]
            decoded = struct.unpack("<d", double_bytes)[0]
            self.assertAlmostEqual(decoded, bpm, places=10,
                                   msg=f"BPM={bpm} roundtrips to {decoded}")

    def test_no_zero_blobs(self):
        """Blobs should not be all zeros for any valid BPM."""
        for bpm in [128.0, 1.0, 999.0, 63.9, 151.999]:
            blob = pack_beatgrid(bpm, 0)
            self.assertNotEqual(blob, b"\x00" * len(blob))

    def test_reasonable_length(self):
        """Blobs should be 14-18 bytes for typical inputs."""
        for bpm in [128.0, 144.0, 100.0, 75.5, 175.2, 63.9]:
            blob = pack_beatgrid(bpm, 0)
            self.assertTrue(14 <= len(blob) <= 18,
                            f"BPM={bpm}: len={len(blob)}, expected 14-18")

    def test_byte_positions(self):
        """Specific byte positions must always be the same values."""
        for bpm in [50.0, 100.0, 150.0]:
            blob = pack_beatgrid(bpm, 0)
            # byte 0: field 1 tag for BeatGrid (length-delimited)
            self.assertEqual(blob[0], 0x0A)
            # byte 1: length of Bpm sub-message (always 9)
            self.assertEqual(blob[1], 0x09)
            # byte 2: field 1 tag for Bpm (fixed64)
            self.assertEqual(blob[2], 0x09)
            # byte 11: field 2 tag for BeatGrid.first_beat (length-delimited)
            self.assertEqual(blob[11], 0x12)
