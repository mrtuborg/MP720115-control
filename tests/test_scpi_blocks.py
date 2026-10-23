"""
Unit tests for IEEE 488.2 definite-length block parsing and
:WAVeform:PREamble? parsing/scaling. Standard library unittest only.

Run with: python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from mp72_control.scpi_blocks import (  # noqa: E402
    IEEEBlockError,
    build_ieee_block,
    header_total_len,
    parse_ieee_block,
    parse_ieee_block_with_trailer,
    parse_preamble,
    peek_declared_length,
)


class TestBuildParseRoundTrip(unittest.TestCase):
    def test_round_trip_small_payload(self):
        payload = bytes(range(16))
        block = build_ieee_block(payload)
        self.assertEqual(parse_ieee_block(block), payload)

    def test_round_trip_empty_payload(self):
        block = build_ieee_block(b"")
        self.assertEqual(parse_ieee_block(block), b"")

    def test_manual_example_123_bytes(self):
        # Manual page 6 example: #3123 + 123 bytes of data.
        payload = bytes([0xAA] * 123)
        block = b"#3123" + payload
        self.assertEqual(parse_ieee_block(block), payload)

    def test_manual_example_display_data_header(self):
        # Manual page 62 example literal header text: "#800012345+bitmap data"
        # i.e. n=8 digits of length follow ("00012345" = 12345 bytes).
        payload = bytes([0x42] * 12345)
        block = b"#800012345" + payload
        self.assertEqual(parse_ieee_block(block), payload)

    def test_trailing_newline_is_returned_as_trailer_not_payload(self):
        payload = b"hello world"
        block = build_ieee_block(payload) + b"\n"
        parsed_payload, trailer = parse_ieee_block_with_trailer(block)
        self.assertEqual(parsed_payload, payload)
        self.assertEqual(trailer, b"\n")

    def test_binary_payload_with_embedded_newlines_and_nulls(self):
        # Binary waveform/BMP data must not be mistaken for text and must
        # not be truncated at an embedded \n or \x00.
        payload = bytes([0, 10, 13, 0, 255, 10, 0]) * 5
        block = build_ieee_block(payload)
        self.assertEqual(parse_ieee_block(block), payload)


class TestMalformedBlocks(unittest.TestCase):
    def test_missing_hash_prefix(self):
        with self.assertRaises(IEEEBlockError):
            parse_ieee_block(b"123abc")

    def test_too_short(self):
        with self.assertRaises(IEEEBlockError):
            parse_ieee_block(b"#")

    def test_non_digit_length_of_length(self):
        with self.assertRaises(IEEEBlockError):
            parse_ieee_block(b"#Xabc")

    def test_indefinite_length_block_rejected(self):
        with self.assertRaises(IEEEBlockError):
            parse_ieee_block(b"#0" + b"somepayload\n")

    def test_truncated_header(self):
        # n=5 digits of length promised, but only 2 given before EOF.
        with self.assertRaises(IEEEBlockError):
            parse_ieee_block(b"#512")

    def test_non_digit_length_field(self):
        with self.assertRaises(IEEEBlockError):
            parse_ieee_block(b"#3abc" + b"x" * 10)

    def test_declared_length_exceeds_available_data(self):
        with self.assertRaises(IEEEBlockError):
            parse_ieee_block(b"#3100" + b"short")

    def test_rejects_non_bytes_input(self):
        with self.assertRaises(IEEEBlockError):
            parse_ieee_block("#3123" + "x" * 123)  # type: ignore[arg-type]


class TestStreamingHelpers(unittest.TestCase):
    """Exercises the helpers used by the chunked VXI-11 reader to know how
    many more bytes to request before the full header is available."""

    def test_header_total_len_needs_more_bytes(self):
        self.assertIsNone(header_total_len(b"#"))

    def test_header_total_len_known_after_two_bytes(self):
        self.assertEqual(header_total_len(b"#3123..."), 5)

    def test_peek_declared_length_incremental(self):
        full_header = b"#5" + b"00123"
        # Not enough bytes yet for the 5-digit length field.
        self.assertIsNone(peek_declared_length(full_header[:4]))
        # All header bytes present -> length decodable.
        self.assertEqual(peek_declared_length(full_header), 123)

    def test_peek_declared_length_rejects_indefinite(self):
        with self.assertRaises(IEEEBlockError):
            peek_declared_length(b"#0")


class TestPreambleParsing(unittest.TestCase):
    def test_manual_documented_example(self):
        # Manual page 67 exact example:
        # "return 1,0,0,1,8.000e-009,-6.000e-006,0,4.000e-002,0.000e000,100"
        p = parse_preamble("1,0,0,1,8.000e-009,-6.000e-006,0,4.000e-002,0.000e000,100")
        self.assertEqual(p.format_code, 1)
        self.assertEqual(p.format_name, "WORD")
        self.assertEqual(p.type_code, 0)
        self.assertEqual(p.type_name, "NORMAL")
        self.assertEqual(p.points, 0)
        self.assertEqual(p.count, 1)
        self.assertAlmostEqual(p.x_increment, 8e-9)
        self.assertAlmostEqual(p.x_origin, -6e-6)
        self.assertEqual(p.x_reference, 0)
        self.assertAlmostEqual(p.y_increment, 4e-2)
        self.assertEqual(p.y_origin, 0)
        self.assertEqual(p.y_reference, 100)

    def test_wrong_field_count_raises(self):
        with self.assertRaises(ValueError):
            parse_preamble("1,0,0,1,2,3")

    def test_non_numeric_field_raises(self):
        with self.assertRaises(ValueError):
            parse_preamble("1,0,0,1,x,0,0,0,0,0")

    def test_code_to_volts_uses_manual_semantics(self):
        p = parse_preamble("0,0,120,1,1.0e-6,0.0,0,4.000e-002,1.5,100")
        # volts = (raw_code - y_reference) * y_increment - y_origin
        self.assertAlmostEqual(p.code_to_volts(100), (100 - 100) * 0.04 - 1.5)
        self.assertAlmostEqual(p.code_to_volts(228), (228 - 100) * 0.04 - 1.5)
        self.assertAlmostEqual(p.code_to_volts(0), (0 - 100) * 0.04 - 1.5)

    def test_code_to_time_uses_manual_semantics(self):
        p = parse_preamble("0,0,120,1,2.0e-3,-1.0,0,1,0,0")
        # t = (index - x_reference) * x_increment + x_origin
        self.assertAlmostEqual(p.code_to_time(0), (0 - 0) * 2e-3 + (-1.0))
        self.assertAlmostEqual(p.code_to_time(10), (10 - 0) * 2e-3 + (-1.0))

    def test_unknown_format_type_codes_labeled_unknown(self):
        p = parse_preamble("9,9,0,1,1,0,0,1,0,0")
        self.assertIn("UNKNOWN", p.format_name)
        self.assertIn("UNKNOWN", p.type_name)


if __name__ == "__main__":
    unittest.main()
