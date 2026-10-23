#!/usr/bin/env python3
"""Visual screensaver for the Multicomp Pro MP720115 (documented controls only).

    python examples/visual_screensaver.py --dry-run
    python examples/visual_screensaver.py --host 192.168.32.132 --cycles 1 --dwell 2
    python examples/visual_screensaver.py --restore output/screensaver-.../recovery.json

Cycles through display style, FFT, MATH and (optionally) FILTER frames, reads a
fresh screen image after every frame and records PASS/WARN/FAIL evidence. Screen
bytes are verified in memory; files are optional (``--retain-screens N``).

STATUS: the active frame set was live-verified on MP720115 firmware 5.00.0.19;
every run still re-verifies each change and screen (docs/VALIDATION.md).

Safety model
- The ID must begin with "UNI-T,MP720115" before the first write.
- Frames only change display style and FUNCtion (math/FFT/filter) fields.
  Channel settings and visibility, timebase, trigger, RUN/STOP, waveform
  transfer and network state are never written. The acquisition memory-depth
  baseline is guarded because this firmware can change it as a side effect of
  FUNCtion modes; recovery writes it only when needed. No clear/erase command
  and no raw SCPI exist here.
- A baseline/recovery JSON is written atomically BEFORE the first change.
- Normal end, SIGINT, SIGTERM, KeyboardInterrupt and ordinary exceptions all
  restore the baseline (dependency-aware order) and verify it by readback.
- SIGKILL, power loss or a dead host process CANNOT be handled: the instrument
  stays in the last frame. Run ``--restore PATH`` with the recovery JSON.
- Every change is verified by readback; one retry, then stop and restore.
  A command returning without error is never treated as proof.
- Screen hashes only show that pixels changed, not what they physically mean.
  An unchanged screen with correct readback is WARN.
- ``--no-restore`` leaves the instrument as is (loud warning); default restores.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import shutil
import signal
import struct
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from mp72_control import MP720115  # noqa: E402
from mp72_control.exceptions import MP720115Error  # noqa: E402

DEFAULT_HOST = "192.168.32.132"
IDENTITY_PREFIX = "UNI-T,MP720115"
SCHEMA_VERSION = 1
EXPECTED_SIZE = (800, 480)
MIN_DIFF_RATIO = 0.001  # below this the screen is treated as unchanged
MIN_FREE_BYTES = 200 * 1024 * 1024
FREQ_MIN_HZ = 1.0
FREQ_MAX_HZ = 1.0e8
FRAME_CUTOFF_REL_TOL = 1e-2
RESTORE_CUTOFF_REL_TOL = 1e-3

# Application (dependency) order; restore uses its own explicit sequence.
FIELD_ORDER: Tuple[str, ...] = (
    "display.format", "display.grid_mode", "display.grid_brightness",
    "display.wave_brightness", "display.type",
    "math.mode", "math.source1", "math.source2", "math.operation",
    "fft.window", "fft.vtype",
    "filter.type", "filter.low_hz", "filter.high_hz",
    "acquire.memory_depth",
)
FLOAT_FIELDS = {"filter.low_hz", "filter.high_hz"}

# field -> (getter method, setter method, extra setter args)
FIELD_METHODS: Dict[str, Tuple[str, str, Tuple[Any, ...]]] = {
    "display.format": ("get_display_format", "set_display_format", ()),
    "display.grid_mode": ("get_grid_mode", "set_grid_mode", ()),
    "display.grid_brightness": ("get_grid_brightness", "set_grid_brightness", ()),
    "display.wave_brightness": ("get_wave_brightness", "set_wave_brightness", ()),
    "display.type": ("get_display_type", "set_display_type", ()),
    "math.mode": ("get_math_mode", "set_math_mode", ()),
    "math.source1": ("get_function_source", "set_function_source", (1,)),
    "math.source2": ("get_function_source", "set_function_source", (2,)),
    "math.operation": ("get_math_operation", "set_math_operation", ()),
    "fft.window": ("get_fft_window", "set_fft_window", ()),
    "fft.vtype": ("get_fft_vtype", "set_fft_vtype", ()),
    "filter.type": ("get_filter_type", "set_filter_type", ()),
    "filter.low_hz": ("get_filter_low_hz", "set_filter_low_hz", ()),
    "filter.high_hz": ("get_filter_high_hz", "set_filter_high_hz", ()),
    "acquire.memory_depth": ("get_memory_depth", "set_memory_depth", ()),
}

# Restore sequence: (temporary math mode or None, fields). Controlling fields
# (type) come before the fields that depend on them.
RESTORE_STEPS: Tuple[Tuple[Optional[str], Tuple[str, ...]], ...] = (
    ("FILTER", ("filter.type", "filter.low_hz", "filter.high_hz")),
    ("FFT", ("fft.window", "fft.vtype")),
    ("MATH", ("math.source1", "math.source2", "math.operation")),
    (None, ("math.mode",)),
    (None, ("acquire.memory_depth",)),
    (None, ("display.type", "display.wave_brightness", "display.grid_brightness",
            "display.grid_mode", "display.format")),
)


class ScreensaverError(Exception):
    """Base class for fail-closed conditions."""


class IdentityError(ScreensaverError):
    pass


class StateMismatch(ScreensaverError):
    pass


class ScreenError(ScreensaverError):
    pass


class TerminationRequested(BaseException):
    """Raised from the SIGTERM handler; BaseException so nothing swallows it."""

    def __init__(self, signum: int):
        super().__init__(f"signal {signum}")
        self.signum = signum


# --------------------------------------------------------------------------
# Frame planning
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    name: str
    group: str
    settings: Tuple[Tuple[str, Any], ...]
    needs: Tuple[str, ...] = ()

    def fields(self) -> Tuple[str, ...]:
        return tuple(f for f, _ in self.settings)


def _frame(name: str, group: str, settings: Dict[str, Any], needs: Sequence[str] = ()) -> Frame:
    ordered = tuple((f, settings[f]) for f in FIELD_ORDER if f in settings)
    assert len(ordered) == len(settings), f"unknown field in {name}"
    return Frame(name, group, ordered, tuple(needs))


def derive_cutoffs(center_hz: float) -> Tuple[float, float]:
    """Conservative (low, high) cutoffs around a verified centre frequency."""
    return center_hz * 0.5, center_hz * 2.0


def validate_filter_center(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("filter frequency must be a number")
    freq = float(value)
    if not math.isfinite(freq) or not FREQ_MIN_HZ <= freq <= FREQ_MAX_HZ:
        raise ValueError(
            f"filter frequency {value!r} outside the safe range {FREQ_MIN_HZ}..{FREQ_MAX_HZ:g} Hz")
    return freq


def plan_frames(ch1_on: bool, ch2_on: bool, filter_center_hz: Optional[float] = None,
                include_filter: bool = False) -> List[Frame]:
    """One cycle of frames. CH1/CH2-dependent frames are omitted when the
    channel is off (the screensaver never turns channels on)."""
    frames: List[Frame] = []
    yt = {"display.type": "YT"}
    frames.append(_frame("yt-vectors-full", "display", dict(yt, **{
        "display.format": "VECTORS", "display.grid_mode": "FULL",
        "display.grid_brightness": 60, "display.wave_brightness": 100})))
    for mode in ("CROSS", "NONE", "GRID"):
        frames.append(_frame(f"yt-dots-{mode.lower()}", "display", dict(yt, **{
            "display.format": "DOTS", "display.grid_mode": mode,
            "display.grid_brightness": 60, "display.wave_brightness": 100})))
    for level in (20, 45, 70, 100):
        frames.append(_frame(f"yt-dots-grid-wave{level}", "display", dict(yt, **{
            "display.format": "DOTS",
            "display.wave_brightness": level})))
    for level in (10, 40, 70, 100):
        frames.append(_frame(f"yt-vectors-full-grid{level}", "display", dict(yt, **{
            "display.format": "VECTORS",
            "display.wave_brightness": 100, "display.grid_brightness": level})))

    if ch1_on:
        for window in ("RECTANGULAR", "HANNING", "HAMMING", "BMAN"):
            for vtype in ("VRMS", "DBRMS"):
                frames.append(_frame(f"fft-ch1-{window.lower()}-{vtype.lower()}", "fft", dict(yt, **{
                    "math.mode": "FFT", "math.source1": 1,
                    "fft.window": window, "fft.vtype": vtype}), ("ch1",)))
    if ch2_on:
        frames.append(_frame("fft-ch2-noise", "fft", dict(yt, **{
            "math.mode": "FFT", "math.source1": 2,
            "fft.window": "HANNING", "fft.vtype": "DBRMS"}), ("ch2",)))
    if ch1_on and ch2_on:
        for op in ("ADD", "SUBTRACT", "MULTIPLY"):
            frames.append(_frame(f"math-ch1-{op.lower()}-ch2", "math", dict(yt, **{
                "math.mode": "MATH", "math.source1": 1, "math.source2": 2,
                "math.operation": op}), ("ch1", "ch2")))
        frames.append(_frame("xy12", "xy", {"display.type": "XY12",
                                            "display.format": "VECTORS"}, ("ch1", "ch2")))
    if ch1_on and (include_filter or filter_center_hz is not None):
        cutoffs: Dict[str, Any] = {}
        if filter_center_hz is not None:
            low, high = derive_cutoffs(filter_center_hz)
            cutoffs = {"filter.low_hz": low, "filter.high_hz": high}
        for ftype in ("LP", "HP", "BP", "BS"):
            settings = dict(yt, **{
                "math.mode": "FILTER", "math.source1": 1, "filter.type": ftype})
            settings.update(cutoffs)
            frames.append(_frame(f"filter-ch1-{ftype.lower()}", "filter", settings, ("ch1",)))
    return frames


def runtime_plan(frames: Sequence[Frame], unreadable: Sequence[str]) -> Tuple[List[Frame], List[Dict[str, Any]]]:
    """Drop frames that touch a field whose baseline could not be read
    (it could not be restored, so it is never changed)."""
    blocked = set(unreadable)
    keep: List[Frame] = []
    skipped: List[Dict[str, Any]] = []
    for frame in frames:
        bad = sorted(blocked.intersection(frame.fields()))
        if bad:
            skipped.append({"frame": frame.name, "reason": "baseline unreadable: " + ", ".join(bad)})
        else:
            keep.append(frame)
    return keep, skipped


def frame_to_dict(frame: Frame) -> Dict[str, Any]:
    return {"name": frame.name, "group": frame.group, "needs": list(frame.needs),
            "settings": {f: v for f, v in frame.settings}}


# --------------------------------------------------------------------------
# Files: atomic JSON, ledger
# --------------------------------------------------------------------------

def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def write_json_atomic(path: str, obj: Any) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    tmp = os.path.join(directory, f".{os.path.basename(path)}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


class Evidence:
    """Append-only JSONL plus a concise Markdown ledger."""

    def __init__(self, output_dir: str):
        self.dir = output_dir
        self.jsonl_path = os.path.join(output_dir, "frames.jsonl")
        self.ledger_path = os.path.join(output_dir, "ledger.md")
        self.counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
        self._ledger_started = False

    def _append(self, path: str, text: str) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())

    def event(self, record: Dict[str, Any]) -> None:
        record = dict(record, ts=utc_now())
        self._append(self.jsonl_path, json.dumps(record, sort_keys=True) + "\n")

    def ledger_header(self, meta: Dict[str, Any]) -> None:
        self._ledger_started = True
        text = (
            "# MP720115 visual screensaver ledger\n\n"
            f"- Started: {meta['started']}\n- Host: {meta['host']}\n- ID: `{meta['idn']}`\n"
            f"- Recovery file: `{meta['recovery']}`\n\n"
            "> Controls are documented and were live-verified on firmware 5.00.0.19; this run "
            "still re-verifies every setting. "
            "Screen hashes/difference ratios show only that pixels changed, not what they mean. "
            "Unchanged screen with correct readback is WARN. SIGKILL/power loss requires "
            "`--restore PATH`.\n\n"
            "| # | Cycle | Frame | Verdict | Diff | SHA-256 | BMP | Note |\n"
            "|---|---|---|---|---|---|---|---|\n")
        self._append(self.ledger_path, text)

    def frame(self, rec: Dict[str, Any]) -> None:
        self.counts[rec["verdict"]] += 1
        self.event(dict(rec, type="frame"))
        ratio = rec.get("diff_ratio")
        sha = rec.get("sha256") or ""
        row = "| {i} | {c} | {n} | {v} | {d} | {s} | {p} | {note} |\n".format(
            i=rec["index"], c=rec["cycle"], n=rec["name"], v=rec["verdict"],
            d="" if ratio is None else f"{ratio:.4f}", s=sha[:12],
            p=os.path.basename(rec.get("bmp") or ""),
            note=(rec.get("note") or "").replace("|", "/")[:120])
        self._append(self.ledger_path, row)

    def ledger_footer(self, summary: Dict[str, Any]) -> None:
        text = (
            "\n## Result\n\n"
            f"- Frames: PASS {self.counts['PASS']}, WARN {self.counts['WARN']}, FAIL {self.counts['FAIL']}\n"
            f"- Stop reason: {summary['stop_reason']}\n"
            f"- Recovery: {summary['recovery']}\n")
        self._append(self.ledger_path, text)


# --------------------------------------------------------------------------
# BMP validation and comparison
# --------------------------------------------------------------------------

@dataclass
class Bmp:
    width: int
    height: int
    bpp: int
    offset: int
    stride: int
    data: bytes

    def pixel_rows(self) -> List[bytes]:
        row_bytes = self.width * self.bpp // 8
        rows = []
        for r in range(abs(self.height)):
            start = self.offset + r * self.stride
            rows.append(self.data[start:start + row_bytes])
        return rows


def parse_bmp(data: bytes) -> Bmp:
    if not data:
        raise ScreenError("screen capture is empty")
    if len(data) < 54 or data[:2] != b"BM":
        raise ScreenError("screen capture is not a BMP (bad signature or too short)")
    declared, offset = struct.unpack_from("<I4xI", data, 2)
    dib_size = struct.unpack_from("<I", data, 14)[0]
    if dib_size < 40:
        raise ScreenError(f"unsupported BMP header size {dib_size}")
    width, height, planes, bpp, compression = struct.unpack_from("<iiHHI", data, 18)
    if width <= 0 or height == 0 or planes != 1 or bpp not in (8, 16, 24, 32) or compression not in (0, 3):
        raise ScreenError(f"implausible BMP header w={width} h={height} planes={planes} bpp={bpp} comp={compression}")
    stride = ((width * bpp + 31) // 32) * 4
    needed = offset + stride * abs(height)
    if declared != len(data):
        raise ScreenError(f"BMP declared size {declared} != received {len(data)} (truncated/corrupt)")
    if offset < 14 + dib_size or len(data) < needed:
        raise ScreenError(f"BMP pixel data truncated ({len(data)} < {needed})")
    return Bmp(width, height, bpp, offset, stride, data)


def diff_ratio(prev: Optional[Bmp], cur: Bmp) -> Optional[float]:
    """Fraction of pixels that differ from the previous screen."""
    if prev is None:
        return None
    if (prev.width, prev.height, prev.bpp) != (cur.width, cur.height, cur.bpp):
        return 1.0
    step = cur.bpp // 8
    changed = 0
    for row_a, row_b in zip(prev.pixel_rows(), cur.pixel_rows()):
        if row_a == row_b:
            continue
        for i in range(0, len(row_a), step):
            if row_a[i:i + step] != row_b[i:i + step]:
                changed += 1
    return changed / float(cur.width * abs(cur.height))


# --------------------------------------------------------------------------
# Instrument access
# --------------------------------------------------------------------------

def values_equal(field: str, a: Any, b: Any, rel_tol: float) -> bool:
    if a is None or b is None:
        return False
    if field in FLOAT_FIELDS:
        return math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=1e-9)
    return a == b


def read_field(scope, field: str) -> Any:
    getter, _, extra = FIELD_METHODS[field]
    return getattr(scope, getter)(*extra)


def write_field(scope, field: str, value: Any) -> None:
    _, setter, extra = FIELD_METHODS[field]
    getattr(scope, setter)(*extra, value)


def refresh_transport(scope) -> None:
    """Open a fresh raw VXI-11 link when the transport supports it."""
    reconnect = getattr(getattr(scope, "resource", None), "reconnect", None)
    if callable(reconnect):
        reconnect()


def check_identity(scope) -> str:
    idn = scope.idn()
    if not isinstance(idn, str) or not idn.startswith(IDENTITY_PREFIX):
        raise IdentityError(f"identity guard failed: expected ID beginning {IDENTITY_PREFIX!r}, got {idn!r}")
    return idn


def snapshot(scope) -> Tuple[Dict[str, Any], Dict[str, str]]:
    baseline: Dict[str, Any] = {}
    unreadable: Dict[str, str] = {}
    for field in FIELD_ORDER:
        try:
            # Isolate every new/firmware-sensitive query. A timeout otherwise
            # leaves a late response queued and shifts all following readbacks.
            refresh_transport(scope)
            baseline[field] = read_field(scope, field)
        except Exception as exc:  # noqa: BLE001 - unreadable fields are never changed
            unreadable[field] = f"{type(exc).__name__}: {exc}"
    return baseline, unreadable


def read_channel_context(scope) -> Dict[str, bool]:
    return {"ch1_display": bool(scope.get_channel_display(1)),
            "ch2_display": bool(scope.get_channel_display(2))}


def apply_and_verify(scope, settings: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    """Write each field then immediately read it back; raise on mismatch."""
    readback: Dict[str, Any] = {}
    for field, value in settings:
        write_field(scope, field, value)
        try:
            actual = read_field(scope, field)
        except Exception as exc:  # noqa: BLE001
            raise StateMismatch(f"{field}: readback failed after write: {exc}") from exc
        readback[field] = actual
        if not values_equal(field, value, actual, FRAME_CUTOFF_REL_TOL):
            raise StateMismatch(f"{field}: requested {value!r}, readback {actual!r}")
    return readback


def verify_fields(scope, settings: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    readback: Dict[str, Any] = {}
    for field, value in settings:
        actual = read_field(scope, field)
        readback[field] = actual
        if not values_equal(field, value, actual, FRAME_CUTOFF_REL_TOL):
            raise StateMismatch(f"{field}: requested {value!r}, later readback {actual!r}")
    return readback


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------

def restore_baseline(scope, baseline: Dict[str, Any], log: Callable[[str], None] = print) -> Dict[str, Any]:
    """Restore ``baseline`` in dependency-aware order and verify by readback.

    Never raises for instrument errors: each field is attempted, one retry,
    and the report says exactly what is and is not restored."""
    report: Dict[str, Any] = {"fields": {}, "ok": True}

    def current(field: str) -> Any:
        try:
            refresh_transport(scope)
            return read_field(scope, field)
        except Exception:  # noqa: BLE001
            return None

    def same(field: str, value: Any) -> bool:
        return values_equal(field, baseline[field], value, RESTORE_CUTOFF_REL_TOL)

    def try_write(field: str) -> Optional[str]:
        try:
            refresh_transport(scope)
            write_field(scope, field, baseline[field])
        except Exception as exc:  # noqa: BLE001
            return f"{type(exc).__name__}: {exc}"
        return None

    now = {f: current(f) for f in baseline}
    mode_now = now.get("math.mode")
    for temp_mode, fields in RESTORE_STEPS:
        dirty = [f for f in fields if f in baseline and not same(f, now[f])]
        if not dirty:
            continue
        if temp_mode is not None and mode_now != temp_mode and "math.mode" in baseline:
            try_write_mode(scope, temp_mode)
            now["math.mode"] = mode_now = current("math.mode")
        for field in dirty:
            err = try_write(field)
            now[field] = current(field)
            if field == "math.mode":
                mode_now = now[field]
            entry = {"baseline": baseline[field], "readback": now[field], "error": err}
            report["fields"][field] = entry

    # Final verification pass with one retry per field.
    for field in baseline:
        value = current(field)
        err = None
        if not same(field, value):
            err = try_write(field)
            value = current(field)
        ok = same(field, value)
        entry = report["fields"].setdefault(field, {"baseline": baseline[field]})
        entry.update({"readback": value, "ok": ok})
        if err:
            entry["error"] = err
        if not ok:
            report["ok"] = False
            log(f"RESTORE FAILED for {field}: baseline {baseline[field]!r}, readback {value!r}")
    return report


def try_write_mode(scope, mode: str) -> Optional[str]:
    try:
        scope.set_math_mode(mode)
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return None


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

@dataclass
class RunConfig:
    host: str
    output_dir: str
    cycles: int
    dwell: float
    no_restore: bool
    filter_frequency: Optional[float]
    transport: str = "visa"
    timeout_ms: int = 5000
    retain_screens: int = 0


class Screensaver:
    def __init__(self, scope, config: RunConfig, sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] = print):
        self.scope = scope
        self.cfg = config
        self.sleep = sleep
        self.log = log
        self.evidence = Evidence(config.output_dir)
        self.recovery_path = os.path.join(config.output_dir, "recovery.json")
        self.recovery: Dict[str, Any] = {}
        self.index = 0
        self.prev_bmp: Optional[Bmp] = None
        self.prev_path: Optional[str] = None
        self.baseline: Dict[str, Any] = {}
        self.filter_center: Optional[float] = None
        self.armed = False
        self.stop_reason = "completed"
        self.exit_code = 0
        # Exact paths created by this instance and eligible for bounded
        # retention. The baseline capture is deliberately never added here.
        self._frame_files: List[str] = []

    # -- helpers -------------------------------------------------------------

    def _save_bmp(self, name: str, data: bytes, suffix: str = "bmp",
                  retention_eligible: bool = True) -> Optional[str]:
        if self.cfg.retain_screens == 0:
            return None
        path = os.path.join(self.cfg.output_dir, f"{self.index:04d}-{name}.{suffix}")
        with open(path, "xb") as fh:  # exclusive create guarantees a unique path
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if retention_eligible:
            self._frame_files.append(os.path.abspath(path))
        return path

    def _capture(self, name: str, permanent: bool = False) -> Tuple[Optional[str], bytes, Bmp]:
        if self.cfg.retain_screens > 0 and shutil.disk_usage(self.cfg.output_dir).free < MIN_FREE_BYTES:
            raise ScreenError("less than 200 MB free in the output directory")
        # The tested 5.00.0.19 firmware intermittently poisons a raw VXI-11
        # link after a burst of short queries, while the same long BMP query
        # succeeds on a fresh link. Reconnect is transport-only and does not
        # change instrument state. PyVISA/fakes simply have no reconnect().
        try:
            refresh_transport(self.scope)
            data = self.scope.capture_screen_bmp()
        except Exception as exc:  # noqa: BLE001 - normalize as retryable screen failure
            raise ScreenError(f"screen transport failed: {exc}") from exc
        try:
            bmp = parse_bmp(data)
        except ScreenError:
            if data:
                self._save_bmp(name, data, "bad", retention_eligible=not permanent)
                self._enforce_retention()
            raise
        return self._save_bmp(name, data, retention_eligible=not permanent), data, bmp

    def _enforce_retention(self) -> None:
        """Keep only the newest configured frame captures.

        Only exact paths successfully created by this instance are considered.
        The baseline screen is not in ``_frame_files`` and can never be removed.
        """
        output_dir = os.path.abspath(self.cfg.output_dir)
        while len(self._frame_files) > self.cfg.retain_screens:
            path = self._frame_files.pop(0)
            if os.path.dirname(path) != output_dir:
                raise ScreenError(f"refusing to evict screenshot outside output directory: {path}")
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise ScreenError(f"cannot evict old screenshot {path}: {exc}") from exc
            self.evidence.event({
                "type": "screenshot_evicted", "path": path,
                "retain_screens": self.cfg.retain_screens,
            })

    def _update_recovery(self, **changes: Any) -> None:
        self.recovery.update(changes)
        self.recovery["updated"] = utc_now()
        write_json_atomic(self.recovery_path, self.recovery)

    # -- start ------------------------------------------------------------------

    def prepare(self) -> List[Frame]:
        """Identity guard, snapshot, baseline screen, recovery file. No writes."""
        idn = check_identity(self.scope)
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        context = read_channel_context(self.scope)
        baseline, unreadable = snapshot(self.scope)
        self.baseline = baseline
        center = self.cfg.filter_frequency
        center_source = ("--filter-frequency" if center is not None
                         else "existing instrument cutoffs (not modified)")
        self.filter_center = center
        frames, skipped = runtime_plan(
            plan_frames(context["ch1_display"], context["ch2_display"], center,
                        include_filter=True), list(unreadable))
        for attempt in (1, 2):
            try:
                path0, data0, bmp0 = self._capture(
                    "baseline" + ("" if attempt == 1 else "-retry"), permanent=True)
                break
            except ScreenError as exc:
                if attempt == 2:
                    raise
                self.log(f"baseline screen attempt 1 failed: {exc}; retrying once")
        self.prev_bmp, self.prev_path = bmp0, path0
        self.recovery = {
            "schema_version": SCHEMA_VERSION, "created": utc_now(), "host": self.cfg.host,
            "idn": idn, "status": "armed", "context_not_modified": context,
            "baseline": baseline, "unreadable": unreadable,
            "baseline_screen": {"path": path0, "sha256": hashlib.sha256(data0).hexdigest()},
            "note": ("Written before the first change. If the program is killed (SIGKILL/power "
                     "loss) run: python examples/visual_screensaver.py --restore <this file>"),
        }
        write_json_atomic(self.recovery_path, self.recovery)
        self.armed = True
        self.evidence.ledger_header({"started": self.recovery["created"], "host": self.cfg.host,
                                     "idn": idn, "recovery": self.recovery_path})
        self.evidence.event({
            "type": "run_start", "host": self.cfg.host, "idn": idn, "channel_context": context,
            "filter_center_hz": center, "filter_center_source": center_source,
            "baseline_screen": self.recovery["baseline_screen"], "recovery": self.recovery_path,
            "unreadable": unreadable, "skipped_frames": skipped,
            "frames": [frame_to_dict(f) for f in frames], "cycles": self.cfg.cycles,
            "dwell": self.cfg.dwell, "no_restore": self.cfg.no_restore})
        return frames

    # -- frames -----------------------------------------------------------------

    def run_frame(self, frame: Frame, cycle: int) -> Dict[str, Any]:
        self.index += 1
        last_error = ""
        attempts = 0
        for attempts in (1, 2):
            requested = dict(frame.settings)
            try:
                readback = apply_and_verify(self.scope, frame.settings)
                self.sleep(self.cfg.dwell)
                path, data, bmp = self._capture(frame.name + ("" if attempts == 1 else "-retry"))
                readback = verify_fields(self.scope, frame.settings)
            except (StateMismatch, ScreenError) as exc:
                self._enforce_retention()
                last_error = f"{type(exc).__name__}: {exc}"
                self.log(f"  attempt {attempts} failed: {last_error}")
                continue
            except Exception as exc:  # noqa: BLE001 - transport errors are not retried
                self._fail_record(frame, cycle, attempts, f"{type(exc).__name__}: {exc}")
                raise
            ratio = diff_ratio(self.prev_bmp, bmp)
            notes = []
            verdict = "PASS"
            if ratio is not None and ratio < MIN_DIFF_RATIO:
                verdict = "WARN"
                notes.append("screen unchanged vs previous frame despite correct readback")
            if (bmp.width, bmp.height) != EXPECTED_SIZE:
                verdict = "WARN" if verdict == "PASS" else verdict
                notes.append(f"unexpected screen size {bmp.width}x{bmp.height}")
            if attempts == 2:
                verdict = "WARN" if verdict == "PASS" else verdict
                notes.append(f"succeeded on retry after: {last_error}")
            rec = {
                "index": self.index, "cycle": cycle, "name": frame.name, "group": frame.group,
                "requested": requested, "readback": readback, "bmp": path,
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                "width": bmp.width, "height": bmp.height, "previous_bmp": self.prev_path,
                "diff_ratio": ratio, "verdict": verdict, "attempts": attempts,
                "note": "; ".join(notes)}
            self.prev_bmp, self.prev_path = bmp, path
            self.evidence.frame(rec)
            self._enforce_retention()
            self.log(f"[{self.index:04d}] {frame.name}: {verdict}"
                     + (f" (diff {ratio:.4f})" if ratio is not None else ""))
            return rec
        self._fail_record(frame, cycle, attempts, last_error)
        raise ScreensaverError(f"frame {frame.name} failed after one retry: {last_error}")

    def _fail_record(self, frame: Frame, cycle: int, attempts: int, note: str) -> None:
        self.evidence.frame({
            "index": self.index, "cycle": cycle, "name": frame.name, "group": frame.group,
            "requested": dict(frame.settings), "readback": None, "bmp": None, "sha256": None,
            "diff_ratio": None, "verdict": "FAIL", "attempts": attempts, "note": note})

    def loop(self, frames: List[Frame]) -> None:
        cycle = 0
        context0 = self.recovery["context_not_modified"]
        while self.cfg.cycles == 0 or cycle < self.cfg.cycles:
            cycle += 1
            context = read_channel_context(self.scope)
            if context != context0:
                self.log(f"channel display state changed externally {context0} -> {context}; replanning")
                frames, skipped = runtime_plan(plan_frames(
                    context["ch1_display"], context["ch2_display"], self.filter_center,
                    include_filter=True),
                    list(self.recovery["unreadable"]))
                self.evidence.event({"type": "replan", "channel_context": context, "skipped_frames": skipped,
                                     "frames": [frame_to_dict(f) for f in frames]})
                context0 = context
            for frame in frames:
                self.run_frame(frame, cycle)

    # -- end --------------------------------------------------------------------

    def finish(self) -> None:
        if not self.armed:
            return
        if self.cfg.no_restore:
            self.log("!" * 70)
            self.log("WARNING: --no-restore was passed. The instrument was NOT restored.")
            self.log(f"Run later: python examples/visual_screensaver.py --restore {self.recovery_path}")
            self.log("!" * 70)
            self._update_recovery(status="not_restored", stop_reason=self.stop_reason)
            recovery_text = f"NOT restored (--no-restore); use --restore {self.recovery_path}"
        else:
            report = restore_baseline(self.scope, self.baseline, self.log)
            status = "restored" if report["ok"] else "restore_failed"
            self._update_recovery(status=status, stop_reason=self.stop_reason, restore_report=report)
            self.evidence.event({"type": "restore", "status": status, "report": report})
            recovery_text = f"{status}; baseline in {self.recovery_path}"
            if not report["ok"]:
                self.exit_code = 3
                self.log(f"RESTORE INCOMPLETE - run --restore {self.recovery_path}")
        self.evidence.event({"type": "run_end", "stop_reason": self.stop_reason,
                             "counts": self.evidence.counts, "recovery": recovery_text})
        self.evidence.ledger_footer({"stop_reason": self.stop_reason, "recovery": recovery_text})
        self.armed = False

    def run(self) -> int:
        try:
            frames = self.prepare()
            if not frames:
                raise ScreensaverError("no frames planned")
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
        # Restoration must not be cut short by another signal.
        saved = _block_signals()
        try:
            self.finish()
        finally:
            _unblock_signals(saved)
        return self.exit_code


def _block_signals() -> Dict[int, Any]:
    saved = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            saved[sig] = signal.signal(sig, signal.SIG_IGN)
        except ValueError:  # not the main thread
            pass
    return saved


def _unblock_signals(saved: Dict[int, Any]) -> None:
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def install_signal_handlers() -> None:
    def handler(signum, _frame):
        raise TerminationRequested(signum)
    signal.signal(signal.SIGTERM, handler)


# --------------------------------------------------------------------------
# --restore mode
# --------------------------------------------------------------------------

def run_restore(scope, path: str, log: Callable[[str], None] = print) -> int:
    with open(path, encoding="utf-8") as fh:
        recovery = json.load(fh)
    if recovery.get("schema_version") != SCHEMA_VERSION or not isinstance(recovery.get("baseline"), dict):
        log(f"ERROR: {path} is not a recognized recovery file")
        return 2
    idn = check_identity(scope)
    if idn != recovery.get("idn"):
        log(f"ERROR: instrument ID {idn!r} differs from the recovery file ID {recovery.get('idn')!r}; refusing")
        return 2
    baseline = {k: v for k, v in recovery["baseline"].items() if k in FIELD_METHODS}
    report = restore_baseline(scope, baseline, log)
    recovery["status"] = "restored" if report["ok"] else "restore_failed"
    recovery["restore_report"] = report
    recovery["updated"] = utc_now()
    write_json_atomic(path, recovery)
    log(f"restore {recovery['status']}: {len(baseline)} fields checked")
    return 0 if report["ok"] else 3


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _nonneg_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be a finite number >= 0")
    return value


def _nonneg_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0 (0 = infinite)")
    return value


def _screen_retention(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0 (0 = do not persist screen files)")
    return value


def _filter_freq(text: str) -> float:
    try:
        return validate_filter_center(float(text))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Visual screensaver for the MP720115 (documented controls only).",
                                epilog="SIGKILL/power loss cannot restore automatically: use --restore PATH.")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--transport", choices=["visa", "raw-vxi11"], default="visa")
    p.add_argument("--timeout-ms", type=int, default=5000)
    p.add_argument("--cycles", type=_nonneg_int, default=1, help="0 = infinite (default 1)")
    p.add_argument("--dwell", type=_nonneg_float, default=2.0,
                   help="minimum seconds to show each frame before in-memory screen verification")
    p.add_argument("--output-dir", default=None,
                   help="default: output/screensaver-<UTC timestamp>")
    p.add_argument("--retain-screens", type=_screen_retention, default=0, metavar="N",
                   help="persist at most N frame BMP/bad captures; 0 saves no screen files (default 0); "
                        "when N > 0 the baseline is always kept")
    p.add_argument("--filter-frequency", type=_filter_freq, default=None, metavar="HZ",
                   help="experimental cutoff centre Hz for CH1 filters; default preserves existing cutoffs")
    p.add_argument("--no-restore", action="store_true",
                   help="DANGEROUS: leave the final frame on the instrument")
    p.add_argument("--restore", metavar="PATH", help="restore baseline from a recovery JSON and exit")
    p.add_argument("--dry-run", action="store_true", help="print the planned frames; never connect")
    return p


def connect(args):
    if args.transport == "raw-vxi11":
        return MP720115.connect_raw_vxi11(args.host, timeout_ms=args.timeout_ms)
    return MP720115.connect_visa(args.host, timeout_ms=args.timeout_ms)


def dry_run_plan(filter_frequency: Optional[float]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"note": "dry run: no connection made; filter frames preserve existing "
                                   "cutoffs unless --filter-frequency is supplied"}
    for label, ch2 in (("ch2_on", True), ("ch2_off", False)):
        frames = plan_frames(True, ch2, filter_frequency, include_filter=True)
        out[label] = [frame_to_dict(f) for f in frames]
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.dry_run:
        print(json.dumps(dry_run_plan(args.filter_frequency), indent=2))
        return 0
    if args.no_restore and args.restore:
        print("ERROR: --no-restore cannot be combined with --restore", file=sys.stderr)
        return 2
    if args.no_restore:
        print("!" * 70, file=sys.stderr)
        print("WARNING: --no-restore: the instrument will be left in the last frame style.",
              file=sys.stderr)
        print("!" * 70, file=sys.stderr)
    try:
        scope = connect(args)
    except MP720115Error as exc:
        print(f"ERROR: cannot connect: {exc}", file=sys.stderr)
        return 2
    try:
        if args.restore:
            try:
                return run_restore(scope, args.restore)
            except IdentityError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
        output_dir = args.output_dir or os.path.join(
            "output", "screensaver-" + _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S"))
        cfg = RunConfig(args.host, output_dir, args.cycles, args.dwell, args.no_restore,
                        args.filter_frequency, args.transport, args.timeout_ms,
                        args.retain_screens)
        install_signal_handlers()
        print("Controls are documented and live-verified on firmware 5.00.0.19. "
              "SIGKILL/power loss needs --restore PATH.")
        return Screensaver(scope, cfg).run()
    finally:
        try:
            scope.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
