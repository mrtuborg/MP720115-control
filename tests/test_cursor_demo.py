"""Offline safety and recovery tests for examples/cursor_demo.py."""

import hashlib
import importlib.util
import os
import re
import struct
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "examples"))

from mp72_control import MP720115  # noqa: E402
from mp72_control.scpi_blocks import build_ieee_block  # noqa: E402

if "visual_screensaver" not in sys.modules:
    vs_spec = importlib.util.spec_from_file_location(
        "visual_screensaver", os.path.join(ROOT, "examples", "visual_screensaver.py")
    )
    vs = importlib.util.module_from_spec(vs_spec)
    sys.modules["visual_screensaver"] = vs
    vs_spec.loader.exec_module(vs)

spec = importlib.util.spec_from_file_location(
    "cursor_demo", os.path.join(ROOT, "examples", "cursor_demo.py")
)
cd = importlib.util.module_from_spec(spec)
sys.modules["cursor_demo"] = cd
spec.loader.exec_module(cd)

IDN = "UNI-T,MP720115,0000000000000,5.00.0.19"


def make_bmp(seed: int, width: int = 800, height: int = 480) -> bytes:
    stride = ((width * 24 + 31) // 32) * 4
    # A compact deterministic image: changing the state changes every pixel,
    # while retaining the real instrument dimensions for dimension checks.
    pixel = hashlib.sha256(str(seed).encode()).digest()[:3]
    row = pixel * width + b"\0" * (stride - width * 3)
    pixels = row * height
    size = 54 + len(pixels)
    return (b"BM" + struct.pack("<IHHI", size, 0, 0, 54)
            + struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0,
                          len(pixels), 0, 0, 0, 0) + pixels)


class FakeCursorInstrument:
    def __init__(self):
        self.timeout = 5000
        self.log = []
        self.closed = False
        self.pending = False
        self.channels = {1: True, 2: True, 3: False, 4: False}
        self.state = {
            ":CURSor:TYPE": "CLOSe",
            ":CURSor:MODE": "TRACK",
            ":CURSor:SOURce": "1",
            ":CURSor:CURA": "111",
            ":CURSor:CURB": "555",
        }
        self.ignore = set()

    def write(self, command):
        self.log.append(command)
        if command == ":DISPlay:DATA?":
            self.pending = True
            return len(command)
        key, _, value = command.partition(" ")
        if key in self.ignore:
            return len(command)
        if key == ":CURSor:SOURce":
            value = value.replace("CHANnel", "")
        self.state[key] = value
        return len(command)

    def query(self, command):
        self.log.append(command)
        if command == "*IDN?":
            return IDN
        match = re.fullmatch(r":CHAN([1-4]):DISPlay\?", command)
        if match:
            return "1" if self.channels[int(match.group(1))] else "0"
        if (command in (":CURSor:MODE?", ":CURSor:SOURce?")
                and self.state[":CURSor:TYPE"].strip().upper().startswith("CLOS")):
            raise TimeoutError(f"{command} unavailable while cursors are closed")
        if command.endswith("?") and command[:-1] in self.state:
            return self.state[command[:-1]]
        raise AssertionError(f"unexpected query {command!r}")

    def read_raw(self):
        assert self.pending
        self.pending = False
        seed = hash(tuple(sorted(self.state.items()))) & 0xFFFFFF
        return build_ieee_block(make_bmp(seed))

    def read_bytes(self, count, chunk_size=None, break_on_termchar=False):
        return b""

    def close(self):
        self.closed = True

    def writes(self):
        return [item for item in self.log if "?" not in item]


def run_demo(resource=None, cycles=1, no_restore=False):
    resource = resource or FakeCursorInstrument()
    parent = tempfile.mkdtemp(prefix="cursor-demo-test-")
    output = os.path.join(parent, "run")
    config = cd.Config("fake", output, cycles, 0.0, 1234, "visa", 5000, no_restore)
    demo = cd.CursorDemo(MP720115(resource), config, sleep=lambda _seconds: None,
                         log=lambda _message: None)
    return resource, demo, demo.run(), output


class CursorPlanningTests(unittest.TestCase):
    def test_plan_uses_only_enabled_channels_and_valid_ranges(self):
        frames = cd.plan_frames(True, False)
        self.assertTrue(frames)
        self.assertEqual({frame.source for frame in frames}, {1})
        for frame in frames:
            if frame.cursor_type != "CLOSE" and frame.a is not None and frame.b is not None:
                MP720115.validate_cursor_position(frame.cursor_type, frame.a)
                MP720115.validate_cursor_position(frame.cursor_type, frame.b)

    def test_plan_supports_ch3_and_ch4_without_enabling_channels(self):
        frames = cd.plan_frames(False, False, True, True)
        self.assertTrue(frames)
        self.assertEqual({frame.source for frame in frames}, {3, 4})

    def test_seeded_order_is_repeatable_and_blackout_not_first(self):
        frames = cd.plan_frames(True, True)
        first = cd.cycle_order(frames, 1, 99)
        second = cd.cycle_order(frames, 1, 99)
        self.assertEqual(first, second)
        self.assertNotEqual(first[0].cursor_type, "CLOSE")

    def test_blackout_frame_requests_only_readable_type(self):
        blackout = next(frame for frame in cd.plan_frames(True, True)
                        if frame.cursor_type == "CLOSE")
        self.assertEqual(blackout.state(), {"cursor.type": "CLOSE"})

    def test_track_frames_do_not_write_coupled_coordinates(self):
        frames = cd.plan_frames(True, True, True, True)
        for frame in frames:
            if frame.cursor_type != "CLOSE" and frame.mode == "TRACK":
                self.assertIsNone(frame.a, frame.name)
                self.assertIsNone(frame.b, frame.name)


class CursorRunnerTests(unittest.TestCase):
    def test_run_checks_frames_saves_no_bmp_and_restores_exact_baseline(self):
        resource = FakeCursorInstrument()
        baseline = dict(resource.state)
        resource, demo, code, output = run_demo(resource)
        self.assertEqual(code, 0)
        self.assertEqual(resource.state, baseline)
        self.assertEqual(demo.evidence.counts["FAIL"], 0)
        self.assertEqual(sum(demo.evidence.counts.values()), 12)
        self.assertEqual([name for name in os.listdir(output) if name.lower().endswith(".bmp")], [])
        self.assertTrue(os.path.isfile(os.path.join(output, "recovery.json")))
        self.assertTrue(os.path.isfile(os.path.join(output, "frames.jsonl")))
        self.assertTrue(os.path.isfile(os.path.join(output, "ledger.md")))

    def test_silent_setter_failure_retries_then_restores(self):
        resource = FakeCursorInstrument()
        baseline = dict(resource.state)
        resource.ignore.add(":CURSor:CURA")
        _resource, demo, code, _output = run_demo(resource)
        self.assertEqual(code, 1)
        self.assertEqual(resource.state, baseline)
        self.assertEqual(demo.evidence.counts["FAIL"], 1)

    def test_activation_failure_uses_partial_recovery(self):
        resource = FakeCursorInstrument()
        baseline = dict(resource.state)
        resource.ignore.add(":CURSor:TYPE")
        _resource, demo, code, _output = run_demo(resource)
        self.assertEqual(code, 1)
        self.assertEqual(resource.state, baseline)
        self.assertEqual(demo.recovery["status"], "restored")
        self.assertTrue(demo.recovery["restore_report"]["ok"])

    def test_no_restore_is_explicit_and_leaves_final_state(self):
        resource = FakeCursorInstrument()
        baseline = dict(resource.state)
        _resource, demo, code, _output = run_demo(resource, no_restore=True)
        self.assertEqual(code, 0)
        self.assertNotEqual(resource.state, baseline)
        self.assertEqual(demo.recovery["status"], "not_restored")

    def test_writes_are_strictly_allowlisted(self):
        resource, _demo, code, _output = run_demo()
        self.assertEqual(code, 0)
        allowed = re.compile(r"^:CURSor:(TYPE|MODE|SOURce|CURA|CURB) ")
        for command in resource.writes():
            self.assertRegex(command, allowed)

    def test_script_has_no_raw_scpi_or_other_subsystem_setters(self):
        text = open(os.path.join(ROOT, "examples", "cursor_demo.py"), encoding="utf-8").read()
        self.assertNotRegex(text, r"scope\.write|scope\.query|\._write|\._query|\.resource\b")
        for forbidden in ("set_channel", "set_timebase", "set_trigger", "set_acquire",
                          "capture_waveform", "scope.run(", "scope.stop("):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
