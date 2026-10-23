"""Offline tests for the display/FUNCtion controller methods and
examples/visual_screensaver.py using a stateful fake transport (no network)."""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from mp72_control.controller import MP720115  # noqa: E402
from mp72_control.exceptions import ValidationError, VerificationError  # noqa: E402
from mp72_control.scpi_blocks import build_ieee_block  # noqa: E402

_spec = importlib.util.spec_from_file_location("visual_screensaver", os.path.join(ROOT, "examples", "visual_screensaver.py"))
vs = importlib.util.module_from_spec(_spec)
sys.modules["visual_screensaver"] = vs
_spec.loader.exec_module(vs)

IDN = "UNI-T,MP720115,0000000000000,5.00.0.19"


def make_bmp(seed: int, w: int = 16, h: int = 8) -> bytes:
    stride = ((w * 24 + 31) // 32) * 4
    pixels = bytearray()
    for y in range(h):
        row = bytearray()
        for x in range(w):
            v = hashlib.sha256(f"{seed}-{x}-{y}".encode()).digest()
            row += v[:3]
        row += b"\0" * (stride - len(row))
        pixels += row
    size = 54 + len(pixels)
    return (b"BM" + struct.pack("<IHHI", size, 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(pixels), 0, 0, 0, 0)
            + bytes(pixels))


class FakeInstrument:
    """Stateful transport. Stores readbacks in the instrument's own spellings."""

    def __init__(self, ch1=True, ch2=True, idn=IDN):
        self.timeout = 5000
        self.idn = idn
        self.log = []
        self.closed = False
        self.state = {
            ":DISPlay:FORMat": "VECT", ":DISPlay:GRID:BRIGhtness": "50",
            ":DISPlay:WAVE:BRIGhtness": "70", ":DISPlay:GRID:MODE": "FULL",
            ":DISPlay:TYPE": "YT", ":FUNCtion:MATH:MODE": "MATH",
            ":FUNCtion:OPERation": "ADD", ":FUNCtion:SOURce1": "1", ":FUNCtion:SOURce2": "2",
            ":FUNCtion:FFT:WINDow": "RECT", ":FUNCtion:FFT:VTYPe": "VRMS",
            ":FUNCtion:FILTer:TYPE": "LP", ":FUNCtion:FILTer:FREQuency:HIGH": "1.000e003",
            ":FUNCtion:FILTer:FREQuency:LOW": "6.000e001",
            ":ACQuire:MEMory:DEPTh": "AUTO",
        }
        self.channels = {1: ch1, 2: ch2}
        self.freq_response = "1.000e003"
        self.ignore = set()          # setter keys that silently do nothing
        self.fail_on = None          # callable(cmd) -> Exception | None
        self.frozen_screen = False
        self.corrupt_screens = 0
        self.screens = 0
        self._pending = None
        self.timeouts = []

    def _key_value(self, cmd):
        key, _, value = cmd.partition(" ")
        return key, value

    def write(self, cmd):
        self.log.append(cmd)
        if self.fail_on:
            exc = self.fail_on(cmd)
            if exc:
                raise exc
        if cmd == ":DISPlay:DATA?":
            self._pending = "bmp"
            return len(cmd)
        key, value = self._key_value(cmd)
        if key in self.ignore:
            return len(cmd)
        if key.startswith(":FUNCtion:SOURce"):
            value = value.replace("CHANnel", "")
        if key.endswith("BRIGhtness") or key.endswith("HIGH") or key.endswith("LOW"):
            value = value
        self.state[key] = value
        return len(cmd)

    def query(self, cmd):
        self.log.append(cmd)
        if cmd == "*IDN?":
            return self.idn
        if cmd in (":CHAN1:DISPlay?", ":CHAN2:DISPlay?"):
            return "1" if self.channels[int(cmd[5])] else "0"
        if cmd == ":MEASure:FREQ? CHAN1":
            return self.freq_response
        if cmd.endswith("?") and cmd[:-1] in self.state:
            return self.state[cmd[:-1]]
        if cmd == ":FUNCtion:FFT:FREQuency?":
            return "1.000e003"
        raise AssertionError(f"unexpected query {cmd}")

    def read_raw(self):
        assert self._pending == "bmp"
        self.screens += 1
        if self.corrupt_screens:
            self.corrupt_screens -= 1
            return build_ieee_block(make_bmp(1)[:-5])
        seed = 0 if self.frozen_screen else hash(tuple(sorted(self.state.items()))) & 0xFFFFFF
        return build_ieee_block(make_bmp(seed))

    def read_bytes(self, count, chunk_size=None, break_on_termchar=False):
        return b""

    def close(self):
        self.closed = True

    def snapshot_state(self):
        baseline, unreadable = vs.snapshot(MP720115(self))
        assert not unreadable, unreadable
        return baseline

    def writes(self):
        return [c for c in self.log if "?" not in c]


class ControllerCommands(unittest.TestCase):
    def setUp(self):
        self.res = FakeInstrument()
        self.scope = MP720115(self.res)

    def written(self):
        return self.res.writes()

    def test_setter_command_strings(self):
        s = self.scope
        s.set_display_format("vectors"); s.set_display_format("DOTS")
        s.set_grid_brightness(1); s.set_wave_brightness(100)
        s.set_grid_mode("cross"); s.set_display_type("xy12")
        s.set_math_mode("fft"); s.set_math_operation("subtract")
        s.set_function_source(1, 1); s.set_function_source(2, 4)
        s.set_fft_window("bman"); s.set_fft_vtype("dbrms")
        s.set_filter_type("bp"); s.set_filter_high_hz(2000); s.set_filter_low_hz(500.5)
        self.assertEqual(self.written(), [
            ":DISPlay:FORMat VECTors", ":DISPlay:FORMat DOTS",
            ":DISPlay:GRID:BRIGhtness 1", ":DISPlay:WAVE:BRIGhtness 100",
            ":DISPlay:GRID:MODE CROSS", ":DISPlay:TYPE XY12",
            ":FUNCtion:MATH:MODE FFT", ":FUNCtion:OPERation SUBTract",
            ":FUNCtion:SOURce1 CHANnel1", ":FUNCtion:SOURce2 CHANnel4",
            ":FUNCtion:FFT:WINDow BMAN", ":FUNCtion:FFT:VTYPe DBRMS",
            ":FUNCtion:FILTer:TYPE BP", ":FUNCtion:FILTer:FREQuency:HIGH 2000.0",
            ":FUNCtion:FILTer:FREQuency:LOW 500.5"])

    def test_every_documented_enum_value_accepted(self):
        for value in ("VECTors", "DOTS"):
            self.scope.set_display_format(value)
        for value in ("FULL", "GRID", "CROSS", "NONE"):
            self.scope.set_grid_mode(value)
        for value in ("XY12", "XY34", "YT"):
            self.scope.set_display_type(value)
        for value in ("MATH", "FFT", "LOGic", "FILTer", "ADVance"):
            self.scope.set_math_mode(value)
        for value in ("ADD", "SUBTract", "MULTiply", "DIVide", "AND", "OR", "NOT", "XOR"):
            self.scope.set_math_operation(value)
        for value in ("RECTangular", "HANNing", "HAMMing", "BMAN"):
            self.scope.set_fft_window(value)
        for value in ("VRMS", "DBRMS"):
            self.scope.set_fft_vtype(value)
        for value in ("LP", "HP", "BP", "BS"):
            self.scope.set_filter_type(value)
        self.assertEqual(len(self.written()), 2 + 4 + 3 + 5 + 8 + 4 + 2 + 4)

    def test_validation_rejects_before_any_write(self):
        s = self.scope
        bad = [
            lambda: s.set_display_format("LINES"), lambda: s.set_display_format(None),
            lambda: s.set_grid_brightness(0), lambda: s.set_grid_brightness(101),
            lambda: s.set_grid_brightness(50.5), lambda: s.set_grid_brightness(True),
            lambda: s.set_wave_brightness(float("nan")), lambda: s.set_wave_brightness("50"),
            lambda: s.set_wave_brightness(float("inf")),
            lambda: s.set_grid_mode("OFF"), lambda: s.set_display_type("XY13"),
            lambda: s.set_math_mode("EXPR"), lambda: s.set_math_operation("POWER"),
            lambda: s.set_function_source(3, 1), lambda: s.set_function_source(1, 5),
            lambda: s.set_function_source(1, 1.0), lambda: s.set_function_source(True, 1),
            lambda: s.set_fft_window("BLACKMAN"), lambda: s.set_fft_vtype("DB"),
            lambda: s.set_filter_type("NOTCH"),
            lambda: s.set_filter_high_hz(0), lambda: s.set_filter_high_hz(-5),
            lambda: s.set_filter_low_hz(float("nan")), lambda: s.set_filter_low_hz(float("inf")),
            lambda: s.set_filter_low_hz("abc"),
        ]
        for i, call in enumerate(bad):
            with self.subTest(i=i), self.assertRaises(ValidationError):
                call()
        self.assertEqual(self.res.log, [])

    def test_query_normalization_and_command_strings(self):
        st = self.res.state
        st[":DISPlay:FORMat"] = "VECTors"; self.assertEqual(self.scope.get_display_format(), "VECTORS")
        st[":FUNCtion:FFT:WINDow"] = "HAMMing"; self.assertEqual(self.scope.get_fft_window(), "HAMMING")
        st[":FUNCtion:FFT:WINDow"] = "hamm"; self.assertEqual(self.scope.get_fft_window(), "HAMMING")
        st[":FUNCtion:OPERation"] = "SUBT"; self.assertEqual(self.scope.get_math_operation(), "SUBTRACT")
        st[":FUNCtion:MATH:MODE"] = "LOG"; self.assertEqual(self.scope.get_math_mode(), "LOGIC")
        st[":DISPlay:GRID:BRIGhtness"] = "5.000e001"; self.assertEqual(self.scope.get_grid_brightness(), 50)
        self.assertEqual(self.scope.get_wave_brightness(), 70)
        self.assertEqual(self.scope.get_filter_high_hz(), 1000.0)
        self.assertEqual(self.scope.get_filter_low_hz(), 60.0)
        self.assertEqual(self.scope.get_fft_frequency(), 1000.0)
        st[":DISPlay:TYPE"] = "YT\x00\x00"; self.assertEqual(self.scope.get_display_type(), "YT")
        self.assertEqual(self.scope.get_grid_mode(), "FULL")
        self.assertEqual(self.scope.get_fft_vtype(), "VRMS")
        self.assertEqual(self.scope.get_filter_type(), "LP")
        self.assertEqual([c for c in self.res.log if c.endswith("?")][-1], ":FUNCtion:FILTer:TYPE?")
        self.assertIn(":FUNCtion:FFT:FREQuency?", self.res.log)

    def test_source_alias_normalization(self):
        for raw, expected in (("1", 1), ("2.0", 2), ("1.000e000", 1), ("CHANnel3", 3),
                              ("chan4", 4), ("CH2", 2), (' "1" ', 1)):
            self.res.state[":FUNCtion:SOURce1"] = raw
            self.assertEqual(self.scope.get_function_source(1), expected, raw)
        for raw in ("5", "9", "0", "1.5", "nan", "CHANnel7", "", "junk"):
            self.res.state[":FUNCtion:SOURce2"] = raw
            with self.assertRaises(VerificationError, msg=raw):
                self.scope.get_function_source(2)
        with self.assertRaises(ValidationError):
            self.scope.get_function_source(3)

    def test_unrecognized_enum_readback_fails_closed(self):
        self.res.state[":DISPlay:TYPE"] = "3D"
        with self.assertRaises(VerificationError):
            self.scope.get_display_type()
        self.res.state[":FUNCtion:FILTer:FREQuency:LOW"] = "*"
        with self.assertRaises(VerificationError):
            self.scope.get_filter_low_hz()

    def test_forbidden_commands_not_in_controller_or_script(self):
        import ast
        for rel in ("src/mp72_control/controller.py", "src/mp72_control/cli.py", "examples/visual_screensaver.py"):
            text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
            for token in (":DISPlay:CLEar", "DISP:CLE", ":FUNCtion:EXPRession", "FUNC:EXPR"):
                self.assertNotIn(token.lower(), text.lower(), f"{token} in {rel}")
            tree = ast.parse(text)
            docs = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                    if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in docs:
                    for token in ("*RST", "*CLS", ":AUTO", "SYSTem:CAL", "SYSTem:CLEAr", "COMMunicate:LAN"):
                        self.assertNotIn(token.lower(), node.value.lower(), f"{token} in {rel}")
        for name in dir(MP720115):
            self.assertNotIn("clear", name.lower())
            self.assertNotIn("expression", name.lower())
        self.assertFalse(hasattr(MP720115, "write"))

    def test_script_writes_only_via_controller_setters(self):
        text = open(os.path.join(ROOT, "examples", "visual_screensaver.py"), encoding="utf-8").read()
        self.assertNotRegex(text, r"scope\.write|scope\.query|\.resource\b|\._write|\._query")
        for forbidden in ("set_channel", "set_timebase", "set_acquire", "set_trigger", "scope.run(", "scope.stop(",
                          "capture_waveform", "apply_acquisition"):
            self.assertNotIn(forbidden, text)

    def test_all_fake_writes_in_screensaver_run_are_allowlisted(self):
        res, out = run_screensaver()
        allowed = re.compile(r"^:(DISPlay:(FORMat|GRID:BRIGhtness|WAVE:BRIGhtness|GRID:MODE|TYPE)|"
                             r"FUNCtion:(MATH:MODE|OPERation|SOURce[12]|FFT:WINDow|FFT:VTYPe|"
                             r"FILTer:TYPE|FILTer:FREQuency:(HIGH|LOW))) ")
        for cmd in res.writes():
            if cmd == ":DISPlay:DATA?":
                continue
            self.assertRegex(cmd, allowed)


def run_screensaver(res=None, cycles=1, no_restore=False, filter_frequency=None, outdir=None,
                    retain_screens=64):
    res = res or FakeInstrument()
    outdir = outdir or tempfile.mkdtemp(prefix="vs-test-")
    cfg = vs.RunConfig("fake", outdir, cycles, 0.0, no_restore, filter_frequency,
                       retain_screens=retain_screens)
    saver = vs.Screensaver(MP720115(res), cfg, sleep=lambda s: None, log=lambda m: None)
    code = saver.run()
    return res, (saver, code, outdir)


class Planning(unittest.TestCase):
    def names(self, **kw):
        return [f.name for f in vs.plan_frames(**kw)]

    def test_ch2_on_includes_dependent_frames(self):
        names = self.names(ch1_on=True, ch2_on=True)
        for n in ("fft-ch2-noise", "math-ch1-add-ch2", "math-ch1-subtract-ch2",
                  "math-ch1-multiply-ch2", "xy12"):
            self.assertIn(n, names)
        for w in ("rectangular", "hanning", "hamming", "bman"):
            for v in ("vrms", "dbrms"):
                self.assertIn(f"fft-ch1-{w}-{v}", names)

    def test_ch2_off_skips_every_ch2_frame(self):
        frames = vs.plan_frames(True, False)
        self.assertTrue(frames)
        for f in frames:
            self.assertNotIn("ch2", f.needs)
            self.assertNotIn(2, [v for k, v in f.settings if k == "math.source2"])
            self.assertNotIn(("math.source1", 2), f.settings)
            self.assertNotEqual(dict(f.settings).get("display.type"), "XY12")
        self.assertFalse([n for n in self.names(ch1_on=True, ch2_on=False) if "ch2" in n or n == "xy12"])

    def test_display_frame_coverage(self):
        names = self.names(ch1_on=True, ch2_on=False)
        for n in ("yt-vectors-full", "yt-dots-cross", "yt-dots-none", "yt-dots-grid"):
            self.assertIn(n, names)
        self.assertGreaterEqual(len([n for n in names if "wave" in n]), 3)
        self.assertGreaterEqual(len([n for n in names if "-grid" in n and "vectors" in n]), 3)

    def test_filter_frames_only_with_safe_frequency(self):
        self.assertFalse([n for n in self.names(ch1_on=True, ch2_on=True) if n.startswith("filter")])
        frames = [f for f in vs.plan_frames(True, True, 1000.0) if f.group == "filter"]
        self.assertEqual([dict(f.settings)["filter.type"] for f in frames], ["LP", "HP", "BP", "BS"])
        for f in frames:
            d = dict(f.settings)
            self.assertEqual((d["filter.low_hz"], d["filter.high_hz"]), (500.0, 2000.0))
            self.assertEqual(d["math.source1"], 1)
            self.assertLess(0, d["filter.low_hz"])
        self.assertFalse([f for f in vs.plan_frames(False, True, 1000.0) if f.group == "filter"])
        existing = [f for f in vs.plan_frames(True, True, None, include_filter=True)
                    if f.group == "filter"]
        self.assertEqual(len(existing), 4)
        self.assertTrue(all("filter.low_hz" not in dict(f.settings) and
                            "filter.high_hz" not in dict(f.settings) for f in existing))

    def test_filter_frequency_validation(self):
        for bad in (0, -1, float("nan"), float("inf"), 0.5, 1e12, True):
            with self.assertRaises(ValueError):
                vs.validate_filter_center(bad)
        self.assertEqual(vs.validate_filter_center("1e3"), 1000.0)

    def test_settings_are_in_dependency_order(self):
        for f in vs.plan_frames(True, True, 1000.0):
            idx = [vs.FIELD_ORDER.index(k) for k, _ in f.settings]
            self.assertEqual(idx, sorted(idx))

    def test_snapshot_reconnects_before_each_field_when_transport_supports_it(self):
        res = FakeInstrument()
        calls = []
        res.reconnect = lambda: calls.append(True)
        baseline, unreadable = vs.snapshot(MP720115(res))
        self.assertFalse(unreadable)
        self.assertEqual(set(baseline), set(vs.FIELD_ORDER))
        self.assertEqual(len(calls), len(vs.FIELD_ORDER))

    def test_dry_run_never_connects(self):
        calls = []
        orig = vs.connect
        vs.connect = lambda args: calls.append(args)
        try:
            import contextlib, io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(vs.main(["--dry-run"]), 0)
        finally:
            vs.connect = orig
        self.assertEqual(calls, [])
        plan = json.loads(buf.getvalue())
        self.assertGreater(len(plan["ch2_on"]), len(plan["ch2_off"]))

    def test_cli_validation(self):
        for argv in (["--cycles", "-1"], ["--dwell", "-1"], ["--dwell", "nan"], ["--filter-frequency", "0"],
                     ["--retain-screens", "-1"],
                     ["--transport", "serial"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                vs.build_parser().parse_args(argv)
        args = vs.build_parser().parse_args([])
        self.assertFalse(args.no_restore)
        self.assertEqual((args.cycles, args.transport, args.timeout_ms, args.retain_screens),
                         (1, "visa", 5000, 0))


class Runs(unittest.TestCase):
    def setUp(self):
        self._size = vs.EXPECTED_SIZE
        vs.EXPECTED_SIZE = (16, 8)  # fake screens are tiny

    def tearDown(self):
        vs.EXPECTED_SIZE = self._size
        for d in getattr(self, "dirs", []):
            shutil.rmtree(d, ignore_errors=True)

    def run_it(self, res=None, **kw):
        res, info = run_screensaver(res, **kw)
        self.dirs = getattr(self, "dirs", []) + [info[2]]
        return res, info

    def records(self, outdir):
        with open(os.path.join(outdir, "frames.jsonl")) as fh:
            return [json.loads(l) for l in fh]

    def test_normal_run_pass_and_restores_baseline(self):
        res = FakeInstrument()
        before = res.snapshot_state()
        res, (saver, code, out) = self.run_it(res)
        self.assertEqual(code, 0)
        self.assertEqual(res.snapshot_state(), before)
        frames = [r for r in self.records(out) if r["type"] == "frame"]
        self.assertEqual(len(frames), 29)  # 25 + 4 filter frames from the measured 1 kHz
        self.assertEqual(len({f["bmp"] for f in frames}), len(frames))
        for f in frames:
            self.assertEqual(f["sha256"], hashlib.sha256(open(f["bmp"], "rb").read()).hexdigest())
            self.assertIn(f["verdict"], ("PASS", "WARN"))
            self.assertIsNotNone(f["diff_ratio"])
            self.assertEqual(f["requested"].keys(), f["readback"].keys())
        recs = self.records(out)
        self.assertEqual(recs[-2]["type"], "restore")
        self.assertEqual(recs[-2]["status"], "restored")
        self.assertEqual(json.load(open(os.path.join(out, "recovery.json")))["status"], "restored")
        ledger = open(os.path.join(out, "ledger.md")).read()
        self.assertIn("live-verified", ledger)
        self.assertIn("SIGKILL", ledger)
        self.assertIn("| PASS |", ledger)
        self.assertNotIn("Bode", ledger)

    def test_unexpected_screen_size_is_warn(self):
        vs.EXPECTED_SIZE = (800, 480)
        res, (saver, code, out) = self.run_it(FakeInstrument())
        frames = [r for r in self.records(out) if r["type"] == "frame"]
        self.assertTrue(all(f["verdict"] == "WARN" and "16x8" in f["note"] for f in frames))

    def test_channel_state_never_written_and_ch2_off_skips(self):
        res = FakeInstrument(ch2=False)
        res, (saver, code, out) = self.run_it(res)
        self.assertEqual(code, 0)
        names = [r["name"] for r in self.records(out) if r["type"] == "frame"]
        self.assertFalse([n for n in names if "ch2" in n or n == "xy12"])
        self.assertFalse([c for c in res.writes() if c.startswith(":CHAN") or "SOURce2 " in c])
        self.assertFalse([c for c in res.writes() if c.startswith((":TIM", ":ACQ", ":TRIG", ":RUN", ":STOP", ":WAV", ":SYST"))])

    def test_filter_frames_use_existing_cutoffs_by_default_and_explicit_override(self):
        res, (saver, code, out) = self.run_it(FakeInstrument())
        self.assertEqual(code, 0)
        run_start = self.records(out)[0]
        self.assertEqual(run_start["filter_center_source"], "existing instrument cutoffs (not modified)")
        names = [r["name"] for r in self.records(out) if r["type"] == "frame"]
        self.assertIn("filter-ch1-bs", names)
        self.assertFalse([c for c in res.log if c.startswith(":FUNCtion:FILTer:FREQuency:")
                          and not c.endswith("?")])
        res = FakeInstrument(); res.freq_response = "*"
        res, (saver, code, out) = self.run_it(res, filter_frequency=200.0)
        self.assertIn(":FUNCtion:FILTer:FREQuency:HIGH 400.0", res.log)

    def test_hidden_memory_depth_side_effect_is_restored(self):
        res = FakeInstrument()
        original_write = res.write

        def write(cmd):
            result = original_write(cmd)
            if cmd == ":FUNCtion:MATH:MODE FFT":
                res.state[":ACQuire:MEMory:DEPTh"] = "28K"
            return result

        res.write = write
        res, (saver, code, out) = self.run_it(res)
        self.assertEqual(code, 0)
        self.assertEqual(res.state[":ACQuire:MEMory:DEPTh"], "AUTO")
        self.assertIn(":ACQuire:MEMory:DEPTh AUTO", res.log)
        report = json.load(open(os.path.join(out, "recovery.json")))["restore_report"]
        self.assertTrue(report["fields"]["acquire.memory_depth"]["ok"])

    def test_identity_guard_blocks_before_any_write(self):
        for idn in ("RIGOL,DS1054Z,1,1", "uni-t,mp720115,1,1", "XUNI-T,MP720115", "UNI-T,MP72011"):
            res = FakeInstrument(idn=idn)
            res, (saver, code, out) = run_screensaver(res)
            self.dirs = getattr(self, "dirs", []) + [out]
            self.assertEqual(code, 2, idn)
            self.assertEqual(res.writes(), [])
            self.assertFalse(os.path.exists(os.path.join(out, "recovery.json")))

    def test_readback_mismatch_fails_closed_after_one_retry_and_restores(self):
        res = FakeInstrument()
        before = res.snapshot_state()
        res.ignore.add(":FUNCtion:FFT:WINDow")
        res, (saver, code, out) = self.run_it(res)
        self.assertEqual(code, 1)
        self.assertEqual(res.snapshot_state(), before)
        frames = [r for r in self.records(out) if r["type"] == "frame"]
        self.assertEqual(frames[-1]["verdict"], "FAIL")
        self.assertEqual(frames[-1]["attempts"], 2)
        self.assertIn("requested", frames[-1]["note"])
        self.assertEqual(frames[-1]["name"], "fft-ch1-hanning-vrms")
        self.assertEqual(sum(1 for c in res.log if c == ":FUNCtion:FFT:WINDow HANNing"), 2)
        self.assertEqual(self.records(out)[-2]["status"], "restored")

    def test_transient_mismatch_succeeds_on_retry_with_warn(self):
        res = FakeInstrument()
        orig_write = res.write
        state = {"dropped": False}

        def write(cmd):
            if cmd == ":DISPlay:GRID:MODE CROSS" and not state["dropped"]:
                state["dropped"] = True
                res.log.append(cmd)
                return len(cmd)
            return orig_write(cmd)
        res.write = write
        res, (saver, code, out) = self.run_it(res)
        self.assertEqual(code, 0)
        f = next(r for r in self.records(out) if r.get("name") == "yt-dots-cross")
        self.assertEqual((f["verdict"], f["attempts"]), ("WARN", 2))

    def test_corrupt_screen_retries_once_then_stops_and_restores(self):
        res = FakeInstrument()
        before = res.snapshot_state()
        res.corrupt_screens = 100
        res2, (saver, code, out) = self.run_it(res)
        self.assertEqual(code, 1)  # baseline screen is corrupt: fail closed before any write
        self.assertEqual(res.writes(), [])
        self.assertEqual(res2.snapshot_state(), before)

    def test_corrupt_after_baseline_is_fail(self):
        res = FakeInstrument()
        before = res.snapshot_state()
        orig = res.read_raw
        count = {"n": 0}

        def read_raw():
            count["n"] += 1
            if count["n"] >= 2:
                return build_ieee_block(b"")  # empty screen
            return orig()
        res.read_raw = read_raw
        res, (saver, code, out) = self.run_it(res)
        self.assertEqual(code, 1)
        self.assertEqual(res.snapshot_state(), before)
        frames = [r for r in self.records(out) if r["type"] == "frame"]
        self.assertEqual([f["verdict"] for f in frames], ["FAIL"])
        self.assertEqual(frames[0]["attempts"], 2)
        self.assertEqual(count["n"], 3)

    def test_unchanged_screen_with_correct_readback_is_warn(self):
        res = FakeInstrument(); res.frozen_screen = True
        res, (saver, code, out) = self.run_it(res)
        self.assertEqual(code, 0)
        frames = [r for r in self.records(out) if r["type"] == "frame"]
        self.assertTrue(all(f["verdict"] == "WARN" for f in frames))
        self.assertTrue(all(f["diff_ratio"] == 0.0 for f in frames))

    def test_recovery_file_written_atomically_before_first_change(self):
        res = FakeInstrument()
        seen = {}
        orig = res.write

        def write(cmd):
            if not cmd.endswith("?") and "first" not in seen:
                seen["first"] = cmd
                outs = [d for d in os.listdir(self.outdir) if d == "recovery.json"]
                seen["recovery"] = json.load(open(os.path.join(self.outdir, "recovery.json"))) if outs else None
                seen["tmp"] = [d for d in os.listdir(self.outdir) if ".tmp-" in d]
            return orig(cmd)
        res.write = write
        self.outdir = tempfile.mkdtemp(prefix="vs-test-")
        self.dirs = [self.outdir]
        before = res.snapshot_state()
        run_screensaver(res, outdir=self.outdir)
        rec = seen["recovery"]
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], "armed")
        self.assertEqual(rec["idn"], IDN)
        self.assertEqual(rec["baseline"]["display.format"], "VECTORS")
        self.assertEqual(rec["baseline"]["math.source1"], 1)
        self.assertEqual(rec["context_not_modified"], {"ch1_display": True, "ch2_display": True})
        self.assertEqual(seen["tmp"], [])
        self.assertEqual(rec["baseline"], {k: v for k, v in rec["baseline"].items()})
        self.assertTrue(os.path.exists(rec["baseline_screen"]["path"]))
        self.assertEqual([d for d in os.listdir(self.outdir) if ".tmp-" in d], [])
        self.assertEqual(res.snapshot_state(), before)

    def test_atomic_write_leaves_old_file_on_failure(self):
        d = tempfile.mkdtemp(prefix="vs-test-")
        self.dirs = [d]
        path = os.path.join(d, "r.json")
        vs.write_json_atomic(path, {"a": 1})
        with self.assertRaises(TypeError):
            vs.write_json_atomic(path, {"a": object()})
        self.assertEqual(json.load(open(path)), {"a": 1})
        self.assertEqual(os.listdir(d), ["r.json"])

    def inject_and_check_restore(self, make_exc, expected_code):
        res = FakeInstrument()
        before = res.snapshot_state()
        count = {"n": 0}

        def fail_on(cmd):
            if cmd.startswith(":FUNCtion:FFT:VTYPe") and not cmd.endswith("?"):
                count["n"] += 1
                if count["n"] == 3:
                    return make_exc()
            return None
        res.fail_on = fail_on
        res, (saver, code, out) = self.run_it(res)
        self.assertEqual(code, expected_code)
        self.assertEqual(res.snapshot_state(), before)
        self.assertEqual(json.load(open(os.path.join(out, "recovery.json")))["status"], "restored")

    def test_restore_after_injected_exception(self):
        self.inject_and_check_restore(lambda: RuntimeError("boom"), 1)

    def test_restore_after_keyboard_interrupt(self):
        self.inject_and_check_restore(lambda: KeyboardInterrupt(), 130)

    def test_restore_after_sigterm(self):
        self.inject_and_check_restore(lambda: vs.TerminationRequested(15), 143)

    def test_restore_after_transport_error(self):
        self.inject_and_check_restore(lambda: OSError("link down"), 1)

    def test_real_sigterm_and_sigint_restore(self):
        import signal
        for sig, code_expected in ((signal.SIGTERM, 143), (signal.SIGINT, 130)):
            res = FakeInstrument()
            before = res.snapshot_state()
            outdir = tempfile.mkdtemp(prefix="vs-test-")
            self.dirs.append(outdir) if hasattr(self, "dirs") else setattr(self, "dirs", [outdir])
            calls = {"n": 0}

            def sleep(_):
                calls["n"] += 1
                if calls["n"] == 5:
                    os.kill(os.getpid(), sig)
            old = signal.getsignal(signal.SIGTERM)
            try:
                vs.install_signal_handlers()
                saver = vs.Screensaver(MP720115(res), vs.RunConfig("fake", outdir, 1, 0.0, False, None),
                                       sleep=sleep, log=lambda m: None)
                self.assertEqual(saver.run(), code_expected)
            finally:
                signal.signal(signal.SIGTERM, old)
            self.assertEqual(res.snapshot_state(), before)
            self.assertEqual(json.load(open(os.path.join(outdir, "recovery.json")))["status"], "restored")

    def test_no_restore_requires_flag_and_warns(self):
        res = FakeInstrument()
        before = res.snapshot_state()
        logs = []
        outdir = tempfile.mkdtemp(prefix="vs-test-")
        self.dirs = [outdir]
        cfg = vs.RunConfig("fake", outdir, 1, 0.0, True, None)
        vs.Screensaver(MP720115(res), cfg, sleep=lambda s: None, log=logs.append).run()
        self.assertNotEqual(res.snapshot_state(), before)
        self.assertTrue(any("--no-restore" in m and "NOT restored" in m for m in logs))
        self.assertEqual(json.load(open(os.path.join(outdir, "recovery.json")))["status"], "not_restored")

    def test_restore_mode_recovers_from_killed_run(self):
        res = FakeInstrument()
        before = res.snapshot_state()
        outdir = tempfile.mkdtemp(prefix="vs-test-")
        self.dirs = [outdir]
        cfg = vs.RunConfig("fake", outdir, 1, 0.0, True, None)
        vs.Screensaver(MP720115(res), cfg, sleep=lambda s: None, log=lambda m: None).run()
        self.assertNotEqual(res.snapshot_state(), before)
        path = os.path.join(outdir, "recovery.json")
        self.assertEqual(vs.run_restore(MP720115(res), path, log=lambda m: None), 0)
        self.assertEqual(res.snapshot_state(), before)
        self.assertEqual(json.load(open(path))["status"], "restored")
        writes = [c for c in res.writes() if c != ":DISPlay:DATA?"]
        self.assertFalse([c for c in writes[-20:] if not c.startswith((":DISP", ":FUNC"))])

    def test_restore_mode_refuses_wrong_identity_or_id_mismatch(self):
        res = FakeInstrument()
        outdir = tempfile.mkdtemp(prefix="vs-test-")
        self.dirs = [outdir]
        run_screensaver(res, outdir=outdir)
        path = os.path.join(outdir, "recovery.json")
        other = FakeInstrument(idn="UNI-T,MP720115,SERIAL2,5.00.0.19")
        self.assertEqual(vs.run_restore(MP720115(other), path, log=lambda m: None), 2)
        self.assertEqual(other.writes(), [])
        wrong = FakeInstrument(idn="FOO,BAR")
        with self.assertRaises(vs.IdentityError):
            vs.run_restore(MP720115(wrong), path, log=lambda m: None)
        self.assertEqual(wrong.writes(), [])

    def test_restore_reports_failure_when_instrument_refuses(self):
        res = FakeInstrument()
        outdir = tempfile.mkdtemp(prefix="vs-test-")
        self.dirs = [outdir]
        cfg = vs.RunConfig("fake", outdir, 1, 0.0, True, None)
        vs.Screensaver(MP720115(res), cfg, sleep=lambda s: None, log=lambda m: None).run()
        res.ignore.add(":DISPlay:GRID:MODE")
        res.state[":DISPlay:GRID:MODE"] = "NONE"
        self.assertEqual(vs.run_restore(MP720115(res), os.path.join(outdir, "recovery.json"), log=lambda m: None), 3)
        self.assertEqual(json.load(open(os.path.join(outdir, "recovery.json")))["status"], "restore_failed")

    def test_restore_order_is_dependency_aware(self):
        res = FakeInstrument()
        outdir = tempfile.mkdtemp(prefix="vs-test-")
        self.dirs = [outdir]
        cfg = vs.RunConfig("fake", outdir, 1, 0.0, True, None)
        vs.Screensaver(MP720115(res), cfg, sleep=lambda s: None, log=lambda m: None).run()
        res.log.clear()
        vs.restore_baseline(MP720115(res), vs_baseline(outdir), log=lambda m: None)
        writes = res.writes()
        keys = [w.split(" ")[0] for w in writes]
        if ":FUNCtion:FILTer:TYPE" in keys and ":FUNCtion:FILTer:FREQuency:LOW" in keys:
            self.assertLess(keys.index(":FUNCtion:FILTer:TYPE"), keys.index(":FUNCtion:FILTer:FREQuency:LOW"))
        if ":FUNCtion:MATH:MODE" in keys and ":DISPlay:TYPE" in keys:
            self.assertLess(keys.index(":FUNCtion:MATH:MODE"), keys.index(":DISPlay:TYPE"))

    def test_unreadable_baseline_field_is_never_changed(self):
        res = FakeInstrument()
        res.state[":FUNCtion:SOURce1"] = "7"
        res, (saver, code, out) = self.run_it(res)
        self.assertEqual(code, 0)
        self.assertFalse([c for c in res.writes() if c.startswith(":FUNCtion:SOURce1")])
        run_start = self.records(out)[0]
        self.assertIn("math.source1", run_start["unreadable"])
        self.assertTrue(run_start["skipped_frames"])
        self.assertEqual(res.state[":FUNCtion:SOURce1"], "7")

    def test_numeric_source_readback_accepted_in_verification(self):
        res, (saver, code, out) = self.run_it(FakeInstrument())
        self.assertEqual(code, 0)
        math_frames = [r for r in self.records(out) if r.get("group") == "math"]
        self.assertEqual(len(math_frames), 3)
        self.assertEqual(math_frames[0]["readback"]["math.source2"], 2)

    def test_cycles_and_infinite_stop(self):
        res, (saver, code, out) = self.run_it(FakeInstrument(), cycles=2)
        frames = [r for r in self.records(out) if r["type"] == "frame"]
        self.assertEqual({f["cycle"] for f in frames}, {1, 2})
        self.assertEqual(len({f["bmp"] for f in frames}), len(frames))
        res = FakeInstrument()
        sleeps = {"n": 0}
        outdir = tempfile.mkdtemp(prefix="vs-test-")
        self.dirs.append(outdir)

        def sleep(_):
            sleeps["n"] += 1
            if sleeps["n"] == 30:
                raise KeyboardInterrupt
        before = res.snapshot_state()
        saver = vs.Screensaver(MP720115(res), vs.RunConfig("fake", outdir, 0, 0.0, False, None),
                               sleep=sleep, log=lambda m: None)
        self.assertEqual(saver.run(), 130)
        self.assertEqual(res.snapshot_state(), before)

    def test_bounded_screenshot_retention_preserves_baseline_and_logs_eviction(self):
        res = FakeInstrument()
        outdir = tempfile.mkdtemp(prefix="vs-test-")
        self.dirs = getattr(self, "dirs", []) + [outdir]
        outside = tempfile.NamedTemporaryFile(prefix="vs-outside-", delete=False)
        outside.write(b"outside")
        outside.close()
        try:
            cfg = vs.RunConfig("fake", outdir, 2, 0.0, False, None, retain_screens=3)
            saver = vs.Screensaver(MP720115(res), cfg, sleep=lambda s: None, log=lambda m: None)
            self.assertEqual(saver.run(), 0)
            recovery = json.load(open(os.path.join(outdir, "recovery.json")))
            baseline = recovery["baseline_screen"]["path"]
            self.assertTrue(os.path.exists(baseline))
            captures = [p for p in os.listdir(outdir) if p.endswith((".bmp", ".bad"))]
            self.assertEqual(len(captures), 4)  # permanent baseline + newest three frames
            evictions = [r for r in self.records(outdir) if r["type"] == "screenshot_evicted"]
            self.assertGreater(len(evictions), 0)
            self.assertTrue(all(os.path.dirname(r["path"]) == os.path.abspath(outdir)
                                for r in evictions))
            self.assertEqual(open(outside.name, "rb").read(), b"outside")
        finally:
            os.unlink(outside.name)

    def test_zero_screenshot_retention_verifies_in_memory_without_files(self):
        res = FakeInstrument()
        outdir = tempfile.mkdtemp(prefix="vs-test-")
        self.dirs = getattr(self, "dirs", []) + [outdir]
        cfg = vs.RunConfig("fake", outdir, 1, 0.0, False, None, retain_screens=0)
        saver = vs.Screensaver(MP720115(res), cfg, sleep=lambda s: None, log=lambda m: None)
        self.assertEqual(saver.run(), 0)
        self.assertFalse([p for p in os.listdir(outdir) if p.endswith((".bmp", ".bad"))])
        recovery = json.load(open(os.path.join(outdir, "recovery.json")))
        self.assertIsNone(recovery["baseline_screen"]["path"])
        frames = [r for r in self.records(outdir) if r["type"] == "frame"]
        self.assertTrue(frames)
        self.assertTrue(all(r["bmp"] is None for r in frames))
        self.assertTrue(all(r["sha256"] and r["diff_ratio"] is not None for r in frames))


def vs_baseline(outdir):
    return json.load(open(os.path.join(outdir, "recovery.json")))["baseline"]


class BmpTools(unittest.TestCase):
    def test_parse_and_diff(self):
        a, b = vs.parse_bmp(make_bmp(1)), vs.parse_bmp(make_bmp(2))
        self.assertEqual((a.width, a.height, a.bpp), (16, 8, 24))
        self.assertEqual(vs.diff_ratio(None, a), None)
        self.assertEqual(vs.diff_ratio(a, a), 0.0)
        self.assertGreater(vs.diff_ratio(a, b), 0.9)

    def test_corrupt_inputs_rejected(self):
        good = make_bmp(1)
        for bad in (b"", b"BM", good[:-1], b"XX" + good[2:], good + b"\0"):
            with self.assertRaises(vs.ScreenError):
                vs.parse_bmp(bad)


if __name__ == "__main__":
    unittest.main()
