"""Offline regression tests for fail-closed readback verification of the
EDGE trigger level and the dependency-ordered acquisition setup."""

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from mp72_control import cli  # noqa: E402
from mp72_control.controller import MP720115, check_trigger_level, parse_memory_depth  # noqa: E402
from mp72_control.exceptions import ValidationError, VerificationError  # noqa: E402


class StatefulScope:
    """Fake instrument. Quirks mirror firmware 5.00.0.19 observations:
    the average count is only accepted in AVERAGE type, memory depth can be
    refused or overridden, and the trigger level can clamp."""

    def __init__(self):
        self.log = []
        self.state = {
            ":ACQuire:TYPE": "NORMal",
            ":ACQuire:MEMory:DEPTh": "AUTO",
            ":ACQuire:AVERages:COUNt": "8",
            ":TRIGger:MODE": "EDGE",
            ":TRIGger:SOURce": "CHANnel1",
            ":TRIGger:EDGE:SLOPe": "POSitive",
            ":TRIGger:LEVel": "0.8",
            ":TRIGger:SWEep": "AUTO",
            ":TRIGger:COUPling": "DC",
        }
        self.level_clamp = None
        self.ignore_depth = False
        self.depth_override = None
        self.level_resets_on_coupling = False
        self.timeout = 5000

    def write(self, cmd):
        self.log.append(cmd)
        key, _, value = cmd.partition(" ")
        if key == ":ACQuire:AVERages:COUNt" and self.state[":ACQuire:TYPE"] != "AVERage":
            return len(cmd)
        if key == ":ACQuire:MEMory:DEPTh":
            if not self.ignore_depth:
                self.state[key] = self.depth_override or value
            return len(cmd)
        if key == ":TRIGger:LEVel" and self.level_clamp is not None:
            value = repr(self.level_clamp)
        if key == ":TRIGger:COUPling" and self.level_resets_on_coupling:
            self.state[":TRIGger:LEVel"] = "-11.84"
        if key == ":ACQuire:TYPE":
            value = {"NORM": "NORMal", "AVER": "AVERage", "PEAK": "PEAKdetect",
                     "HRES": "HRESolution", "ENV": "ENVelope"}[value]
        self.state[key] = value
        return len(cmd)

    def query(self, cmd):
        self.log.append(cmd)
        return self.state[cmd[:-1]]

    def close(self):
        pass

    def writes(self):
        return [c for c in self.log if not c.endswith("?")]


class TestTriggerLevel(unittest.TestCase):
    def test_check_accepts_quantized_value(self):
        check_trigger_level(0.8, 0.804)
        check_trigger_level(10.0, 10.05)

    def test_check_rejects_with_clear_message(self):
        with self.assertRaisesRegex(VerificationError, "trigger level mismatch: requested 0.8 V.*8.16 V"):
            check_trigger_level(0.8, 8.16)

    def test_check_rejects_nan_and_bad_tolerance(self):
        with self.assertRaises(VerificationError):
            check_trigger_level(0.8, float("nan"))
        with self.assertRaises(ValidationError):
            check_trigger_level(0.8, 0.8, -1)

    def test_verified_setter_clamp_fails_closed(self):
        fake = StatefulScope()
        fake.level_clamp = 8.16
        with self.assertRaises(VerificationError):
            MP720115(fake).set_trigger_level_verified(0.8)

    def test_verified_setter_returns_readback(self):
        fake = StatefulScope()
        self.assertEqual(MP720115(fake).set_trigger_level_verified(0.5), 0.5)

    def test_plain_setter_unchanged(self):
        fake = StatefulScope()
        fake.level_clamp = 8.16
        MP720115(fake).set_trigger_level(0.8)  # backward compatible: no verification

    def _run(self, fake, argv):
        orig = cli.connect
        cli.connect = lambda args: MP720115(fake)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--host", "h"] + argv)
        finally:
            cli.connect = orig
        return code, out.getvalue(), err.getvalue()

    def test_cli_applies_level_last_and_succeeds(self):
        fake = StatefulScope()
        code, out, _ = self._run(fake, ["trigger-edge", "--select", "--source", "CHANNEL2",
                                        "--slope", "NEGATIVE", "--level", "0.3",
                                        "--sweep", "NORMAL", "--coupling", "AC"])
        self.assertEqual(code, 0)
        self.assertEqual(fake.writes()[-1], ":TRIGger:LEVel 0.3")
        self.assertIn("level=0.3V", out)

    def test_cli_level_mismatch_exits_nonzero_with_error(self):
        fake = StatefulScope()
        fake.level_clamp = 8.16
        code, out, err = self._run(fake, ["trigger-edge", "--level", "0.8"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR: trigger level mismatch", err)
        self.assertIn("level=8.16V", out)

    def test_cli_level_not_trusted_when_coupling_resets_it(self):
        fake = StatefulScope()
        fake.level_resets_on_coupling = True
        code, _, _ = self._run(fake, ["trigger-edge", "--coupling", "AC", "--level", "0.8"])
        self.assertEqual(code, 0)  # level written after coupling survives

    def test_cli_without_level_does_not_verify(self):
        fake = StatefulScope()
        fake.state[":TRIGger:LEVel"] = "8.16"
        code, _, _ = self._run(fake, ["trigger-edge", "--sweep", "NORMAL"])
        self.assertEqual(code, 0)

    def test_cli_level_tolerance_option(self):
        fake = StatefulScope()
        fake.level_clamp = 0.85
        self.assertEqual(self._run(fake, ["trigger-edge", "--level", "0.8"])[0], 1)
        self.assertEqual(self._run(fake, ["trigger-edge", "--level", "0.8",
                                          "--level-tolerance", "0.1"])[0], 0)


class TestAcquisitionOrdering(unittest.TestCase):
    def test_average_count_applied_in_average_then_returns_to_normal(self):
        fake = StatefulScope()
        fake.state[":ACQuire:AVERages:COUNt"] = "16"
        report = MP720115(fake).apply_acquisition(acq_type="NORMAL", average_count=8)
        self.assertEqual(fake.writes(), [":ACQuire:TYPE AVER", ":ACQuire:AVERages:COUNt 8",
                                         ":ACQuire:TYPE NORM"])
        self.assertEqual((report.acquire_type, report.average_count), ("NORMal", 8))

    def test_average_count_alone_restores_previous_type(self):
        fake = StatefulScope()
        fake.state[":ACQuire:TYPE"] = "PEAKdetect"
        MP720115(fake).apply_acquisition(average_count=4)
        self.assertEqual(fake.writes(), [":ACQuire:TYPE AVER", ":ACQuire:AVERages:COUNt 4",
                                         ":ACQuire:TYPE PEAK"])

    def test_average_type_with_count_needs_no_extra_switch(self):
        fake = StatefulScope()
        MP720115(fake).apply_acquisition(acq_type="AVERAGE", average_count=16)
        self.assertEqual(fake.writes(), [":ACQuire:TYPE AVER", ":ACQuire:AVERages:COUNt 16"])

    def test_already_average_does_not_resend_type(self):
        fake = StatefulScope()
        fake.state[":ACQuire:TYPE"] = "AVERage"
        MP720115(fake).apply_acquisition(average_count=32)
        self.assertEqual(fake.writes(), [":ACQuire:AVERages:COUNt 32"])

    def test_invalid_count_rejected_before_any_write(self):
        fake = StatefulScope()
        with self.assertRaises(ValidationError):
            MP720115(fake).apply_acquisition(acq_type="NORMAL", average_count=6)
        self.assertEqual(fake.writes(), [])

    def test_memory_depth_applied_after_type(self):
        fake = StatefulScope()
        MP720115(fake).apply_acquisition(acq_type="HRESOLUTION", memory_depth="7K")
        self.assertEqual(fake.writes(), [":ACQuire:TYPE HRES", ":ACQuire:MEMory:DEPTh 7K"])

    def test_memory_depth_mismatch_fails(self):
        fake = StatefulScope()
        fake.ignore_depth = True
        with self.assertRaisesRegex(VerificationError, "memory depth mismatch: requested 7K.*AUTO"):
            MP720115(fake).apply_acquisition(memory_depth="7K")

    def test_memory_depth_equivalent_format_accepted(self):
        fake = StatefulScope()
        fake.depth_override = "7.00K"
        MP720115(fake).apply_acquisition(memory_depth="7K")

    def test_auto_depth_reporting_concrete_value_warns(self):
        fake = StatefulScope()
        fake.depth_override = "28K"
        report = MP720115(fake).apply_acquisition(memory_depth="AUTO")
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("28K", report.warnings[0])

    def test_count_mismatch_still_restores_target_type_then_fails(self):
        fake = StatefulScope()
        fake.write = lambda cmd, orig=fake.write: orig(cmd) if "COUNt" not in cmd else len(cmd)
        with self.assertRaisesRegex(VerificationError, "average count mismatch") as ctx:
            MP720115(fake).apply_acquisition(acq_type="NORMAL", average_count=2)
        self.assertEqual(str(ctx.exception).count("average count mismatch"), 1)
        self.assertEqual(fake.state[":ACQuire:TYPE"], "NORMal")

    def test_parse_memory_depth(self):
        self.assertIsNone(parse_memory_depth("auto"))
        self.assertEqual(parse_memory_depth("7K"), 7000)
        self.assertEqual(parse_memory_depth("70M"), 7e7)
        with self.assertRaises(VerificationError):
            parse_memory_depth("lots")

    def test_cli_acquire_depth_failure_exits_nonzero(self):
        fake = StatefulScope()
        fake.ignore_depth = True
        orig = cli.connect
        cli.connect = lambda args: MP720115(fake)
        err = io.StringIO()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = cli.main(["--host", "h", "acquire", "--memory-depth", "7K"])
        finally:
            cli.connect = orig
        self.assertEqual(code, 1)
        self.assertIn("memory depth mismatch", err.getvalue())

    def test_cli_acquire_auto_warning_exit_zero(self):
        fake = StatefulScope()
        fake.depth_override = "28K"
        orig = cli.connect
        cli.connect = lambda args: MP720115(fake)
        err = io.StringIO()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = cli.main(["--host", "h", "acquire", "--memory-depth", "AUTO"])
        finally:
            cli.connect = orig
        self.assertEqual(code, 0)
        self.assertIn("WARNING:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
