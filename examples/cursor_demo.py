#!/usr/bin/env python3
"""Closed-loop cursor choreography for the MP720115 front-panel display.

This demo changes only the documented CURSor subsystem.  It never changes
channel visibility/scales, timebase, trigger, acquisition, RUN/STOP, network,
storage, calibration, or waveform transfer settings.  Screen captures are
verified in memory and are never saved as BMP files.

Examples::

    python examples/cursor_demo.py --dry-run
    python examples/cursor_demo.py --cycles 3 --dwell 3
    python examples/cursor_demo.py --restore output/cursors-.../recovery.json

The programming manual documents cursor position and state readback, but no
SCPI query for the displayed delta-T, 1/delta-T, or delta-V result.  Therefore
this program verifies mode/type/source/positions plus actual pixel changes; it
does not invent a numeric measurement readback that the instrument cannot send.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from mp72_control import MP720115  # noqa: E402
from mp72_control.exceptions import MP720115Error  # noqa: E402
from visual_screensaver import (  # noqa: E402
    EXPECTED_SIZE,
    IDENTITY_PREFIX,
    MIN_DIFF_RATIO,
    Bmp,
    IdentityError,
    ScreenError,
    StateMismatch,
    TerminationRequested,
    _block_signals,
    _unblock_signals,
    check_identity,
    diff_ratio,
    install_signal_handlers,
    parse_bmp,
    refresh_transport,
    utc_now,
    write_json_atomic,
)

DEFAULT_HOST = "192.168.32.132"
SCHEMA_VERSION = 1
FIELD_ORDER = ("cursor.type", "cursor.mode", "cursor.source", "cursor.a", "cursor.b")


class CursorDemoError(Exception):
    """A fail-closed cursor-demo error."""


@dataclass(frozen=True)
class Frame:
    name: str
    group: str
    cursor_type: str
    mode: str
    source: int
    a: Optional[int] = None
    b: Optional[int] = None

    def state(self) -> Dict[str, Any]:
        # Firmware 5.00.0.19 times out on MODE?/SOURce? while TYPE=CLOSE.
        # A blackout frame therefore proves only the readable TYPE=CLOSE;
        # hidden mode/source are set and verified by the next active frame.
        if self.cursor_type == "CLOSE":
            return {"cursor.type": "CLOSE"}
        out = {
            "cursor.type": self.cursor_type,
            "cursor.mode": self.mode,
            "cursor.source": self.source,
        }
        if self.a is not None and self.b is not None:
            out.update({"cursor.a": self.a, "cursor.b": self.b})
        return out

    def describe(self) -> Dict[str, Any]:
        out = {"name": self.name, "group": self.group, **self.state()}
        if self.a is not None and self.b is not None:
            out["screen_coordinate_delta"] = abs(self.b - self.a)
        return out


def plan_frames(ch1_on: bool, ch2_on: bool, ch3_on: bool = False,
                ch4_on: bool = False) -> List[Frame]:
    """Build a deterministic, validated cursor show for enabled channels."""
    frames: List[Frame] = []
    if ch1_on:
        frames.extend([
            Frame("ch1-time-wide-independent", "time", "TIME", "INDEPENDENT", 1, 90, 610),
            Frame("ch1-time-medium-track", "time", "TIME", "TRACK", 1),
            Frame("ch1-time-edge-zoom", "time", "TIME", "INDEPENDENT", 1, 315, 385),
            Frame("ch1-time-crosshair", "time", "TIME", "TRACK", 1),
            Frame("ch1-amplitude-wide", "amplitude", "AMPLITUDE", "INDEPENDENT", 1, 48, 207),
            Frame("ch1-amplitude-center", "amplitude", "AMPLITUDE", "TRACK", 1),
            Frame("ch1-amplitude-tight", "amplitude", "AMPLITUDE", "INDEPENDENT", 1, 108, 147),
            Frame("cursor-blackout-ch1", "transition", "CLOSE", "INDEPENDENT", 1),
        ])
    if ch2_on:
        frames.extend([
            Frame("ch2-noise-amplitude-wide", "noise", "AMPLITUDE", "INDEPENDENT", 2, 38, 217),
            Frame("ch2-noise-amplitude-band", "noise", "AMPLITUDE", "TRACK", 2),
            Frame("ch2-noise-amplitude-tight", "noise", "AMPLITUDE", "INDEPENDENT", 2, 104, 151),
            Frame("cursor-blackout-ch2", "transition", "CLOSE", "TRACK", 2),
        ])
    for channel, enabled in ((3, ch3_on), (4, ch4_on)):
        if not enabled:
            continue
        frames.extend([
            Frame(f"ch{channel}-time-wide", "time", "TIME", "INDEPENDENT",
                  channel, 90, 610),
            Frame(f"ch{channel}-time-tight", "time", "TIME", "TRACK", channel),
            Frame(f"ch{channel}-amplitude-wide", "amplitude", "AMPLITUDE",
                  "INDEPENDENT", channel, 48, 207),
            Frame(f"ch{channel}-amplitude-tight", "amplitude", "AMPLITUDE",
                  "TRACK", channel),
            Frame(f"cursor-blackout-ch{channel}", "transition", "CLOSE",
                  "INDEPENDENT", channel),
        ])
    for frame in frames:
        if frame.cursor_type != "CLOSE" and frame.a is not None and frame.b is not None:
            MP720115.validate_cursor_position(frame.cursor_type, frame.a)
            MP720115.validate_cursor_position(frame.cursor_type, frame.b)
    return frames


def cycle_order(frames: Sequence[Frame], cycle: int, seed: int) -> List[Frame]:
    """Pseudo-random but reproducible order, with blackout never first."""
    active = [frame for frame in frames if frame.cursor_type != "CLOSE"]
    blackout = [frame for frame in frames if frame.cursor_type == "CLOSE"]
    random.Random(seed + cycle).shuffle(active)
    if blackout:
        # One blackout at the midpoint and the rest at the end makes the
        # cursor overlay visibly disappear/reappear without an unsafe write.
        active.insert(max(1, len(active) // 2), blackout[0])
        active.extend(blackout[1:])
    return active


def read_field(scope, field: str) -> Any:
    return {
        "cursor.type": scope.get_cursor_type,
        "cursor.mode": scope.get_cursor_mode,
        "cursor.source": scope.get_cursor_source,
        "cursor.a": scope.get_cursor_a,
        "cursor.b": scope.get_cursor_b,
    }[field]()


def write_field(scope, field: str, value: Any, cursor_type: str) -> None:
    if field == "cursor.type":
        scope.set_cursor_type(value)
    elif field == "cursor.mode":
        scope.set_cursor_mode(value)
    elif field == "cursor.source":
        scope.set_cursor_source(value)
    elif field == "cursor.a":
        scope.set_cursor_a(value, cursor_type)
    elif field == "cursor.b":
        scope.set_cursor_b(value, cursor_type)
    else:
        raise CursorDemoError(f"unknown field {field!r}")


def snapshot(scope) -> Dict[str, Any]:
    baseline: Dict[str, Any] = {}
    for field in FIELD_ORDER:
        refresh_transport(scope)
        baseline[field] = read_field(scope, field)
    return baseline


def channel_context(scope) -> Dict[str, bool]:
    return {f"ch{ch}_display": bool(scope.get_channel_display(ch)) for ch in range(1, 5)}


def capture(scope) -> Tuple[bytes, Bmp]:
    refresh_transport(scope)
    data = scope.capture_screen_bmp()
    return data, parse_bmp(data)


def apply_and_verify(scope, frame: Frame) -> Dict[str, Any]:
    requested = frame.state()
    readback: Dict[str, Any] = {}
    for field in FIELD_ORDER:
        if field not in requested:
            continue
        write_field(scope, field, requested[field], frame.cursor_type)
        actual = read_field(scope, field)
        readback[field] = actual
        if actual != requested[field]:
            raise StateMismatch(f"{field}: requested {requested[field]!r}, readback {actual!r}")
    return readback


def verify_frame(scope, frame: Frame) -> Dict[str, Any]:
    expected = frame.state()
    readback = {field: read_field(scope, field) for field in expected}
    mismatches = {field: (expected[field], readback[field]) for field in expected
                  if expected[field] != readback[field]}
    if mismatches:
        raise StateMismatch(f"later cursor readback mismatch: {mismatches}")
    return readback


def restore_baseline(scope, baseline: Dict[str, Any], log: Callable[[str], None] = print) -> Dict[str, Any]:
    """Restore all cursor fields with one retry per field and final readback."""
    report: Dict[str, Any] = {"fields": {}, "ok": True}
    original_type = baseline["cursor.type"]

    # A crash/failure during temporary activation can leave only the partial
    # pre-write baseline (TYPE/A/B). If it already matches, do not manufacture
    # a risky activation merely to restore state that never changed.
    if original_type == "CLOSE" and not any(
            field in baseline for field in ("cursor.mode", "cursor.source")):
        partial_matches = True
        for field, expected in baseline.items():
            try:
                refresh_transport(scope)
                actual = read_field(scope, field)
            except Exception as exc:  # noqa: BLE001
                actual = f"{type(exc).__name__}: {exc}"
            ok = actual == expected
            report["fields"][field] = {
                "baseline": expected, "readback": actual, "ok": ok,
                "note": "partial pre-activation baseline verification",
            }
            partial_matches = partial_matches and ok
        if partial_matches:
            return report

    work_type = original_type if original_type != "CLOSE" else "TIME"
    sequence = [("cursor.type", work_type)]
    # Coordinates are deterministic only in INDEPENDENT. Restore them there,
    # then put the baseline mode back last; TRACK couples A/B in a
    # firmware- and cursor-type-dependent way.
    if "cursor.mode" in baseline and any(field in baseline for field in ("cursor.a", "cursor.b")):
        sequence.append(("cursor.mode", "INDEPENDENT"))
    sequence.extend((field, baseline[field]) for field in
                    ("cursor.source", "cursor.a", "cursor.b") if field in baseline)
    if "cursor.mode" in baseline:
        sequence.append(("cursor.mode", baseline["cursor.mode"]))
    sequence.append(("cursor.type", original_type))
    for field, value in sequence:
        errors: List[str] = []
        actual: Any = None
        for _attempt in (1, 2):
            try:
                refresh_transport(scope)
                write_field(scope, field, value, work_type)
                actual = read_field(scope, field)
                if actual == value:
                    break
                errors.append(f"requested {value!r}, readback {actual!r}")
            except Exception as exc:  # noqa: BLE001 - restoration must continue
                errors.append(f"{type(exc).__name__}: {exc}")
        ok = actual == value
        report["fields"][field] = {
            "baseline": value, "readback": actual, "ok": ok,
            "errors": errors,
        }
        if not ok:
            report["ok"] = False
            log(f"RESTORE FAILED for {field}: {errors[-1] if errors else 'unknown error'}")

    # MODE?/SOURce? are unavailable when TYPE=CLOSE on firmware 5.00.0.19.
    # They were already written and read back under temporary TIME above;
    # final verification checks every field that remains queryable after CLOSE.
    for field, expected in baseline.items():
        if original_type == "CLOSE" and field in ("cursor.mode", "cursor.source"):
            report["fields"].setdefault(field, {})["final_verification"] = (
                "verified before restoring TYPE=CLOSE; query unavailable while closed"
            )
            continue
        try:
            refresh_transport(scope)
            actual = read_field(scope, field)
        except Exception as exc:  # noqa: BLE001
            actual = f"{type(exc).__name__}: {exc}"
        if actual != expected:
            report["ok"] = False
            report["fields"].setdefault(field, {})["final_mismatch"] = {
                "expected": expected, "actual": actual,
            }
    return report


class Evidence:
    def __init__(self, output_dir: str):
        self.jsonl = os.path.join(output_dir, "frames.jsonl")
        self.ledger = os.path.join(output_dir, "ledger.md")
        self.counts = {"PASS": 0, "WARN": 0, "FAIL": 0}

    def append_json(self, record: Dict[str, Any]) -> None:
        with open(self.jsonl, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(record, ts=utc_now()), sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def start(self, meta: Dict[str, Any]) -> None:
        text = (
            "# MP720115 cursor-demo ledger\n\n"
            f"- Started: {meta['started']}\n- Host: {meta['host']}\n"
            f"- ID: `{meta['idn']}`\n- Recovery: `{meta['recovery']}`\n\n"
            "> Every cursor write is checked by SCPI readback and by an in-memory screen capture. "
            "The firmware exposes cursor coordinates but not the displayed numeric delta result. "
            "No BMP files are saved.\n\n"
            "| # | Cycle | Frame | Verdict | Pixel diff | Coordinate delta | Note |\n"
            "|---|---|---|---|---|---|---|\n"
        )
        with open(self.ledger, "x", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())

    def frame(self, record: Dict[str, Any]) -> None:
        self.counts[record["verdict"]] += 1
        self.append_json(dict(record, type="frame"))
        diff = record.get("diff_ratio")
        delta = record.get("screen_coordinate_delta")
        row = (f"| {record['index']} | {record['cycle']} | {record['name']} | "
               f"{record['verdict']} | {'' if diff is None else f'{diff:.4f}'} | "
               f"{'' if delta is None else delta} | {record.get('note', '')} |\n")
        with open(self.ledger, "a", encoding="utf-8") as fh:
            fh.write(row)
            fh.flush()
            os.fsync(fh.fileno())

    def finish(self, reason: str, recovery: str) -> None:
        with open(self.ledger, "a", encoding="utf-8") as fh:
            fh.write(
                "\n## Result\n\n"
                f"- PASS: {self.counts['PASS']}; WARN: {self.counts['WARN']}; "
                f"FAIL: {self.counts['FAIL']}\n- Stop reason: {reason}\n- Recovery: {recovery}\n"
            )
            fh.flush()
            os.fsync(fh.fileno())


@dataclass
class Config:
    host: str
    output_dir: str
    cycles: int
    dwell: float
    seed: int
    transport: str
    timeout_ms: int
    no_restore: bool = False


class CursorDemo:
    def __init__(self, scope, config: Config, sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] = print):
        self.scope, self.cfg, self.sleep, self.log = scope, config, sleep, log
        self.evidence = Evidence(config.output_dir)
        self.recovery_path = os.path.join(config.output_dir, "recovery.json")
        self.recovery: Dict[str, Any] = {}
        self.baseline: Dict[str, Any] = {}
        self.prev_bmp: Optional[Bmp] = None
        self.index = 0
        self.armed = False
        self.stop_reason = "completed"
        self.exit_code = 0

    def prepare(self) -> List[Frame]:
        idn = check_identity(self.scope)
        os.makedirs(self.cfg.output_dir, exist_ok=False)
        context = channel_context(self.scope)
        # TYPE, A and B remain readable while cursors are closed. MODE and
        # SOURce do not on firmware 5.00.0.19, so create a recoverable partial
        # baseline before temporarily opening TIME to reveal those fields.
        self.baseline = {}
        for field in ("cursor.type", "cursor.a", "cursor.b"):
            refresh_transport(self.scope)
            self.baseline[field] = read_field(self.scope, field)
        data, self.prev_bmp = capture(self.scope)
        self.recovery = {
            "schema_version": SCHEMA_VERSION,
            "created": utc_now(),
            "updated": utc_now(),
            "host": self.cfg.host,
            "idn": idn,
            "status": "armed",
            "baseline": self.baseline,
            "phase": "partial_baseline_before_cursor_activation",
            "channel_context_not_modified": context,
            "baseline_screen": {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)},
            "note": ("Written before the first change. Run python examples/cursor_demo.py "
                     "--restore <this file> after SIGKILL or power loss."),
        }
        write_json_atomic(self.recovery_path, self.recovery)
        self.armed = True

        if self.baseline["cursor.type"] == "CLOSE":
            self.scope.set_cursor_type("TIME")
            if self.scope.get_cursor_type() != "TIME":
                raise StateMismatch("temporary cursor activation did not read back TIME")
        for field in ("cursor.mode", "cursor.source"):
            refresh_transport(self.scope)
            self.baseline[field] = read_field(self.scope, field)
        if self.recovery["baseline"]["cursor.type"] == "CLOSE":
            self.scope.set_cursor_type("CLOSE")
            if self.scope.get_cursor_type() != "CLOSE":
                raise StateMismatch("temporary cursor activation did not restore CLOSE")

        self.recovery.update({
            "baseline": self.baseline,
            "phase": "complete_baseline",
            "updated": utc_now(),
        })
        write_json_atomic(self.recovery_path, self.recovery)
        frames = plan_frames(context["ch1_display"], context["ch2_display"],
                             context["ch3_display"], context["ch4_display"])
        if not frames:
            raise CursorDemoError("no channel is enabled; no safe cursor frames")
        self.evidence.start({"started": self.recovery["created"], "host": self.cfg.host,
                             "idn": idn, "recovery": self.recovery_path})
        self.evidence.append_json({
            "type": "run_start", "frames": [f.describe() for f in frames],
            "cycles": self.cfg.cycles, "dwell": self.cfg.dwell, "seed": self.cfg.seed,
            "channel_context": context,
        })
        return frames

    def run_frame(self, frame: Frame, cycle: int) -> None:
        self.index += 1
        last_error = ""
        for attempt in (1, 2):
            try:
                readback = apply_and_verify(self.scope, frame)
                self.sleep(self.cfg.dwell)
                data, bmp = capture(self.scope)
                readback = verify_frame(self.scope, frame)
            except Exception as exc:  # noqa: BLE001 - exactly one retry, then fail closed
                last_error = f"{type(exc).__name__}: {exc}"
                self.log(f"  attempt {attempt} failed: {last_error}")
                continue
            ratio = diff_ratio(self.prev_bmp, bmp)
            notes: List[str] = []
            verdict = "PASS"
            if ratio is not None and ratio < MIN_DIFF_RATIO:
                verdict = "WARN"
                notes.append("screen barely changed despite correct cursor readback")
            if (bmp.width, bmp.height) != EXPECTED_SIZE:
                verdict = "WARN"
                notes.append(f"unexpected screen size {bmp.width}x{bmp.height}")
            if attempt == 2:
                verdict = "WARN"
                notes.append(f"succeeded on retry after {last_error}")
            record = {
                "index": self.index, "cycle": cycle, "name": frame.name,
                "group": frame.group, "requested": frame.state(), "readback": readback,
                "screen_coordinate_delta": (None if frame.a is None else abs(frame.b - frame.a)),
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                "diff_ratio": ratio, "verdict": verdict, "attempts": attempt,
                "note": "; ".join(notes),
            }
            self.prev_bmp = bmp
            self.evidence.frame(record)
            self.log(f"[{self.index:04d}] {frame.name}: {verdict}"
                     + ("" if ratio is None else f" (pixel diff {ratio:.4f})"))
            return
        self.evidence.frame({
            "index": self.index, "cycle": cycle, "name": frame.name, "group": frame.group,
            "requested": frame.state(), "readback": None, "screen_coordinate_delta": None,
            "sha256": None, "bytes": None, "diff_ratio": None, "verdict": "FAIL",
            "attempts": 2, "note": last_error,
        })
        raise CursorDemoError(f"frame {frame.name} failed after one retry: {last_error}")

    def loop(self, frames: List[Frame]) -> None:
        baseline_context = self.recovery["channel_context_not_modified"]
        cycle = 0
        while self.cfg.cycles == 0 or cycle < self.cfg.cycles:
            cycle += 1
            current_context = channel_context(self.scope)
            if current_context != baseline_context:
                raise CursorDemoError(
                    f"channel visibility changed externally: {baseline_context} -> {current_context}"
                )
            for frame in cycle_order(frames, cycle, self.cfg.seed):
                self.run_frame(frame, cycle)

    def finish(self) -> None:
        if not self.armed:
            return
        if self.cfg.no_restore:
            status = "not_restored"
            self.log(f"WARNING: cursor baseline not restored; use --restore {self.recovery_path}")
        else:
            report = restore_baseline(self.scope, self.baseline, self.log)
            status = "restored" if report["ok"] else "restore_failed"
            self.recovery["restore_report"] = report
            if not report["ok"]:
                self.exit_code = 3
        self.recovery.update({"status": status, "stop_reason": self.stop_reason, "updated": utc_now()})
        write_json_atomic(self.recovery_path, self.recovery)
        self.evidence.append_json({"type": "run_end", "status": status,
                                   "stop_reason": self.stop_reason, "counts": self.evidence.counts})
        self.evidence.finish(self.stop_reason, f"{status}; {self.recovery_path}")
        self.armed = False

    def run(self) -> int:
        try:
            frames = self.prepare()
            self.loop(frames)
        except TerminationRequested as exc:
            self.stop_reason = f"signal {exc.signum}"
            self.exit_code = 128 + exc.signum
        except KeyboardInterrupt:
            self.stop_reason = "KeyboardInterrupt"
            self.exit_code = 130
        except IdentityError as exc:
            self.stop_reason = str(exc)
            self.exit_code = 2
            self.log(f"ERROR: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.stop_reason = f"{type(exc).__name__}: {exc}"
            self.exit_code = 1
            self.log(f"ERROR: {self.stop_reason}")
        saved = _block_signals()
        try:
            self.finish()
        finally:
            _unblock_signals(saved)
        return self.exit_code


def run_restore(scope, path: str, log: Callable[[str], None] = print) -> int:
    with open(path, encoding="utf-8") as fh:
        recovery = json.load(fh)
    if recovery.get("schema_version") != SCHEMA_VERSION or not isinstance(recovery.get("baseline"), dict):
        log(f"ERROR: {path} is not a cursor-demo recovery file")
        return 2
    idn = check_identity(scope)
    if idn != recovery.get("idn"):
        log(f"ERROR: instrument {idn!r} differs from recovery ID {recovery.get('idn')!r}; refusing")
        return 2
    report = restore_baseline(scope, recovery["baseline"], log)
    recovery.update({"status": "restored" if report["ok"] else "restore_failed",
                     "restore_report": report, "updated": utc_now()})
    write_json_atomic(path, recovery)
    return 0 if report["ok"] else 3


def nonnegative_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be a finite number >= 0")
    return value


def nonnegative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0 (0 = infinite)")
    return value


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Verified cursor choreography on the MP720115 display")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--transport", choices=("visa", "raw-vxi11"), default="visa")
    p.add_argument("--timeout-ms", type=int, default=5000)
    p.add_argument("--cycles", type=nonnegative_int, default=1, help="0 = infinite")
    p.add_argument("--dwell", type=nonnegative_float, default=2.0,
                   help="minimum seconds per cursor view")
    p.add_argument("--seed", type=int, default=720115,
                   help="deterministic per-cycle frame shuffle seed")
    p.add_argument("--output-dir", default=None,
                   help="default: output/cursors-<UTC timestamp>")
    p.add_argument("--no-restore", action="store_true",
                   help="DANGEROUS: leave the last cursor view active")
    p.add_argument("--restore", metavar="PATH", help="restore a cursor baseline and exit")
    p.add_argument("--dry-run", action="store_true", help="print plans; never connect")
    return p


def connect(args):
    if args.transport == "raw-vxi11":
        return MP720115.connect_raw_vxi11(args.host, timeout_ms=args.timeout_ms)
    return MP720115.connect_visa(args.host, timeout_ms=args.timeout_ms)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    if args.dry_run:
        print(json.dumps({
            "note": "no connection; screen images are always checked in memory and never persisted",
            "ch1_ch2": [f.describe() for f in plan_frames(True, True)],
            "ch1_only": [f.describe() for f in plan_frames(True, False)],
            "all_channels": [f.describe() for f in plan_frames(True, True, True, True)],
        }, indent=2))
        return 0
    if args.restore and args.no_restore:
        print("ERROR: --restore and --no-restore cannot be combined", file=sys.stderr)
        return 2
    try:
        scope = connect(args)
    except MP720115Error as exc:
        print(f"ERROR: cannot connect: {exc}", file=sys.stderr)
        return 2
    try:
        if args.restore:
            return run_restore(scope, args.restore)
        output = args.output_dir or os.path.join(
            "output", "cursors-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        )
        install_signal_handlers()
        cfg = Config(args.host, output, args.cycles, args.dwell, args.seed,
                     args.transport, args.timeout_ms, args.no_restore)
        return CursorDemo(scope, cfg).run()
    finally:
        try:
            scope.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
