"""Tests for BeatGrid protobuf encoding (BeatGrid-2.0)."""
import struct
import unittest

from multidj.mixxx_blobs import pack_beatgrid


def _decode_beatgrid(blob: bytes) -> tuple:
    """Decode a BeatGrid-2.0 protobuf BLOB into (bpm, first_beat_frame)."""
    # Field 1: Bpm sub-message (tag=0x0A, wire type 2)
    assert blob[0] == 0x0A, f"Expected 0x0A tag, got {blob[0]:02X}"
    bpm_len = blob[1]
    inner_bpm = blob[2 : 2 + bpm_len]
    # Inner Bpm: field 1 = double (tag=0x09, wire type 1)
    assert inner_bpm[0] == 0x09, f"Expected 0x09 inner tag, got {inner_bpm[0]:02X}"
    bpm = struct.unpack("<d", inner_bpm[1:9])[0]

    # Field 2: Beat sub-message (tag=0x12, wire type 2)
    offset = 2 + bpm_len
    assert blob[offset] == 0x12, f"Expected 0x12 tag, got {blob[offset]:02X}"
    beat_len = blob[offset + 1]
    inner_beat = blob[offset + 2 : offset + 2 + beat_len]
    # Inner Beat: field 1 = varint (tag=0x08, wire type 0)
    assert inner_beat[0] == 0x08, f"Expected 0x08 inner tag, got {inner_beat[0]:02X}"
    # Decode varint
    pos = 1
    frame = 0
    shift = 0
    while pos < len(inner_beat):
        byte = inner_beat[pos]
        frame |= (byte & 0x7F) << shift
        pos += 1
        if not (byte & 0x80):
            break
        shift += 7

    return bpm, frame


class TestPackBeatgrid(unittest.TestCase):
    def test_produces_valid_protobuf(self):
        """Protobuf BLOB starts with 0x0A (BeatGrid.bpm sub-message tag)."""
        for bpm in [128.0, 100.0, 144.0, 75.5, 175.2, 63.9, 0.0]:
            blob = pack_beatgrid(bpm, 0)
            self.assertEqual(blob[0], 0x0A,
                             f"BPM={bpm}: expected proto header 0x0A, got {blob[0]:02X}")

    def test_roundtrip_bpm(self):
        """The BPM value decodes back from protobuf correctly."""
        for bpm in [128.0, 144.0, 100.0, 75.5, 175.2, 99.38, 151.999]:
            blob = pack_beatgrid(bpm, 0)
            decoded_bpm, _ = _decode_beatgrid(blob)
            self.assertAlmostEqual(decoded_bpm, bpm, places=10,
                                   msg=f"BPM={bpm} roundtrips to {decoded_bpm}")

    def test_roundtrip_first_beat_frame(self):
        """The first_beat_frame value decodes back."""
        for bpm, frame in [(128.0, 0), (144.0, 17454), (100.0, 44100)]:
            blob = pack_beatgrid(bpm, frame)
            _, decoded_frame = _decode_beatgrid(blob)
            self.assertEqual(decoded_frame, frame,
                             msg=f"frame={frame} roundtrips to {decoded_frame}")

    def test_specific_bpm_values(self):
        """Verify protobuf structure for known BPM values."""
        blob = pack_beatgrid(128.0, 0)
        decoded_bpm, decoded_frame = _decode_beatgrid(blob)
        self.assertAlmostEqual(decoded_bpm, 128.0, places=1)
        self.assertEqual(decoded_frame, 0)
        self.assertEqual(blob[0], 0x0A)  # proto header
        self.assertNotEqual(blob[:8], struct.pack("<d", 128.0))  # NOT raw struct

    def test_no_zero_blobs(self):
        """Blobs should not be all zeros for any valid BPM > 0."""
        for bpm in [128.0, 1.0, 999.0, 63.9, 151.999]:
            blob = pack_beatgrid(bpm, 0)
            self.assertNotEqual(blob, b"\x00" * len(blob))

    def test_zero_bpm_is_allowed(self):
        """0 BPM should produce a valid protobuf blob."""
        blob = pack_beatgrid(0.0, 0)
        decoded_bpm, decoded_frame = _decode_beatgrid(blob)
        self.assertEqual(decoded_bpm, 0.0)
        self.assertEqual(decoded_frame, 0)

    def test_large_frame_positions(self):
        """Frame positions up to typical track lengths should work."""
        for frame in [0, 100, 100000, 10000000]:
            blob = pack_beatgrid(128.0, frame)
            _, decoded_frame = _decode_beatgrid(blob)
            self.assertEqual(decoded_frame, frame)
