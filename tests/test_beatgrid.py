"""Tests for BeatGrid legacy struct encoding."""
import struct
import unittest

from multidj.mixxx_blobs import pack_beatgrid


class TestPackBeatgrid(unittest.TestCase):
    def test_returns_16_bytes(self):
        """Legacy BeatGrid format must be exactly 16 bytes (2 doubles)."""
        for bpm in [128.0, 100.0, 144.0, 75.5, 175.2, 63.9, 0.0]:
            blob = pack_beatgrid(bpm, 0)
            self.assertEqual(len(blob), 16,
                             f"BPM={bpm} produced {len(blob)} bytes, expected 16")

    def test_roundtrip_bpm(self):
        """The BPM double decodes back to the same value."""
        for bpm in [128.0, 144.0, 100.0, 75.5, 175.2, 99.38, 151.999]:
            blob = pack_beatgrid(bpm, 0)
            decoded_bpm, decoded_frame = struct.unpack("<dd", blob)
            self.assertAlmostEqual(decoded_bpm, bpm, places=10,
                                   msg=f"BPM={bpm} roundtrips to {decoded_bpm}")

    def test_roundtrip_first_beat_frame(self):
        """The first_beat_frame double decodes back."""
        for bpm, frame in [(128.0, 0), (144.0, 17454), (100.0, 44100)]:
            blob = pack_beatgrid(bpm, frame)
            decoded_bpm, decoded_frame = struct.unpack("<dd", blob)
            self.assertAlmostEqual(decoded_frame, float(frame), places=0,
                                   msg=f"frame={frame} roundtrips to {decoded_frame}")

    def test_specific_bpm_values(self):
        """Known BPM values produce expected byte patterns."""
        # 128.0 as IEEE 754 LE double
        expected_bpm = struct.pack("<d", 128.0)
        blob = pack_beatgrid(128.0, 0)
        self.assertEqual(blob[:8], expected_bpm)
        self.assertEqual(blob[8:], struct.pack("<d", 0.0))

    def test_no_zero_blobs(self):
        """Blobs should not be all zeros for any valid BPM > 0."""
        for bpm in [128.0, 1.0, 999.0, 63.9, 151.999]:
            blob = pack_beatgrid(bpm, 0)
            self.assertNotEqual(blob, b"\x00" * 16)

    def test_zero_bpm_is_allowed(self):
        """0 BPM should produce a valid blob."""
        blob = pack_beatgrid(0.0, 0)
        self.assertEqual(len(blob), 16)
        decoded_bpm, decoded_frame = struct.unpack("<dd", blob)
        self.assertEqual(decoded_bpm, 0.0)
        self.assertEqual(decoded_frame, 0.0)

    def test_large_frame_positions(self):
        """Frame positions up to typical track lengths should work."""
        for frame in [0, 100, 100000, 10000000]:
            blob = pack_beatgrid(128.0, frame)
            _, decoded_frame = struct.unpack("<dd", blob)
            self.assertEqual(decoded_frame, float(frame))
