"""Offline tests: exact-length block continuation, restoration, CLI surface,
forbidden commands, the configure example, and the provisional volts formula."""

import ast
import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from mp72_control import cli  # noqa: E402
from mp72_control.controller import MP720115  # noqa: E402
from mp72_control.exceptions import TransportError, ValidationError  # noqa: E402
from mp72_control.scpi_blocks import build_ieee_block, parse_preamble  # noqa: E402

PREAMBLE = "0,0,4,1,1e-6,0,0,0.04,0.5,100"


class FakeScope:
    """Stateful fake resource: tracks settings, logs every command."""

    def __init__(self):
        self.timeout = 5000
        self.log = []
        self.state = {
            ":CHAN1:DISPlay": "1", ":CHAN3:DISPlay": "0",
            ":WAVeform:SOURce": "CHAN2", ":WAVeform:FORMat": "WORD", ":WAVeform:POINts": "700",
            ":CHAN1:COUPling": "DC", ":CHAN1:SCALe": "1.0", ":CHAN1:OFFSet": "0.12",
            ":CHAN1:PROBe": "1x", ":CHAN1:BWLimit": "0",
            ":TIMebase:MODE": "MAIN", ":TIMebase:SCALe": "5e-07", ":TIMebase:OFFSet": "1.2e-06",
            ":TRIGger:MODE": "EDGE", ":TRIGger:SOURce": "CHANnel2", ":TRIGger:EDGE:SLOPe": "ALTernation",
            ":TRIGger:LEVel": "2.0", ":TRIGger:SWEep": "AUTO", ":TRIGger:COUPling": "DC",
        }
        self.block = build_ieee_block(bytes([100, 101, 102, 103]))
        self.first_chunk = None
        self.rest = b""
        self.fail_data = False
        self.extra_queries = {":WAVeform:PREamble?": PREAMBLE}

    def write(self, cmd):
        self.log.append(cmd)
        head, _, value = cmd.partition(" ")
        if head in self.state:
            self.state[head] = {"ON": "1", "OFF": "0"}.get(value, value)
        return len(cmd)

    def query(self, cmd):
        self.log.append(cmd)
        if cmd.endswith("?") and cmd[:-1] in self.state:
            return self.state[cmd[:-1]]
        return self.extra_queries[cmd]

    def read_raw(self):
        if self.fail_data:
            raise OSError("timeout")
        if self.first_chunk is not None:
            return self.first_chunk
        return self.block

    def read_bytes(self, count, chunk_size=None, break_on_termchar=False):
        self.log.append(f"read_bytes({count},{chunk_size},{break_on_termchar})")
        out, self.rest = self.rest[:count], self.rest[count:]
        return out

    def close(self):
        pass


class TestBlockContinuation(unittest.TestCase):
    def test_multi_chunk_block_read_with_exact_remainder(self):
        payload = bytes(i % 256 for i in range(3000))
        block = build_ieee_block(payload)
        fake = FakeScope()
        fake.first_chunk, fake.rest = block[:1024], block[1024:]
        self.assertEqual(MP720115(fake)._query_raw_block(":DISPlay:DATA?"), payload)
        self.assertIn(f"read_bytes({len(block) - 1024},1024,False)", fake.log)

    def test_complete_first_read_needs_no_continuation(self):
        fake = FakeScope()
        MP720115(fake)._query_raw_block(":WAVeform:DATA?")
        self.assertFalse(any(c.startswith("read_bytes") for c in fake.log))

    def test_truncated_without_read_bytes_is_error(self):
        fake = FakeScope()
        fake.first_chunk = fake.block[:5]
        fake.read_bytes = None
        with self.assertRaises(TransportError):
            MP720115(fake)._query_raw_block(":WAVeform:DATA?")

    def test_screen_capture_returns_full_payload(self):
        payload = b"BM" + bytes(5000)
        block = build_ieee_block(payload)
        fake = FakeScope()
        fake.first_chunk, fake.rest = block[:1024], block[1024:]
        self.assertEqual(MP720115(fake).capture_screen_bmp(), payload)


class TestFormulaAndRestore(unittest.TestCase):
    def test_provisional_volts_formula(self):
        pre = parse_preamble("0,0,4,1,1e-6,0,0,0.04,0.5,100")
        self.assertAlmostEqual(pre.code_to_volts(110), (110 - 100) * 0.04 - 0.5)

    def test_capture_uses_formula_and_marks_provisional(self):
        fake = FakeScope()
        res = MP720115(fake).capture_waveform(1, points=4, fmt="BYTE")
        self.assertTrue(res.volts_provisional)
        for code, volts in zip(res.raw_codes, res.volts):
            self.assertAlmostEqual(volts, (code - 100) * 0.04 - 0.5)

    def test_state_restored_after_success(self):
        fake = FakeScope()
        MP720115(fake).capture_waveform(1, points=4, fmt="BYTE")
        self.assertEqual(fake.log[-3:], [
            ":WAVeform:POINts 700", ":WAVeform:FORMat WORD", ":WAVeform:SOURce CHAN2"])
        self.assertEqual(fake.state[":WAVeform:SOURce"], "CHAN2")

    def test_state_restored_after_io_failure(self):
        fake = FakeScope()
        fake.fail_data = True
        with self.assertRaises(TransportError):
            MP720115(fake).capture_waveform(1, points=4, fmt="BYTE")
        self.assertEqual(fake.state[":WAVeform:POINts"], "700")
        self.assertEqual(fake.state[":WAVeform:FORMat"], "WORD")

    def test_disabled_channel_touches_no_transfer_state(self):
        fake = FakeScope()
        with self.assertRaisesRegex(ValidationError, "disabled"):
            MP720115(fake).capture_waveform(3, points=4)
        self.assertEqual(fake.log, [":CHAN3:DISPlay?"])


class TestCliSurface(unittest.TestCase):
    def test_host_is_required(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["status"])

    def test_required_commands_exist(self):
        sub = next(a for a in cli.build_parser()._actions if getattr(a, "choices", None) and "status" in a.choices)
        self.assertEqual(set(sub.choices), {
            "status", "channel-get", "channel-set", "timebase", "acquire",
            "trigger-edge", "run", "stop", "measure", "waveform", "screen"})

    def test_forbidden_or_raw_subcommands_rejected(self):
        for name in ("scpi", "raw", "write", "query", "reset", "rst", "clear", "cls",
                     "calibrate", "cal", "storage", "lan", "autoset", "auto", "service", "telnet"):
            with self.subTest(name=name), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.build_parser().parse_args(["--host", "h", name])

    def test_forbidden_tokens_absent_from_executable_source(self):
        forbidden = ["*RST", "*CLS", ":SYSTem:CAL", ":SYSTem:CLEAr", "COMMunicate:LAN",
                     ":AUTO", "telnet"]
        pkg = os.path.join(ROOT, "src", "mp72_control")
        for fname in sorted(os.listdir(pkg)):
            if not fname.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(pkg, fname), encoding="utf-8").read())
            strings = [n.value for n in ast.walk(tree)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            docs = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                    if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))}
            for s in strings:
                if s in docs:
                    continue
                for token in forbidden:
                    self.assertNotIn(token.lower(), s.lower(), f"{token!r} in {fname}")

    def test_every_written_command_is_allowlisted(self):
        allowed = (":RUN", ":STOP", ":CHAN", ":TIMebase:", ":ACQuire:", ":TRIGger:", ":WAVeform:")
        fake = FakeScope()
        scope = MP720115(fake)
        scope.run(); scope.stop()
        scope.set_channel_scale(1, 0.5); scope.set_timebase_scale(1e-3)
        scope.set_acquire_type("normal"); scope.set_edge_trigger_mode()
        scope.capture_waveform(1, points=4)
        for cmd in fake.log:
            if cmd.startswith("read_bytes") or cmd.endswith("?"):
                continue
            self.assertTrue(cmd.startswith(allowed), cmd)

    def test_status_cli_with_fake_and_screen_write(self):
        fake = FakeScope()
        orig = cli.connect
        cli.connect = lambda args: MP720115(fake)
        try:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["--host", "h", "run"]), 0)
        finally:
            cli.connect = orig
        self.assertEqual(fake.log, [":RUN"])


def _norm(value):
    try:
        return float(value)
    except ValueError:
        return value.upper().replace("CHANNEL", "CHAN")[:3]


def _load_example():
    path = os.path.join(ROOT, "examples", "configure_and_capture.py")
    spec = importlib.util.spec_from_file_location("configure_and_capture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestConfigureExample(unittest.TestCase):
    def setUp(self):
        self.mod = _load_example()
        self.tmp = os.path.join(ROOT, "tests", "_tmp_example.csv")

    def tearDown(self):
        if os.path.exists(self.tmp):
            os.remove(self.tmp)

    def _args(self):
        class A:
            channel = 1; scale = 0.2; timebase = 1e-3; level = 0.5; points = 4
        a = A(); a.csv = self.tmp
        return a

    def test_settings_restored_after_success_without_acquire_mode_change(self):
        fake = FakeScope()
        before = dict(fake.state)
        with redirect_stdout(io.StringIO()):
            self.mod.run(MP720115(fake), self._args())
        self.assertTrue(any(c.startswith(":CHAN1:SCALe 0.2") for c in fake.log))
        for key, value in before.items():
            self.assertEqual(_norm(fake.state[key]), _norm(value), key)
        self.assertFalse(any("ACQuire:MODE" in c for c in fake.log))
        self.assertTrue(os.path.exists(self.tmp))

    def test_non_edge_trigger_is_rejected_before_any_write(self):
        fake = FakeScope()
        fake.state[":TRIGger:MODE"] = "PULSE"
        before = dict(fake.state)
        with self.assertRaisesRegex(RuntimeError, "requires the current trigger mode to be EDGE"):
            self.mod.run(MP720115(fake), self._args())
        self.assertEqual(fake.state, before)
        self.assertFalse(any(not item.endswith("?") for item in fake.log))
        self.assertFalse(os.path.exists(self.tmp))

    def test_settings_restored_in_finally_when_capture_fails(self):
        fake = FakeScope()
        fake.fail_data = True
        before = dict(fake.state)
        with redirect_stderr(io.StringIO()), self.assertRaises(TransportError):
            self.mod.run(MP720115(fake), self._args())
        self.assertEqual(float(fake.state[":CHAN1:SCALe"]), float(before[":CHAN1:SCALe"]))
        self.assertEqual(float(fake.state[":TIMebase:SCALe"]), float(before[":TIMebase:SCALe"]))
        self.assertEqual(float(fake.state[":TRIGger:LEVel"]), float(before[":TRIGger:LEVel"]))
        self.assertEqual(fake.state[":TRIGger:SOURce"], "CHAN2")
        self.assertEqual(fake.state[":TRIGger:EDGE:SLOPe"], "ALT")
        self.assertFalse(any("ACQuire:MODE" in c for c in fake.log))

    def test_disabled_channel_example_still_restores(self):
        fake = FakeScope()
        args = self._args()
        args.channel = 3
        fake.state[":CHAN3:COUPling"] = "DC"
        fake.state.update({":CHAN3:SCALe": "0.1", ":CHAN3:OFFSet": "0", ":CHAN3:PROBe": "10x",
                           ":CHAN3:BWLimit": "0"})
        with redirect_stderr(io.StringIO()), self.assertRaises(ValidationError):
            self.mod.run(MP720115(fake), args)
        self.assertEqual(float(fake.state[":CHAN3:SCALe"]), 0.1)
        self.assertEqual(float(fake.state[":TIMebase:SCALe"]), 5e-07)


if __name__ == "__main__":
    unittest.main()
