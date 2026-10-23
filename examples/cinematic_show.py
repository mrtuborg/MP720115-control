#!/usr/bin/env python3
"""Fast front-panel show for the Multicomp Pro MP720115.

Unlike ``visual_screensaver.py``, this deliberately changes channel visibility,
timebase and an already-selected EDGE trigger so that the CH1..CH4 key lamps
and the display visibly animate.  Frames are sent without per-frame readback or
screen capture for speed.  They are therefore logged as SENT, never PASS.

The instrument identity and every readable field needed for recovery are read
before the first show frame.  Ctrl-C/SIGTERM, normal completion and ordinary
errors restore the baseline and verify the final readable state.  SIGKILL or
power loss requires ``--restore <output-dir>/recovery.json``.
"""

from __future__ import annotations

import argparse
import datetime as dt
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
import cursor_demo as cursor  # noqa: E402
import visual_screensaver as visual  # noqa: E402

DEFAULT_HOST = "192.168.32.132"
SCHEMA = "mp72-cinematic-show-v1"


class ShowError(Exception):
    pass


@dataclass(frozen=True)
class Scene:
    name: str
    settings: Tuple[Tuple[str, Any], ...]

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "settings": dict(self.settings)}


def _scene(name: str, **settings: Any) -> Scene:
    return Scene(name, tuple(settings.items()))


def _bounded_scale(value: float) -> float:
    # Conservative range from the UPO-series programming family.  Relative
    # factors normally stay far away from these bounds.
    return min(100.0, max(2e-9, float(value)))


def fixed_scenes(base_scale: float) -> List[Scene]:
    """The deterministic visual part of one cycle."""
    close = {"cursor.type": "CLOSE"}
    return [
        _scene("blackout", channels=(), **close,
               **{"display.type": "YT", "display.format": "DOTS",
                  "display.wave_brightness": 20}),
        _scene("ch1-square-positive", channels=(1,), **close,
               **{"trigger.source": "CHANNEL1", "trigger.slope": "POSITIVE",
                  "trigger.sweep": "AUTO", "timebase.scale": _bounded_scale(base_scale * 0.25),
                  "display.type": "YT", "display.format": "VECTORS",
                  "display.wave_brightness": 100}),
        _scene("ch1-square-negative", channels=(1,), **close,
               **{"trigger.source": "CHANNEL1", "trigger.slope": "NEGATIVE",
                  "trigger.sweep": "NORMAL", "timebase.scale": _bounded_scale(base_scale),
                  "display.type": "YT"}),
        _scene("ch2-noise-fft", channels=(2,), **close,
               **{"trigger.source": "CHANNEL2", "trigger.slope": "POSITIVE",
                  "trigger.sweep": "AUTO", "timebase.scale": _bounded_scale(base_scale * 4),
                  "display.type": "YT", "math.mode": "FFT", "math.source1": 2,
                  "fft.window": "HANNING", "fft.vtype": "DBRMS"}),
        _scene("ch3-solo", channels=(3,), **close,
               **{"trigger.source": "CHANNEL3", "trigger.slope": "POSITIVE",
                  "trigger.sweep": "AUTO", "display.type": "YT"}),
        _scene("ch4-solo", channels=(4,), **close,
               **{"trigger.source": "CHANNEL4", "trigger.slope": "NEGATIVE",
                  "trigger.sweep": "AUTO", "display.type": "YT"}),
        _scene("odd-channels", channels=(1, 3), **close,
               **{"trigger.source": "CHANNEL1", "trigger.slope": "POSITIVE",
                  "trigger.sweep": "NORMAL", "display.type": "YT"}),
        _scene("even-channels", channels=(2, 4), **close,
               **{"trigger.source": "CHANNEL2", "trigger.slope": "NEGATIVE",
                  "trigger.sweep": "AUTO", "display.type": "YT"}),
        _scene("all-channels", channels=(1, 2, 3, 4), **close,
               **{"trigger.source": "CHANNEL1", "trigger.slope": "POSITIVE",
                  "trigger.sweep": "NORMAL", "timebase.scale": _bounded_scale(base_scale),
                  "display.type": "YT", "display.format": "VECTORS"}),
        _scene("math-add", channels=(1, 2), **close,
               **{"trigger.source": "CHANNEL1", "trigger.sweep": "AUTO",
                  "display.type": "YT", "math.mode": "MATH", "math.source1": 1,
                  "math.source2": 2, "math.operation": "ADD"}),
        _scene("math-subtract", channels=(1, 2), **close,
               **{"math.mode": "MATH", "math.source1": 1, "math.source2": 2,
                  "math.operation": "SUBTRACT"}),
        _scene("xy-lissajous", channels=(1, 2), **close,
               **{"display.type": "XY12", "display.format": "VECTORS"}),
        _scene("fft-square-blackman", channels=(1,), **close,
               **{"display.type": "YT", "math.mode": "FFT", "math.source1": 1,
                  "fft.window": "BMAN", "fft.vtype": "DBRMS"}),
        _scene("filter-low-pass", channels=(1,), **close,
               **{"display.type": "YT", "math.mode": "FILTER", "math.source1": 1,
                  "filter.type": "LP"}),
        _scene("filter-high-pass", channels=(1,), **close,
               **{"math.mode": "FILTER", "math.source1": 1, "filter.type": "HP"}),
        _scene("time-cursors", channels=(1,),
               **{"display.type": "YT", "cursor.type": "TIME",
                  "cursor.mode": "INDEPENDENT", "cursor.source": 1,
                  "cursor.a": 90, "cursor.b": 610}),
        _scene("amplitude-cursors", channels=(2,),
               **{"trigger.source": "CHANNEL2", "trigger.sweep": "AUTO",
                  "display.type": "YT", "cursor.type": "AMPLITUDE",
                  "cursor.mode": "INDEPENDENT", "cursor.source": 2,
                  "cursor.a": 48, "cursor.b": 207}),
    ]


def chase_scenes(cycle: int, seed: int) -> List[Scene]:
    """Reproducible pseudo-random CH1..CH4 lamp chase."""
    rng = random.Random(seed + cycle)
    masks = list(range(1, 16))
    rng.shuffle(masks)
    out: List[Scene] = []
    for index, mask in enumerate(masks[:8], 1):
        channels = tuple(ch for ch in range(1, 5) if mask & (1 << (ch - 1)))
        source = rng.choice(channels)
        out.append(_scene(
            f"lamp-chase-{index}-mask-{mask:04b}", channels=channels,
            **{"cursor.type": "CLOSE", "display.type": "YT",
               "trigger.source": f"CHANNEL{source}",
               "trigger.slope": rng.choice(("POSITIVE", "NEGATIVE")),
               "trigger.sweep": "AUTO"},
        ))
    return out


def plan(base_scale: float, cycle: int, seed: int) -> List[Scene]:
    return chase_scenes(cycle, seed) + fixed_scenes(base_scale)


def normalize_trigger_source(value: str) -> str:
    token = str(value).strip().upper().replace("NEL", "").replace("AN", "")
    if token.startswith("CH") and token[-1:] in "1234":
        return f"CHANNEL{token[-1]}"
    return str(value).strip().upper()


def read_controls(scope) -> Dict[str, Any]:
    mode = scope.get_trigger_mode().strip().upper()
    if not mode.startswith("EDGE"):
        raise ShowError(
            f"current trigger mode is {mode!r}, not EDGE; refusing because other modes cannot be restored"
        )
    return {
        "channels": {str(ch): scope.get_channel_display(ch) for ch in range(1, 5)},
        "timebase": {
            "mode": scope.get_timebase_mode(), "scale": scope.get_timebase_scale(),
            "offset": scope.get_timebase_offset(),
        },
        "trigger": {
            "mode": mode, "source": normalize_trigger_source(scope.get_trigger_source()),
            "slope": scope.get_trigger_edge_slope(), "level": scope.get_trigger_level(),
            "sweep": scope.get_trigger_sweep(), "coupling": scope.get_trigger_coupling(),
        },
    }


def write_scene_field(scope, field: str, value: Any, cursor_type: str) -> None:
    if field.startswith("display.") or field.startswith("math.") or field.startswith("fft.") \
            or field.startswith("filter."):
        visual.write_field(scope, field, value)
    elif field == "timebase.scale":
        scope.set_timebase_scale(value)
    elif field == "trigger.source":
        scope.set_trigger_source(value)
    elif field == "trigger.slope":
        scope.set_trigger_edge_slope(value)
    elif field == "trigger.sweep":
        scope.set_trigger_sweep(value)
    elif field.startswith("cursor."):
        cursor.write_field(scope, field, value, cursor_type)
    else:
        raise ShowError(f"unknown scene field {field!r}")


def apply_scene(scope, scene: Scene) -> None:
    state = dict(scene.settings)
    enabled = tuple(state.pop("channels", ()))
    for ch in range(1, 5):
        scope.set_channel_display(ch, ch in enabled)

    cursor_type = str(state.get("cursor.type", "CLOSE"))
    # Dependency order: display/function controls, trigger/timebase, then
    # cursor type/mode/source/coordinates.  There is intentionally no query.
    order = (
        "display.format", "display.wave_brightness", "display.type",
        "math.mode", "math.source1", "math.source2", "math.operation",
        "fft.window", "fft.vtype", "filter.type", "timebase.scale",
        "trigger.source", "trigger.slope", "trigger.sweep",
        "cursor.type", "cursor.mode", "cursor.source", "cursor.a", "cursor.b",
    )
    for field in order:
        if field in state:
            write_scene_field(scope, field, state[field], cursor_type)


def restore_controls(scope, baseline: Dict[str, Any], log: Callable[[str], None] = print) -> Dict[str, Any]:
    report: Dict[str, Any] = {"fields": {}, "ok": True}

    def restore(name: str, expected: Any, setter: Callable[[Any], None], getter: Callable[[], Any],
                equal: Callable[[Any, Any], bool] = lambda a, b: a == b) -> None:
        actual: Any = None
        error: Optional[str] = None
        for _attempt in (1, 2):
            try:
                actual = getter()
                if equal(expected, actual):
                    break
                setter(expected)
                actual = getter()
                if equal(expected, actual):
                    break
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                visual.refresh_transport(scope)
        ok = equal(expected, actual)
        report["fields"][name] = {
            "baseline": expected, "readback": actual, "ok": ok, "error": error,
        }
        if not ok:
            report["ok"] = False
            log(f"RESTORE FAILED {name}: wanted {expected!r}, got {actual!r}; {error or ''}")

    for ch in range(1, 5):
        expected = bool(baseline["channels"][str(ch)])
        restore(f"channel{ch}.display", expected,
                lambda value, ch=ch: scope.set_channel_display(ch, value),
                lambda ch=ch: scope.get_channel_display(ch))

    tb = baseline["timebase"]
    restore("timebase.mode", tb["mode"], scope.set_timebase_mode, scope.get_timebase_mode)
    restore("timebase.scale", float(tb["scale"]), scope.set_timebase_scale,
            scope.get_timebase_scale, lambda a, b: math.isclose(float(a), float(b), rel_tol=1e-6))
    restore("timebase.offset", float(tb["offset"]), scope.set_timebase_offset,
            scope.get_timebase_offset, lambda a, b: math.isclose(float(a), float(b), rel_tol=1e-6,
                                                                  abs_tol=1e-12))

    tr = baseline["trigger"]
    restore("trigger.mode", "EDGE", lambda _value: scope.set_edge_trigger_mode(),
            scope.get_trigger_mode, lambda _a, b: str(b).strip().upper().startswith("EDGE"))
    restore("trigger.source", tr["source"], scope.set_trigger_source,
            lambda: normalize_trigger_source(scope.get_trigger_source()))
    restore("trigger.slope", tr["slope"], scope.set_trigger_edge_slope, scope.get_trigger_edge_slope)
    restore("trigger.sweep", tr["sweep"], scope.set_trigger_sweep, scope.get_trigger_sweep)
    restore("trigger.coupling", tr["coupling"], scope.set_trigger_coupling, scope.get_trigger_coupling)
    # The show never changes trigger level.  Verify it but do not risk a write
    # on firmware 5.00.0.19, where level restoration can clamp unpredictably.
    try:
        actual_level = scope.get_trigger_level()
        level_ok = math.isclose(float(tr["level"]), float(actual_level), rel_tol=0.01, abs_tol=0.01)
    except Exception as exc:  # noqa: BLE001
        actual_level, level_ok = f"{type(exc).__name__}: {exc}", False
    report["fields"]["trigger.level"] = {
        "baseline": tr["level"], "readback": actual_level, "ok": level_ok,
        "note": "show never writes trigger level",
    }
    if not level_ok:
        report["ok"] = False
    return report


def snapshot_cursor_with_recovery(scope, recovery_path: str, recovery: Dict[str, Any]) -> Dict[str, Any]:
    baseline: Dict[str, Any] = {}
    for field in ("cursor.type", "cursor.a", "cursor.b"):
        visual.refresh_transport(scope)
        baseline[field] = cursor.read_field(scope, field)
    recovery["cursor_baseline"] = baseline
    recovery["phase"] = "partial-before-cursor-activation"
    visual.write_json_atomic(recovery_path, recovery)

    original_type = baseline["cursor.type"]
    if original_type == "CLOSE":
        scope.set_cursor_type("TIME")
    for field in ("cursor.mode", "cursor.source"):
        visual.refresh_transport(scope)
        baseline[field] = cursor.read_field(scope, field)
    if original_type == "CLOSE":
        scope.set_cursor_type("CLOSE")
    recovery["cursor_baseline"] = baseline
    recovery["phase"] = "complete-baseline"
    visual.write_json_atomic(recovery_path, recovery)
    return baseline


@dataclass
class Config:
    host: str
    output_dir: str
    cycles: int
    dwell: float
    seed: int


class CinematicShow:
    def __init__(self, scope, cfg: Config, sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] = print):
        self.scope, self.cfg, self.sleep, self.log = scope, cfg, sleep, log
        self.recovery_path = os.path.join(cfg.output_dir, "recovery.json")
        self.events_path = os.path.join(cfg.output_dir, "frames.jsonl")
        self.recovery: Dict[str, Any] = {}
        self.armed = False
        self.stop_reason = "completed"
        self.exit_code = 0
        self.index = 0

    def event(self, record: Dict[str, Any]) -> None:
        with open(self.events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(record, ts=visual.utc_now()), sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def prepare(self) -> float:
        idn = visual.check_identity(self.scope)
        os.makedirs(self.cfg.output_dir, exist_ok=False)
        controls = read_controls(self.scope)
        visual_baseline, visual_unreadable = visual.snapshot(self.scope)
        self.recovery = {
            "schema": SCHEMA, "created": visual.utc_now(), "host": self.cfg.host,
            "idn": idn, "status": "arming", "controls_baseline": controls,
            "visual_baseline": visual_baseline, "visual_unreadable": visual_unreadable,
            "note": ("Frames are SENT without per-frame readback. After SIGKILL/power loss run "
                     "python examples/cinematic_show.py --restore <this file>."),
        }
        visual.write_json_atomic(self.recovery_path, self.recovery)
        # From this point onward snapshot_cursor_with_recovery may briefly
        # activate TIME cursors.  Mark the runner armed before that first
        # possible write so an exception still enters partial recovery.
        self.armed = True
        cursor_baseline = snapshot_cursor_with_recovery(
            self.scope, self.recovery_path, self.recovery)
        self.recovery.update({"cursor_baseline": cursor_baseline, "status": "armed"})
        visual.write_json_atomic(self.recovery_path, self.recovery)
        self.event({"type": "run_start", "idn": idn, "dwell": self.cfg.dwell,
                    "cycles": self.cfg.cycles, "seed": self.cfg.seed,
                    "recovery": self.recovery_path,
                    "verification": "startup baseline + final restore only; frames are SENT"})
        return float(controls["timebase"]["scale"])

    def loop(self, base_scale: float) -> None:
        cycle = 1
        while self.cfg.cycles == 0 or cycle <= self.cfg.cycles:
            scenes = plan(base_scale, cycle, self.cfg.seed)
            for scene in scenes:
                self.index += 1
                apply_scene(self.scope, scene)
                self.event({"type": "frame", "index": self.index, "cycle": cycle,
                            "name": scene.name, "verdict": "SENT",
                            "settings": dict(scene.settings)})
                self.log(f"[{self.index:04d}] {scene.name}: SENT", flush=True)
                self.sleep(self.cfg.dwell)
            cycle += 1

    def finish(self) -> None:
        if not self.armed:
            return
        cursor_report = cursor.restore_baseline(self.scope, self.recovery["cursor_baseline"], self.log)
        visual_report = visual.restore_baseline(self.scope, self.recovery["visual_baseline"], self.log)
        controls_report = restore_controls(self.scope, self.recovery["controls_baseline"], self.log)
        ok = cursor_report["ok"] and visual_report["ok"] and controls_report["ok"]
        status = "restored" if ok else "restore_failed"
        self.recovery.update({
            "status": status, "stop_reason": self.stop_reason, "updated": visual.utc_now(),
            "restore_report": {"ok": ok, "cursor": cursor_report,
                               "visual": visual_report, "controls": controls_report},
        })
        visual.write_json_atomic(self.recovery_path, self.recovery)
        self.event({"type": "run_end", "status": status, "stop_reason": self.stop_reason,
                    "restore_ok": ok})
        if not ok:
            self.exit_code = 3
        self.armed = False

    def run(self) -> int:
        try:
            scale = self.prepare()
            self.loop(scale)
        except visual.TerminationRequested as exc:
            self.stop_reason = f"signal {exc.signum}"
            self.exit_code = 128 + exc.signum
        except KeyboardInterrupt:
            self.stop_reason = "KeyboardInterrupt"
            self.exit_code = 130
        except Exception as exc:  # noqa: BLE001
            self.stop_reason = f"{type(exc).__name__}: {exc}"
            self.exit_code = 1
            self.log(f"ERROR: {self.stop_reason}")
        saved = visual._block_signals()
        try:
            self.finish()
        finally:
            visual._unblock_signals(saved)
        return self.exit_code


def run_restore(scope, path: str) -> int:
    with open(path, encoding="utf-8") as fh:
        recovery = json.load(fh)
    if recovery.get("schema") != SCHEMA:
        print(f"ERROR: {path} is not a cinematic-show recovery file", file=sys.stderr)
        return 2
    idn = visual.check_identity(scope)
    if idn != recovery.get("idn"):
        print(f"ERROR: instrument ID differs from recovery ID; refusing", file=sys.stderr)
        return 2
    cursor_report = cursor.restore_baseline(scope, recovery["cursor_baseline"])
    visual_report = visual.restore_baseline(scope, recovery["visual_baseline"])
    controls_report = restore_controls(scope, recovery["controls_baseline"])
    ok = cursor_report["ok"] and visual_report["ok"] and controls_report["ok"]
    recovery.update({"status": "restored" if ok else "restore_failed",
                     "updated": visual.utc_now(),
                     "restore_report": {"ok": ok, "cursor": cursor_report,
                                        "visual": visual_report, "controls": controls_report}})
    visual.write_json_atomic(path, recovery)
    return 0 if ok else 3


def nonnegative_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be finite and >= 0")
    return value


def nonnegative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0 (0 = infinite)")
    return value


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fast MP720115 channel-lamp cinematic show")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--transport", choices=("visa", "raw-vxi11"), default="visa")
    p.add_argument("--timeout-ms", type=int, default=5000)
    p.add_argument("--cycles", type=nonnegative_int, default=1, help="0 = infinite")
    p.add_argument("--dwell", type=nonnegative_float, default=1.0,
                   help="seconds per scene; frames have no readback delay")
    p.add_argument("--seed", type=int, default=720115)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--restore", metavar="PATH")
    p.add_argument("--dry-run", action="store_true")
    return p


def connect(args):
    if args.transport == "raw-vxi11":
        return MP720115.connect_raw_vxi11(args.host, timeout_ms=args.timeout_ms)
    return MP720115.connect_visa(args.host, timeout_ms=args.timeout_ms)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    if args.dry_run:
        print(json.dumps({
            "note": "no connection; frames are SENT without per-frame readback",
            "fixed_scenes": [scene.as_dict() for scene in fixed_scenes(1e-3)],
            "sample_cycle": [scene.as_dict() for scene in plan(1e-3, 1, args.seed)],
        }, indent=2))
        return 0
    try:
        scope = connect(args)
    except MP720115Error as exc:
        print(f"ERROR: cannot connect: {exc}", file=sys.stderr)
        return 2
    try:
        if args.restore:
            return run_restore(scope, args.restore)
        output = args.output_dir or os.path.join(
            "output", "cinematic-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S"))
        visual.install_signal_handlers()
        print("FAST SHOW: frames are SENT without per-frame readback; Ctrl-C restores the baseline.")
        return CinematicShow(scope, Config(args.host, output, args.cycles,
                                           args.dwell, args.seed)).run()
    finally:
        try:
            scope.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
