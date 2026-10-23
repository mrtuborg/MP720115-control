"""
Focused tests for MP720115 controller validation/command-building logic
using a mock transport (no real instrument/pyvisa required). Complements
test_scpi_blocks.py, which covers the IEEE 488.2 block/preamble parsing in
isolation.

Run with: python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from mp72_control.controller import MP720115  # noqa: E402
from mp72_control.exceptions import ValidationError  # noqa: E402
from mp72_control.scpi_blocks import build_ieee_block  # noqa: E402


class MockResource:
    """Minimal PyVISA-like mock: records writes, answers canned queries."""

    def __init__(self):
        self.timeout = 5000
        self.written = []
        self.closed = False
        self.query_responses = {}
        self._next_raw = b""
        self._remaining_raw = b""

    def write(self, cmd):
        self.written.append(cmd)
        return len(cmd)

    def query(self, cmd):
        self.written.append(cmd)
        if cmd in self.query_responses:
            return self.query_responses[cmd]
        raise AssertionError(f"MockResource: no canned response for query {cmd!r}")

    def read_raw(self):
        return self._next_raw

    def read_bytes(self, count, chunk_size=None, break_on_termchar=False):
        data = self._remaining_raw[:count]
        self._remaining_raw = self._remaining_raw[count:]
        return data

    def close(self):
        self.closed = True


class TestValidationGuards(unittest.TestCase):
    def setUp(self):
        self.res = MockResource()
        self.scope = MP720115(self.res)

    def test_invalid_channel_coupling_rejected_before_write(self):
        with self.assertRaises(ValidationError):
            self.scope.set_channel_coupling(1, "WEIRD")
        self.assertEqual(self.res.written, [], "no SCPI should be sent on validation failure")

    def test_valid_channel_coupling_sends_exact_command(self):
        self.scope.set_channel_coupling(2, "dc")
        self.assertEqual(self.res.written, [":CHAN2:COUPling DC"])

    def test_invalid_channel_number_rejected(self):
        with self.assertRaises(ValidationError):
            self.scope.set_channel_coupling(9, "DC")

    def test_invalid_probe_factor_rejected(self):
        with self.assertRaises(ValidationError):
            self.scope.set_channel_probe(1, "5X")

    def test_valid_probe_factor(self):
        self.scope.set_channel_probe(1, "10X")
        self.assertEqual(self.res.written, [":CHAN1:PROBe 10X"])

    def test_invalid_acquire_type_rejected(self):
        with self.assertRaises(ValidationError):
            self.scope.set_acquire_type("BOGUS")

    def test_valid_acquire_type_maps_to_short_form(self):
        self.scope.set_acquire_type("average")
        self.assertEqual(self.res.written, [":ACQuire:TYPE AVER"])

    def test_invalid_memory_depth_rejected(self):
        with self.assertRaises(ValidationError):
            self.scope.set_memory_depth("999")

    def test_valid_memory_depth(self):
        self.scope.set_memory_depth("7K")
        self.assertEqual(self.res.written, [":ACQuire:MEMory:DEPTh 7K"])

    def test_average_count_must_be_power_of_two_in_range(self):
        with self.assertRaises(ValidationError):
            self.scope.set_average_count(3)
        with self.assertRaises(ValidationError):
            self.scope.set_average_count(1)
        with self.assertRaises(ValidationError):
            self.scope.set_average_count(16384)
        with self.assertRaises(ValidationError):
            self.scope.set_average_count(2.5)
        with self.assertRaises(ValidationError):
            self.scope.set_average_count(True)
        self.scope.set_average_count(32)
        self.assertEqual(self.res.written, [":ACQuire:AVERages:COUNt 32"])

    def test_invalid_trigger_slope_rejected(self):
        with self.assertRaises(ValidationError):
            self.scope.set_trigger_edge_slope("UP")

    def test_valid_trigger_slope(self):
        self.scope.set_trigger_edge_slope("positive")
        self.assertEqual(self.res.written, [":TRIGger:EDGE:SLOPe POS"])

    def test_invalid_trigger_sweep_rejected(self):
        with self.assertRaises(ValidationError):
            self.scope.set_trigger_sweep("CONTINUOUS")

    def test_invalid_trigger_coupling_rejected(self):
        with self.assertRaises(ValidationError):
            self.scope.set_trigger_coupling("RF")

    def test_invalid_trigger_source_rejected(self):
        with self.assertRaises(ValidationError):
            self.scope.set_trigger_source("CHANNEL9")

    def test_valid_trigger_source(self):
        self.scope.set_trigger_source("channel3")
        self.assertEqual(self.res.written, [":TRIGger:SOURce CHAN3"])

    def test_invalid_measurement_name_rejected(self):
        with self.assertRaises(ValidationError):
            self.scope.measure("NOT_A_MEASUREMENT")

    def test_invalid_waveform_format_rejected(self):
        with self.assertRaises(ValidationError):
            self.scope.capture_waveform(1, points=10, fmt="CSV")

    def test_nonpositive_scales_rejected_before_write(self):
        with self.assertRaises(ValidationError):
            self.scope.set_channel_scale(1, 0)
        with self.assertRaises(ValidationError):
            self.scope.set_timebase_scale(-1e-3)
        with self.assertRaises(ValidationError):
            self.scope.set_trigger_level(float("nan"))
        self.assertEqual(self.res.written, [])

    def test_invalid_waveform_points_rejected_before_query(self):
        for value in (0, -1, 2.5, True):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.scope.capture_waveform(1, points=value, fmt="BYTE")
        self.assertEqual(self.res.written, [])

    def test_disabled_channel_is_rejected_before_transfer_state_changes(self):
        self.res.query_responses[":CHAN1:DISPlay?"] = "0"
        with self.assertRaisesRegex(ValidationError, "disabled"):
            self.scope.capture_waveform(1, points=10, fmt="BYTE")
        self.assertEqual(self.res.written, [":CHAN1:DISPlay?"])


class TestRunStopNoDestructiveCommands(unittest.TestCase):
    def setUp(self):
        self.res = MockResource()
        self.scope = MP720115(self.res)

    def test_run_stop_send_documented_commands_only(self):
        self.scope.run()
        self.scope.stop()
        self.assertEqual(self.res.written, [":RUN", ":STOP"])

    def test_no_forbidden_commands_exist_on_the_class(self):
        forbidden_substrings = ["*RST", "*CLS", "SYST:CAL", "SYSTem:CAL", "SYSTem:CLEAr", ":AUTO", "COMMunicate:LAN"]
        src_path = os.path.join(os.path.dirname(__file__), "..", "src", "mp72_control", "controller.py")
        with open(src_path, "r", encoding="utf-8") as fh:
            source = fh.read()
        # Only check executable-looking lines (skip the safety-rules docstring
        # itself, which intentionally lists these strings as *excluded*).
        in_module_docstring = True
        code_lines = []
        lines = source.splitlines()
        # crude: skip until the first line that is exactly a closing triple-quote
        depth = 0
        collecting_docstring = True
        for line in lines:
            if collecting_docstring:
                depth += line.count('"""')
                if depth >= 2:
                    collecting_docstring = False
                continue
            code_lines.append(line)
        code_text = "\n".join(code_lines)
        for token in forbidden_substrings:
            self.assertNotIn(token, code_text, f"forbidden token {token!r} found in controller code")


class TestCursorCommands(unittest.TestCase):
    def setUp(self):
        self.res = MockResource()
        self.scope = MP720115(self.res)

    def test_cursor_writes_use_documented_commands(self):
        self.scope.set_cursor_mode("independent")
        self.scope.set_cursor_type("time")
        self.scope.set_cursor_source(2)
        self.scope.set_cursor_a(0, "time")
        self.scope.set_cursor_b(699, "TIME")
        self.scope.set_cursor_type("amplitude")
        self.scope.set_cursor_a(28, "amplitude")
        self.scope.set_cursor_b(227, "AMP")
        self.assertEqual(self.res.written, [
            ":CURSor:MODE INDependent",
            ":CURSor:TYPE TIME",
            ":CURSor:SOURce CHANnel2",
            ":CURSor:CURA 0",
            ":CURSor:CURB 699",
            ":CURSor:TYPE AMPlitude",
            ":CURSor:CURA 28",
            ":CURSor:CURB 227",
        ])

    def test_cursor_invalid_values_fail_before_write(self):
        calls = [
            lambda: self.scope.set_cursor_mode("linked"),
            lambda: self.scope.set_cursor_type("voltage"),
            lambda: self.scope.set_cursor_source(0),
            lambda: self.scope.set_cursor_source(True),
            lambda: self.scope.set_cursor_a(-1, "time"),
            lambda: self.scope.set_cursor_b(700, "time"),
            lambda: self.scope.set_cursor_a(27, "amplitude"),
            lambda: self.scope.set_cursor_b(228, "amplitude"),
            lambda: self.scope.set_cursor_a(100.5, "amplitude"),
            lambda: self.scope.set_cursor_a(True, "time"),
            lambda: self.scope.set_cursor_a(100, "close"),
        ]
        for call in calls:
            with self.subTest(call=call), self.assertRaises(ValidationError):
                call()
        self.assertEqual(self.res.written, [])

    def test_cursor_queries_are_normalized(self):
        self.res.query_responses = {
            ":CURSor:MODE?": "ind",
            ":CURSor:TYPE?": "AMP",
            ":CURSor:SOURce?": "CHANnel4",
            ":CURSor:CURA?": "28.0",
            ":CURSor:CURB?": "227",
        }
        self.assertEqual(self.scope.get_cursor_mode(), "INDEPENDENT")
        self.assertEqual(self.scope.get_cursor_type(), "AMPLITUDE")
        self.assertEqual(self.scope.get_cursor_source(), 4)
        self.assertEqual(self.scope.get_cursor_a(), 28)
        self.assertEqual(self.scope.get_cursor_b(), 227)

    def test_cursor_source_parser_accepts_numeric_and_channel_forms(self):
        for raw, expected in (("1", 1), ("2.0", 2), ("CH3", 3), ("CHANnel4", 4)):
            with self.subTest(raw=raw):
                self.assertEqual(self.scope.parse_cursor_source(raw), expected)


class TestWaveformRestoreOnException(unittest.TestCase):
    """Verifies :WAVeform:SOURce/FORMat/POINts are restored even when the
    capture raises partway through (per the task's 'restore in finally'
    requirement)."""

    def test_restore_happens_even_if_binary_block_is_malformed(self):
        res = MockResource()
        res.query_responses = {
            ":CHAN1:DISPlay?": "1",
            ":WAVeform:SOURce?": "CHAN4",
            ":WAVeform:FORMat?": "ASCII",
            ":WAVeform:POINts?": "600",
            ":WAVeform:PREamble?": "0,0,10,1,1e-6,0,0,0.04,0,100",
        }
        res._next_raw = b"NOT_A_VALID_BLOCK"  # triggers IEEEBlockError
        scope = MP720115(res)
        with self.assertRaises(Exception):
            scope.capture_waveform(1, points=10, fmt="BYTE")
        # Final 3 writes must be the restoration of original state, in order.
        self.assertEqual(res.written[-3:], [
            ":WAVeform:POINts 600",
            ":WAVeform:FORMat ASCII",
            ":WAVeform:SOURce CHAN4",
        ])

    def test_successful_capture_restores_and_parses_byte_payload(self):
        res = MockResource()
        res.query_responses = {
            ":CHAN2:DISPlay?": "1",
            ":WAVeform:SOURce?": "CHAN2",
            ":WAVeform:FORMat?": "WORD",
            ":WAVeform:POINts?": "1000",
            ":WAVeform:PREamble?": "0,0,4,1,1e-6,0,0,0.04,0,100",
        }
        payload = bytes([10, 20, 30, 40])
        res._next_raw = build_ieee_block(payload)
        scope = MP720115(res)
        result = scope.capture_waveform(2, points=4, fmt="BYTE")
        self.assertEqual(list(result.raw_codes), [10, 20, 30, 40])
        self.assertTrue(result.volts_provisional)
        self.assertAlmostEqual(result.volts[0], (10 - 100) * 0.04 - 0)
        # Restoration happened back to the original SOURce/FORMat/POINts.
        self.assertEqual(res.written[-3:], [
            ":WAVeform:POINts 1000",
            ":WAVeform:FORMat WORD",
            ":WAVeform:SOURce CHAN2",
        ])

    def test_truncated_first_chunk_is_completed_with_exact_read(self):
        res = MockResource()
        res.query_responses = {
            ":CHAN1:DISPlay?": "1",
            ":WAVeform:SOURce?": "CHAN1",
            ":WAVeform:FORMat?": "ASCII",
            ":WAVeform:POINts?": "500",
            ":WAVeform:PREamble?": "0,0,4,1,1e-6,0,0,0.04,0,128",
        }
        block = build_ieee_block(bytes([125, 126, 127, 128]))
        res._next_raw = block[:5]
        res._remaining_raw = block[5:]
        result = MP720115(res).capture_waveform(1, points=4, fmt="BYTE")
        self.assertEqual(list(result.raw_codes), [125, 126, 127, 128])


if __name__ == "__main__":
    unittest.main()
