"""
Reusable MP720115 controller using the UNI-T UPO3000E/UPO2000E SCPI dialect.

Command syntax in this module is sourced from the manufacturer's official
Programming Manual: "UPO3000E&UPO2000E Series Oscilloscope Programming
Manual" (Uni-Trend Technology (China) Co., Ltd., 2017), obtained from
https://unitrend.oss-cn-hongkong.aliyuncs.com/uploads/attach/20250624/
171046f30ffda67ff849c78002191.pdf (official UNI-T download-center link).
See docs/VALIDATION.md for the verified/unverified status of every command
used here.

SAFETY RULES (enforced structurally - these commands/paths DO NOT EXIST
anywhere in this module, not even behind a flag):
    - *RST                              (factory reset)
    - *CLS                              (not even documented by the manual)
    - :SYSTem:CAL                       (self-calibration)
    - :SYSTem:CLEAr                     (empties stored waveforms/setups)
    - :SYSTem:COMMunicate:LAN:*         (network configuration)
    - :AUTO                             (auto-setup, changes all settings)
    - :SYSTem:SETup / raw service/telnet commands
    - Arbitrary/raw SCPI pass-through from the CLI

Transport contract
-------------------
This class is transport-agnostic: the ``resource`` object passed to
``MP720115(resource)`` only needs to implement the small subset of the
PyVISA ``MessageBasedResource`` API actually used here:
    - ``write(command: str) -> int``
    - ``query(command: str) -> str``
    - ``read_raw() -> bytes``
    - ``timeout`` (settable, milliseconds)
    - ``close() -> None``
Real PyVISA resources satisfy this automatically. See
:meth:`MP720115.connect_visa` for the intended production path, and
:mod:`mp72_control.vxi11_raw_transport` for the dependency-free fallback
adapter.
"""

from __future__ import annotations

import struct
import math
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence, Tuple

from .exceptions import MP720115Error, TransportError, ValidationError, VerificationError
from .scpi_blocks import (
    Preamble,
    header_total_len,
    parse_ieee_block,
    parse_preamble,
    peek_declared_length,
)

CHANNELS: Tuple[int, ...] = (1, 2, 3, 4)

CHANNEL_COUPLING = {"DC", "AC", "GND"}
PROBE_FACTORS = {"0.001X", "0.01X", "0.1X", "1X", "10X", "100X", "1000X"}

TIMEBASE_MODES = {"MAIN": "MAIN", "WINDOW": "WIND"}

ACQUIRE_TYPES = {
    "NORMAL": "NORM",
    "AVERAGE": "AVER",
    "PEAKDETECT": "PEAK",
    "ENVELOPE": "ENV",
    "HRESOLUTION": "HRES",
}
ACQUIRE_MODES = {"REALTIME": "RTIM", "EQUIVALENTTIME": "ETIM"}
MEMORY_DEPTHS = {"AUTO", "700", "7K", "70K", "700K", "7M", "70M"}

TRIGGER_SOURCES = {f"CHANNEL{n}": f"CHAN{n}" for n in CHANNELS}
TRIGGER_SOURCES.update({"EXT": "EXT", "EXT5": "EXT5", "ACLINE": "ACLine"})
TRIGGER_SLOPES = {"POSITIVE": "POS", "NEGATIVE": "NEG", "ALTERNATION": "ALT"}
TRIGGER_SWEEP = {"AUTO": "AUTO", "NORMAL": "NORM", "SINGLE": "SING"}
TRIGGER_COUPLING = {"DC", "AC", "LF", "HF", "NOISE"}

WAVEFORM_FORMATS = {"BYTE": "BYTE", "WORD": "WORD", "ASCII": "ASCII"}
WAVEFORM_SOURCES = {f"CHANNEL{n}": f"CHAN{n}" for n in CHANNELS}

MEASUREMENTS = {
    "VPP": "VPP?",
    "VMAX": "VMAX?",
    "VMIN": "VMIN?",
    "VAMPLITUDE": "VAMP?",
    "VTOP": "VTOP?",
    "VBASE": "VBAS?",
    "VMIDDLE": "VMID?",
    "VAVERAGE": "VAV?",
    "VRMS": "VRMS?",
    "FREQUENCY": "FREQ?",
    "PERIOD": "PER?",
    "RISETIME": "RIS?",
    "FALLTIME": "FALL?",
    "OVERSHOOT": "OVER?",
}


def _enum_table(*tokens: str) -> Dict[str, Tuple[str, str]]:
    """Map every accepted spelling of a documented enum to
    ``(canonical, command_token)``. The canonical form is the full upper-case
    word; the abbreviation is the manual's capitalised prefix (``VECTors`` ->
    ``VECT``). The command token is the manual's mixed-case spelling."""
    table: Dict[str, Tuple[str, str]] = {}
    for token in tokens:
        canonical = token.upper()
        short = "".join(c for c in token if c.isupper() or c.isdigit())
        for alias in {canonical, short}:
            table[alias] = (canonical, token)
    return table


DISPLAY_FORMATS = _enum_table("VECTors", "DOTS")
GRID_MODES = _enum_table("FULL", "GRID", "CROSS", "NONE")
DISPLAY_TYPES = _enum_table("XY12", "XY34", "YT")
MATH_MODES = _enum_table("MATH", "FFT", "LOGic", "FILTer", "ADVance")
MATH_OPERATIONS = _enum_table(
    "ADD", "SUBTract", "MULTiply", "DIVide", "AND", "OR", "NOT", "XOR"
)
FFT_WINDOWS = _enum_table("RECTangular", "HANNing", "HAMMing", "BMAN")
FFT_VTYPES = _enum_table("VRMS", "DBRMS")
FILTER_TYPES = _enum_table("LP", "HP", "BP", "BS")
FUNCTION_SOURCES: Tuple[int, ...] = (1, 2, 3, 4)
BRIGHTNESS_RANGE = (1, 100)
CURSOR_MODES = _enum_table("TRACK", "INDependent")
CURSOR_TYPES = _enum_table("AMPlitude", "TIME", "CLOSe")
CURSOR_SOURCES: Tuple[int, ...] = (1, 2, 3, 4)
CURSOR_POSITION_RANGES = {
    "TIME": (0, 699),
    "AMPLITUDE": (28, 227),
}


def _fmt_num(value: float) -> str:
    """Format a float for SCPI transmission (plain decimal, no exponent
    padding games - the instrument's examples accept plain numbers like
    ``2`` or ``20`` and echo back scientific notation on query)."""
    if isinstance(value, bool):
        raise ValidationError(f"expected a number, got bool {value!r}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"expected a finite number, got {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValidationError(f"expected a finite number, got {value!r}")
    return repr(parsed)


def _validate(value: str, allowed: Dict[str, str] | Iterable[str], what: str) -> str:
    key = value.strip().upper()
    if isinstance(allowed, dict):
        if key not in allowed:
            raise ValidationError(f"invalid {what} {value!r}; allowed: {sorted(allowed)}")
        return allowed[key]
    allowed_set = set(a.upper() for a in allowed)
    if key not in allowed_set:
        raise ValidationError(f"invalid {what} {value!r}; allowed: {sorted(allowed_set)}")
    return key


def parse_memory_depth(text: str) -> Optional[float]:
    """Convert a depth string (``7K``, ``7.00K``, ``7000``, ``7M``) to a
    point count. Returns None for ``AUTO``; raises VerificationError for
    anything unparseable."""
    token = str(text).strip().upper()
    if token == "AUTO":
        return None
    scale = 1.0
    if token.endswith("K"):
        scale, token = 1e3, token[:-1]
    elif token.endswith("M"):
        scale, token = 1e6, token[:-1]
    try:
        return float(token) * scale
    except ValueError as exc:
        raise VerificationError(f"unparseable memory depth readback {text!r}") from exc


def check_trigger_level(requested: float, actual: float, tolerance: Optional[float] = None) -> None:
    """Raise VerificationError if ``actual`` differs from ``requested`` by
    more than ``tolerance`` volts (default: max(10 mV, 1 % of the request))."""
    requested = float(_fmt_num(requested))
    if tolerance is None:
        tolerance = max(0.01, 0.01 * abs(requested))
    elif isinstance(tolerance, bool) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValidationError(f"trigger level tolerance must be a non-negative number, got {tolerance!r}")
    if not math.isfinite(actual) or abs(actual - requested) > tolerance:
        raise VerificationError(
            f"trigger level mismatch: requested {requested!r} V, instrument reports {actual!r} V "
            f"(tolerance {tolerance!r} V); the instrument did not apply the level "
            "(it may have clamped to a firmware range limit)"
        )


@dataclass
class AcquisitionReport:
    """Verified readback after :meth:`MP720115.apply_acquisition`."""
    acquire_type: str
    memory_depth: str
    average_count: int
    warnings: list


@dataclass
class WaveformResult:
    """Result of :meth:`MP720115.capture_waveform`."""

    source: str
    format: str
    requested_points: int
    preamble: Preamble
    raw_codes: Sequence[int]
    volts: Optional[Sequence[float]]
    times: Sequence[float]
    volts_provisional: bool


class MP720115:
    """Controller for a UNI-T-identified MP720115 over VXI-11.

    The implemented SCPI dialect is documented in the UPO3000E/UPO2000E
    programming manual (TCPIP::<host>::inst0::INSTR).
    """

    def __init__(self, resource, resource_manager=None):
        self.resource = resource
        self._resource_manager = resource_manager

    # -- construction -----------------------------------------------------

    @classmethod
    def connect_visa(
        cls,
        host: str,
        backend: str = "@py",
        device: str = "inst0",
        timeout_ms: int = 5000,
    ) -> "MP720115":
        """Production connection path using PyVISA + pyvisa-py.

        Requires ``pip install pyvisa pyvisa-py`` (see
        requirements.txt). Raises ``MP720115Error`` with a clear
        message if pyvisa is not installed, instead of a bare
        ``ImportError``.
        """
        try:
            import pyvisa  # type: ignore
        except ImportError as exc:
            raise MP720115Error(
                "pyvisa is not installed. Run: "
                "python3 -m pip install -r requirements.txt"
            ) from exc
        try:
            rm = pyvisa.ResourceManager(backend)
        except Exception as exc:
            raise TransportError(f"failed to initialize PyVISA backend {backend!r}: {exc}") from exc
        try:
            resource = rm.open_resource(f"TCPIP::{host}::{device}::INSTR")
            resource.timeout = timeout_ms
        except Exception as exc:
            rm.close()
            raise TransportError(
                f"failed to open VXI-11 instrument {host!r}/{device!r}: {exc}"
            ) from exc
        return cls(resource, resource_manager=rm)

    @classmethod
    def connect_raw_vxi11(cls, host: str, device: str = "inst0", timeout_ms: int = 5000) -> "MP720115":
        """Fallback connection using the dependency-free VXI-11
        transport (see :mod:`mp72_control.vxi11_raw_transport`). Uses
        identical controller logic to :meth:`connect_visa`.
        """
        from .vxi11_raw_transport import VXI11RawTransport

        transport = VXI11RawTransport(host, device=device, timeout=timeout_ms)
        return cls(transport)

    def close(self) -> None:
        try:
            self.resource.close()
        finally:
            if self._resource_manager is not None:
                self._resource_manager.close()
                self._resource_manager = None

    def __enter__(self) -> "MP720115":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @contextmanager
    def _timeout_ms(self, ms: int):
        """Temporarily override the transport timeout, then restore it."""
        original = getattr(self.resource, "timeout", None)
        try:
            self.resource.timeout = ms
            yield
        finally:
            if original is not None:
                self.resource.timeout = original

    # -- low-level helpers --------------------------------------------------

    def _query(self, cmd: str) -> str:
        try:
            # Firmware 5.00.0.19 sometimes NUL-pads short enum responses
            # (observed as ``YT\x00\x00``). Treat trailing NUL like a transport
            # terminator, not as part of the SCPI value.
            return self.resource.query(cmd).strip(" \t\r\n\x00")
        except Exception as exc:  # noqa: BLE001 - normalize all transport errors
            raise TransportError(f"query {cmd!r} failed: {exc}") from exc

    def _write(self, cmd: str) -> None:
        try:
            self.resource.write(cmd)
        except Exception as exc:  # noqa: BLE001
            raise TransportError(f"write {cmd!r} failed: {exc}") from exc

    def _query_raw_block(self, cmd: str) -> bytes:
        try:
            self.resource.write(cmd)
            raw = bytes(self.resource.read_raw())

            # MP720115 firmware 5.00.0.19 incorrectly sets the VXI-11 END
            # reason bit on every 1024-byte chunk. pyvisa-py's read_raw()
            # therefore returns only the first chunk of a long block. Once
            # the IEEE 488.2 header is present, read the exact remainder;
            # read_bytes(..., break_on_termchar=False) deliberately ignores
            # the premature message-end status while satisfying the count.
            if raw.startswith(b"#"):
                header_len = header_total_len(raw)
                declared = peek_declared_length(raw)
                if header_len is not None and declared is not None:
                    total = header_len + declared
                    if len(raw) < total:
                        read_bytes = getattr(self.resource, "read_bytes", None)
                        if not callable(read_bytes):
                            raise TransportError(
                                f"binary response is truncated ({len(raw)}/{total} bytes) "
                                "and the transport cannot read an exact remainder"
                            )
                        raw += bytes(
                            read_bytes(
                                total - len(raw),
                                chunk_size=1024,
                                break_on_termchar=False,
                            )
                        )
            return parse_ieee_block(raw)
        except Exception as exc:  # noqa: BLE001
            raise TransportError(f"binary query {cmd!r} failed: {exc}") from exc

    @staticmethod
    def _chan_token(n: int) -> str:
        if n not in CHANNELS:
            raise ValidationError(f"invalid channel {n!r}; allowed: {CHANNELS}")
        return f"CHAN{n}"

    @staticmethod
    def _positive_float(value: float, what: str) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{what} must be a positive finite number") from exc
        if not math.isfinite(parsed) or parsed <= 0:
            raise ValidationError(f"{what} must be a positive finite number")
        return parsed

    # -- identity / system (read-only) --------------------------------------

    def idn(self) -> str:
        """``*IDN?`` - manufacturer, model, serial, firmware version."""
        return self._query("*IDN?")

    def system_version(self) -> str:
        """``:SYSTem:VERSion?`` - ``HW:..;SW:..;PD:..;ICV:..`` string."""
        return self._query(":SYSTem:VERSion?")

    # -- run/stop (documented, no readback query exists per manual) --------

    def run(self) -> None:
        """``:RUN`` - start sampling."""
        self._write(":RUN")

    def stop(self) -> None:
        """``:STOP`` - stop sampling."""
        self._write(":STOP")

    # -- channel configuration ----------------------------------------------

    def get_channel_display(self, ch: int) -> bool:
        return self._query(f":{self._chan_token(ch)}:DISPlay?") in ("1", "ON")

    def set_channel_display(self, ch: int, on: bool) -> None:
        self._write(f":{self._chan_token(ch)}:DISPlay {'ON' if on else 'OFF'}")

    def get_channel_coupling(self, ch: int) -> str:
        return self._query(f":{self._chan_token(ch)}:COUPling?")

    def set_channel_coupling(self, ch: int, mode: str) -> None:
        mode = _validate(mode, CHANNEL_COUPLING, "channel coupling")
        self._write(f":{self._chan_token(ch)}:COUPling {mode}")

    def get_channel_scale(self, ch: int) -> float:
        return float(self._query(f":{self._chan_token(ch)}:SCALe?"))

    def set_channel_scale(self, ch: int, volts_per_div: float) -> None:
        value = self._positive_float(volts_per_div, "channel scale")
        self._write(f":{self._chan_token(ch)}:SCALe {_fmt_num(value)}")

    def get_channel_offset(self, ch: int) -> float:
        return float(self._query(f":{self._chan_token(ch)}:OFFSet?"))

    def set_channel_offset(self, ch: int, volts: float) -> None:
        self._write(f":{self._chan_token(ch)}:OFFSet {_fmt_num(volts)}")

    def get_channel_probe(self, ch: int) -> str:
        return self._query(f":{self._chan_token(ch)}:PROBe?")

    def set_channel_probe(self, ch: int, factor: str) -> None:
        factor = _validate(factor, PROBE_FACTORS, "probe factor")
        self._write(f":{self._chan_token(ch)}:PROBe {factor}")

    def get_channel_bwlimit(self, ch: int) -> bool:
        return self._query(f":{self._chan_token(ch)}:BWLimit?") in ("1", "ON")

    def set_channel_bwlimit(self, ch: int, on: bool) -> None:
        self._write(f":{self._chan_token(ch)}:BWLimit {'ON' if on else 'OFF'}")

    # -- timebase -------------------------------------------------------------

    def get_timebase_mode(self) -> str:
        return self._query(":TIMebase:MODE?")

    def set_timebase_mode(self, mode: str) -> None:
        mode = _validate(mode, TIMEBASE_MODES, "timebase mode")
        self._write(f":TIMebase:MODE {mode}")

    def get_timebase_scale(self) -> float:
        return float(self._query(":TIMebase:SCALe?"))

    def set_timebase_scale(self, seconds_per_div: float) -> None:
        value = self._positive_float(seconds_per_div, "timebase scale")
        self._write(f":TIMebase:SCALe {_fmt_num(value)}")

    def get_timebase_offset(self) -> float:
        return float(self._query(":TIMebase:OFFSet?"))

    def set_timebase_offset(self, seconds: float) -> None:
        self._write(f":TIMebase:OFFSet {_fmt_num(seconds)}")

    # -- acquisition ------------------------------------------------------------

    def get_acquire_type(self) -> str:
        return self._query(":ACQuire:TYPE?")

    def set_acquire_type(self, acq_type: str) -> None:
        acq_type = _validate(acq_type, ACQUIRE_TYPES, "acquire type")
        self._write(f":ACQuire:TYPE {acq_type}")

    def get_acquire_mode(self) -> str:
        return self._query(":ACQuire:MODE?")

    def set_acquire_mode(self, mode: str) -> None:
        mode = _validate(mode, ACQUIRE_MODES, "acquire mode")
        self._write(f":ACQuire:MODE {mode}")

    def get_memory_depth(self) -> str:
        return self._query(":ACQuire:MEMory:DEPTh?")

    def set_memory_depth(self, depth: str) -> None:
        depth = _validate(depth, MEMORY_DEPTHS, "memory depth")
        self._write(f":ACQuire:MEMory:DEPTh {depth}")

    def get_average_count(self) -> int:
        return int(float(self._query(":ACQuire:AVERages:COUNt?")))

    @staticmethod
    def _check_average_count(count) -> int:
        if isinstance(count, bool):
            raise ValidationError(
                f"average count must be a power of 2 between 2 and 8192, got {count!r}"
            )
        try:
            parsed = int(count)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"average count must be a power of 2 between 2 and 8192, got {count!r}"
            ) from exc
        if parsed != count or not (2 <= parsed <= 8192) or (parsed & (parsed - 1)) != 0:
            raise ValidationError(
                f"average count must be a power of 2 between 2 and 8192, got {count!r}"
            )
        return parsed

    def set_average_count(self, count: int) -> None:
        parsed = self._check_average_count(count)
        self._write(f":ACQuire:AVERages:COUNt {parsed}")

    def apply_acquisition(
        self,
        acq_type: Optional[str] = None,
        mode: Optional[str] = None,
        memory_depth: Optional[str] = None,
        average_count: Optional[int] = None,
    ) -> AcquisitionReport:
        """Apply acquisition settings in dependency order and verify readback.

        The firmware only accepts the average count while in AVERAGE type,
        so a requested count temporarily selects AVERAGE and then moves to
        the requested type (or restores the previous one). Memory depth is
        applied last because the type can change it. Raises
        VerificationError on any mismatch; an AUTO depth request that reads
        back as a concrete depth is reported as a warning instead.
        """
        if memory_depth is not None:
            depth_token = _validate(memory_depth, MEMORY_DEPTHS, "memory depth")
        if acq_type is not None:
            type_token = _validate(acq_type, ACQUIRE_TYPES, "acquire type")
        if average_count is not None:
            self._check_average_count(average_count)
        warnings: list = []
        errors: list = []

        target = type_token if acq_type is not None else None
        current = None
        if average_count is not None or acq_type is not None:
            current = self._type_token(self.get_acquire_type())
        if target is None:
            target = current
        if mode is not None:
            self.set_acquire_mode(mode)
        if average_count is not None:
            if current != "AVER":
                self._write(":ACQuire:TYPE AVER")
            self.set_average_count(average_count)
            got = self.get_average_count()
            if got != average_count:
                errors.append(f"average count mismatch: requested {average_count}, instrument reports {got}")
            if target != "AVER":
                self._write(f":ACQuire:TYPE {target}")
        elif acq_type is not None and target != current:
            self._write(f":ACQuire:TYPE {target}")
        if memory_depth is not None:
            self.set_memory_depth(depth_token)

        final_type = self.get_acquire_type()
        final_depth = self.get_memory_depth()
        final_count = self.get_average_count()
        if target is not None and self._type_token(final_type) != target:
            errors.append(f"acquire type mismatch: requested {target}, instrument reports {final_type!r}")
        if (average_count is not None and final_count != average_count
                and not any(e.startswith("average count") for e in errors)):
            errors.append(f"average count mismatch: requested {average_count}, instrument reports {final_count}")
        if memory_depth is not None:
            want = parse_memory_depth(depth_token)
            have = parse_memory_depth(final_depth)
            if want is None:
                if have is not None:
                    warnings.append(
                        f"memory depth AUTO requested; instrument reports concrete depth {final_depth!r}")
            elif have != want:
                errors.append(
                    f"memory depth mismatch: requested {depth_token}, instrument reports {final_depth!r}")
        if errors:
            raise VerificationError("; ".join(errors))
        return AcquisitionReport(final_type, final_depth, final_count, warnings)

    @staticmethod
    def _type_token(readback: str) -> Optional[str]:
        upper = readback.strip().upper()
        for token in ACQUIRE_TYPES.values():
            if upper.startswith(token):
                return token
        raise VerificationError(f"unrecognized acquire type readback {readback!r}")

    # -- edge trigger -----------------------------------------------------------

    def set_edge_trigger_mode(self) -> None:
        """``:TRIGger:MODE EDGE`` - select edge trigger as the active
        trigger type (this package only implements edge-trigger control,
        per the task's explicit scope)."""
        self._write(":TRIGger:MODE EDGE")

    def get_trigger_mode(self) -> str:
        return self._query(":TRIGger:MODE?")

    def get_trigger_source(self) -> str:
        return self._query(":TRIGger:SOURce?")

    def set_trigger_source(self, source: str) -> None:
        source = _validate(source, TRIGGER_SOURCES, "trigger source")
        self._write(f":TRIGger:SOURce {source}")

    def get_trigger_edge_slope(self) -> str:
        return self._query(":TRIGger:EDGE:SLOPe?")

    def set_trigger_edge_slope(self, slope: str) -> None:
        slope = _validate(slope, TRIGGER_SLOPES, "trigger edge slope")
        self._write(f":TRIGger:EDGE:SLOPe {slope}")

    def get_trigger_level(self) -> float:
        return float(self._query(":TRIGger:LEVel?"))

    def set_trigger_level(self, volts: float) -> None:
        self._write(f":TRIGger:LEVel {_fmt_num(volts)}")

    def set_trigger_level_verified(self, volts: float, tolerance: Optional[float] = None) -> float:
        """Set the trigger level, read it back, and raise VerificationError
        if it was not applied (e.g. clamped). Returns the readback."""
        self.set_trigger_level(volts)
        actual = self.get_trigger_level()
        check_trigger_level(volts, actual, tolerance)
        return actual

    def get_trigger_sweep(self) -> str:
        return self._query(":TRIGger:SWEep?")

    def set_trigger_sweep(self, sweep: str) -> None:
        sweep = _validate(sweep, TRIGGER_SWEEP, "trigger sweep mode")
        self._write(f":TRIGger:SWEep {sweep}")

    def get_trigger_coupling(self) -> str:
        return self._query(":TRIGger:COUPling?")

    def set_trigger_coupling(self, coupling: str) -> None:
        coupling = _validate(coupling, TRIGGER_COUPLING, "trigger coupling")
        self._write(f":TRIGger:COUPling {coupling}")

    # -- measurements (read-only queries) ---------------------------------------

    def measure(self, measurement: str, source: Optional[int] = None) -> float:
        """Query one of the documented :MEASure:<X>? scalar measurements.

        ``measurement`` is one of the keys of :data:`MEASUREMENTS`
        (case-insensitive), e.g. ``"VPP"``, ``"FREQUENCY"``, ``"PERIOD"``.
        """
        key = measurement.strip().upper()
        if key not in MEASUREMENTS:
            raise ValidationError(
                f"invalid measurement {measurement!r}; allowed: {sorted(MEASUREMENTS)}"
            )
        suffix = MEASUREMENTS[key]
        chan = self._chan_token(source) if source is not None else None
        if chan is not None:
            response = self._query(f":MEASure:{suffix} {chan}")
        else:
            response = self._query(f":MEASure:{suffix}")
        # LIVE FINDING (not documented in the manual): the instrument
        # returns the literal string "*" instead of a number when a
        # measurement cannot be computed on the current waveform (e.g.
        # FREQuency?/PERiod?/RISetime?/FALLtime? on a signal with no
        # detectable edges/cycles). Surface this as a clear error instead
        # of a confusing float-conversion traceback.
        if response.strip() == "*":
            raise MP720115Error(
                f":MEASure:{suffix}{'' if chan is None else ' ' + chan} returned '*' "
                "(instrument could not compute this measurement on the current "
                "waveform - e.g. no qualifying edges/cycles present)"
            )
        return float(response)

    # -- waveform capture ---------------------------------------------------------

    def get_waveform_source(self) -> str:
        return self._query(":WAVeform:SOURce?")

    def get_waveform_format(self) -> str:
        return self._query(":WAVeform:FORMat?")

    def get_waveform_points(self) -> int:
        return int(float(self._query(":WAVeform:POINts?")))

    def get_preamble(self) -> Preamble:
        return parse_preamble(self._query(":WAVeform:PREamble?"))

    def capture_waveform(
        self,
        channel: int,
        points: int = 120,
        fmt: str = "BYTE",
        compute_volts: bool = True,
    ) -> WaveformResult:
        """Capture ``points`` samples from ``channel``.

        A disabled source is rejected before transfer settings are changed.
        The prior ``:WAVeform:SOURce``, ``:WAVeform:FORMat`` and
        ``:WAVeform:POINts`` values are restored on success or failure.
        If the transport offers ``reconnect()``, restoration is retried on
        a fresh VXI-11 link after an I/O failure.

        Only ``BYTE`` is treated as having a well-understood 1-byte/code
        layout in this method (per the manual: BYTE = single-byte
        unsigned code per sample). ``WORD`` is parsed as little-endian
        16-bit unsigned per the manual's explicit example
        ("WORD: return two bytes 0x78 and 0x00 (little end format)").
        ``ASCII`` is parsed as a comma-separated IEEE-488.2 block of
        decimal codes.

        Voltage scaling uses :meth:`Preamble.code_to_volts`. Its offset
        sign was checked live by sweeping and restoring CH1 offset, but
        absolute accuracy was not checked against a calibrated source.
        The result therefore marks converted values as provisional.
        """
        fmt_token = _validate(fmt, WAVEFORM_FORMATS, "waveform format")
        chan_token = self._chan_token(channel)
        if isinstance(points, bool):
            raise ValidationError("waveform points must be a positive integer")
        try:
            requested_points = int(points)
        except (TypeError, ValueError) as exc:
            raise ValidationError("waveform points must be a positive integer") from exc
        if requested_points <= 0 or requested_points != points:
            raise ValidationError("waveform points must be a positive integer")

        if not self.get_channel_display(channel):
            raise ValidationError(
                f"{chan_token} is disabled; enable it before waveform capture"
            )

        prior_source = self.get_waveform_source()
        prior_format = self.get_waveform_format()
        prior_points = self.get_waveform_points()

        restore_commands = (
            f":WAVeform:POINts {prior_points}",
            f":WAVeform:FORMat {prior_format}",
            f":WAVeform:SOURce {prior_source}",
        )

        def restore_state() -> None:
            try:
                for command in restore_commands:
                    self._write(command)
                return
            except Exception as first_error:
                reconnect = getattr(self.resource, "reconnect", None)
                if not callable(reconnect):
                    raise TransportError(
                        f"failed to restore waveform transfer state: {first_error}"
                    ) from first_error
            try:
                reconnect()
                for command in restore_commands:
                    self._write(command)
            except Exception as retry_error:
                raise TransportError(
                    "failed to restore waveform transfer state after reconnect: "
                    f"{retry_error}"
                ) from retry_error

        try:
            self._write(f":WAVeform:SOURce {chan_token}")
            self._write(f":WAVeform:FORMat {fmt_token}")
            self._write(f":WAVeform:POINts {requested_points}")

            preamble = self.get_preamble()
            payload = self._query_raw_block(":WAVeform:DATA?")

            if fmt_token == "BYTE":
                raw_codes = list(payload)
            elif fmt_token == "WORD":
                if len(payload) % 2 != 0:
                    raise MP720115Error(
                        f"WORD-format payload has odd length {len(payload)}; cannot unpack"
                    )
                raw_codes = list(struct.unpack(f"<{len(payload)//2}H", payload))
            else:  # ASCII
                text = payload.decode("ascii", errors="replace")
                raw_codes = [int(float(v)) for v in text.strip().split(",") if v.strip()]

            volts = None
            if compute_volts:
                volts = [preamble.code_to_volts(c) for c in raw_codes]
            times = [preamble.code_to_time(i) for i in range(len(raw_codes))]

            capture = WaveformResult(
                source=chan_token,
                format=fmt_token,
                requested_points=requested_points,
                preamble=preamble,
                raw_codes=raw_codes,
                volts=volts,
                times=times,
                volts_provisional=bool(compute_volts),
            )
        except Exception as capture_error:
            try:
                restore_state()
            except Exception as restore_error:
                raise TransportError(
                    f"waveform capture failed ({capture_error}); additionally, "
                    f"state restoration failed ({restore_error})"
                ) from capture_error
            raise
        else:
            restore_state()
            return capture

    # -- display styling (documented in the manual; not live-verified) -------
    # No clear/erase command is implemented; only these allowlisted controls.

    @staticmethod
    def _enum_set(table, value, what: str) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"invalid {what} {value!r}; allowed: {sorted(set(c for c, _ in table.values()))}")
        key = value.strip().upper()
        if key not in table:
            raise ValidationError(
                f"invalid {what} {value!r}; allowed: {sorted(set(c for c, _ in table.values()))}"
            )
        return table[key][1]

    def _enum_get(self, cmd: str, table, what: str) -> str:
        raw = self._query(cmd)
        key = raw.strip().strip('"').upper()
        if key not in table:
            raise VerificationError(f"unrecognized {what} readback {raw!r} for {cmd!r}")
        return table[key][0]

    @staticmethod
    def _brightness(value) -> int:
        lo, hi = BRIGHTNESS_RANGE
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f"brightness must be an integer {lo}..{hi}, got {value!r}")
        if not math.isfinite(value) or value != int(value) or not lo <= int(value) <= hi:
            raise ValidationError(f"brightness must be an integer {lo}..{hi}, got {value!r}")
        return int(value)

    def _get_brightness(self, cmd: str) -> int:
        raw = self._query(cmd)
        try:
            parsed = float(raw)
        except ValueError as exc:
            raise VerificationError(f"unparseable brightness readback {raw!r} for {cmd!r}") from exc
        if not math.isfinite(parsed) or parsed != int(parsed):
            raise VerificationError(f"non-integer brightness readback {raw!r} for {cmd!r}")
        return int(parsed)

    def get_display_format(self) -> str:
        """``:DISPlay:FORMat?`` -> ``VECTORS`` or ``DOTS``."""
        return self._enum_get(":DISPlay:FORMat?", DISPLAY_FORMATS, "display format")

    def set_display_format(self, fmt: str) -> None:
        """``:DISPlay:FORMat {VECTors|DOTS}``."""
        self._write(f":DISPlay:FORMat {self._enum_set(DISPLAY_FORMATS, fmt, 'display format')}")

    def get_grid_brightness(self) -> int:
        """``:DISPlay:GRID:BRIGhtness?`` (1..100)."""
        return self._get_brightness(":DISPlay:GRID:BRIGhtness?")

    def set_grid_brightness(self, level: int) -> None:
        """``:DISPlay:GRID:BRIGhtness <1..100>``."""
        self._write(f":DISPlay:GRID:BRIGhtness {self._brightness(level)}")

    def get_wave_brightness(self) -> int:
        """``:DISPlay:WAVE:BRIGhtness?`` (1..100)."""
        return self._get_brightness(":DISPlay:WAVE:BRIGhtness?")

    def set_wave_brightness(self, level: int) -> None:
        """``:DISPlay:WAVE:BRIGhtness <1..100>``."""
        self._write(f":DISPlay:WAVE:BRIGhtness {self._brightness(level)}")

    def get_grid_mode(self) -> str:
        """``:DISPlay:GRID:MODE?`` -> FULL, GRID, CROSS or NONE."""
        return self._enum_get(":DISPlay:GRID:MODE?", GRID_MODES, "grid mode")

    def set_grid_mode(self, mode: str) -> None:
        """``:DISPlay:GRID:MODE {FULL|GRID|CROSS|NONE}``."""
        self._write(f":DISPlay:GRID:MODE {self._enum_set(GRID_MODES, mode, 'grid mode')}")

    def get_display_type(self) -> str:
        """``:DISPlay:TYPE?`` -> XY12, XY34 or YT."""
        return self._enum_get(":DISPlay:TYPE?", DISPLAY_TYPES, "display type")

    def set_display_type(self, display_type: str) -> None:
        """``:DISPlay:TYPE {XY12|XY34|YT}``."""
        self._write(f":DISPlay:TYPE {self._enum_set(DISPLAY_TYPES, display_type, 'display type')}")

    # -- screen cursors (documented in the programming manual) -------------

    @staticmethod
    def parse_cursor_source(raw) -> int:
        """Normalize a ``:CURSor:SOURce?`` readback to channel 1..4."""
        text = str(raw).strip().strip('"').upper()
        for prefix in ("CHANNEL", "CHAN", "CH"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        try:
            number = float(text)
        except ValueError as exc:
            raise VerificationError(f"unrecognized cursor source readback {raw!r}") from exc
        if not math.isfinite(number) or number != int(number) or int(number) not in CURSOR_SOURCES:
            raise VerificationError(f"cursor source readback {raw!r} is not CHANnel1..4")
        return int(number)

    @staticmethod
    def validate_cursor_position(cursor_type: str, position: int) -> int:
        """Validate a cursor screen coordinate for TIME or AMPLITUDE mode.

        The manual defines TIME positions as vertical screen columns 0..699
        and AMPLITUDE positions as horizontal screen rows 28..227.  Requiring
        the type here prevents sending a coordinate that is valid in one mode
        but out of range in the other.
        """
        token = MP720115._enum_set(CURSOR_TYPES, cursor_type, "cursor type")
        canonical = CURSOR_TYPES[token.upper()][0]
        if canonical == "CLOSE":
            raise ValidationError("cursor positions cannot be set while cursor type is CLOSE")
        lo, hi = CURSOR_POSITION_RANGES[canonical]
        if isinstance(position, bool) or not isinstance(position, (int, float)):
            raise ValidationError(
                f"{canonical} cursor position must be an integer {lo}..{hi}, got {position!r}"
            )
        if not math.isfinite(position) or position != int(position) or not lo <= int(position) <= hi:
            raise ValidationError(
                f"{canonical} cursor position must be an integer {lo}..{hi}, got {position!r}"
            )
        return int(position)

    def get_cursor_mode(self) -> str:
        """``:CURSor:MODE?`` -> TRACK or INDEPENDENT."""
        return self._enum_get(":CURSor:MODE?", CURSOR_MODES, "cursor mode")

    def set_cursor_mode(self, mode: str) -> None:
        """``:CURSor:MODE {TRACK|INDependent}``."""
        self._write(f":CURSor:MODE {self._enum_set(CURSOR_MODES, mode, 'cursor mode')}")

    def get_cursor_type(self) -> str:
        """``:CURSor:TYPE?`` -> AMPLITUDE, TIME or CLOSE."""
        return self._enum_get(":CURSor:TYPE?", CURSOR_TYPES, "cursor type")

    def set_cursor_type(self, cursor_type: str) -> None:
        """``:CURSor:TYPE {AMPlitude|TIME|CLOSe}``."""
        self._write(f":CURSor:TYPE {self._enum_set(CURSOR_TYPES, cursor_type, 'cursor type')}")

    def get_cursor_source(self) -> int:
        """``:CURSor:SOURce?`` -> channel number 1..4."""
        return self.parse_cursor_source(self._query(":CURSor:SOURce?"))

    def set_cursor_source(self, channel: int) -> None:
        """``:CURSor:SOURce CHANnel<n>`` (n = 1..4)."""
        if isinstance(channel, bool) or not isinstance(channel, int) or channel not in CURSOR_SOURCES:
            raise ValidationError(f"invalid cursor source channel {channel!r}; allowed: {CURSOR_SOURCES}")
        self._write(f":CURSor:SOURce CHANnel{channel}")

    def _get_cursor_position(self, command: str) -> int:
        raw = self._query(command)
        try:
            value = float(raw)
        except ValueError as exc:
            raise VerificationError(f"unparseable cursor position readback {raw!r}") from exc
        if not math.isfinite(value) or value != int(value):
            raise VerificationError(f"non-integer cursor position readback {raw!r}")
        return int(value)

    def get_cursor_a(self) -> int:
        """``:CURSor:CURA?`` -> current screen coordinate."""
        return self._get_cursor_position(":CURSor:CURA?")

    def set_cursor_a(self, position: int, cursor_type: str) -> None:
        """Set cursor A after type-specific coordinate validation."""
        value = self.validate_cursor_position(cursor_type, position)
        self._write(f":CURSor:CURA {value}")

    def get_cursor_b(self) -> int:
        """``:CURSor:CURB?`` -> current screen coordinate."""
        return self._get_cursor_position(":CURSor:CURB?")

    def set_cursor_b(self, position: int, cursor_type: str) -> None:
        """Set cursor B after type-specific coordinate validation."""
        value = self.validate_cursor_position(cursor_type, position)
        self._write(f":CURSor:CURB {value}")

    # -- math / FFT / filter (documented in the manual; not live-verified) ----

    def get_math_mode(self) -> str:
        """``:FUNCtion:MATH:MODE?`` -> MATH, FFT, LOGIC, FILTER or ADVANCE."""
        return self._enum_get(":FUNCtion:MATH:MODE?", MATH_MODES, "math mode")

    def set_math_mode(self, mode: str) -> None:
        """``:FUNCtion:MATH:MODE {MATH|FFT|LOGic|FILTer|ADVance}``."""
        self._write(f":FUNCtion:MATH:MODE {self._enum_set(MATH_MODES, mode, 'math mode')}")

    def get_math_operation(self) -> str:
        """``:FUNCtion:OPERation?``."""
        return self._enum_get(":FUNCtion:OPERation?", MATH_OPERATIONS, "math operation")

    def set_math_operation(self, operation: str) -> None:
        """``:FUNCtion:OPERation {ADD|SUBTract|MULTiply|DIVide|AND|OR|NOT|XOR}``."""
        self._write(
            f":FUNCtion:OPERation {self._enum_set(MATH_OPERATIONS, operation, 'math operation')}"
        )

    @staticmethod
    def _function_source_index(m: int) -> int:
        if isinstance(m, bool) or not isinstance(m, int) or m not in (1, 2):
            raise ValidationError(f"invalid function source slot {m!r}; allowed: (1, 2)")
        return m

    @staticmethod
    def parse_function_source(raw) -> int:
        """Normalize a ``:FUNCtion:SOURce<m>?`` readback to a channel 1..4.

        Accepts ``CHANnel1``/``CHAN1``/``CH1``, ``1``, ``1.0``, ``1.000e000``.
        Anything else (e.g. the manual's 5..9 non-channel codes) raises
        :class:`VerificationError`."""
        text = str(raw).strip().strip('"').upper()
        for prefix in ("CHANNEL", "CHAN", "CH"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        try:
            number = float(text)
        except ValueError as exc:
            raise VerificationError(f"unrecognized function source readback {raw!r}") from exc
        if not math.isfinite(number) or number != int(number) or int(number) not in FUNCTION_SOURCES:
            raise VerificationError(f"function source readback {raw!r} is not CHANnel1..4")
        return int(number)

    def get_function_source(self, m: int) -> int:
        """``:FUNCtion:SOURce<m>?`` (m = 1 or 2) -> channel number 1..4."""
        slot = self._function_source_index(m)
        return self.parse_function_source(self._query(f":FUNCtion:SOURce{slot}?"))

    def set_function_source(self, m: int, channel: int) -> None:
        """``:FUNCtion:SOURce<m> CHANnel<n>`` (m = 1 or 2, n = 1..4)."""
        slot = self._function_source_index(m)
        if isinstance(channel, bool) or not isinstance(channel, int) or channel not in FUNCTION_SOURCES:
            raise ValidationError(f"invalid function source channel {channel!r}; allowed: {FUNCTION_SOURCES}")
        self._write(f":FUNCtion:SOURce{slot} CHANnel{channel}")

    def get_fft_window(self) -> str:
        """``:FUNCtion:FFT:WINDow?``."""
        return self._enum_get(":FUNCtion:FFT:WINDow?", FFT_WINDOWS, "FFT window")

    def set_fft_window(self, window: str) -> None:
        """``:FUNCtion:FFT:WINDow {RECTangular|HANNing|HAMMing|BMAN}``."""
        self._write(f":FUNCtion:FFT:WINDow {self._enum_set(FFT_WINDOWS, window, 'FFT window')}")

    def get_fft_vtype(self) -> str:
        """``:FUNCtion:FFT:VTYPe?`` -> VRMS or DBRMS."""
        return self._enum_get(":FUNCtion:FFT:VTYPe?", FFT_VTYPES, "FFT vertical type")

    def set_fft_vtype(self, vtype: str) -> None:
        """``:FUNCtion:FFT:VTYPe {VRMS|DBRMS}``."""
        self._write(f":FUNCtion:FFT:VTYPe {self._enum_set(FFT_VTYPES, vtype, 'FFT vertical type')}")

    def get_fft_frequency(self) -> float:
        """``:FUNCtion:FFT:FREQuency?`` - read-only centre frequency, Hz."""
        raw = self._query(":FUNCtion:FFT:FREQuency?")
        try:
            return float(raw)
        except ValueError as exc:
            raise VerificationError(f"unparseable FFT frequency readback {raw!r}") from exc

    def get_filter_type(self) -> str:
        """``:FUNCtion:FILTer:TYPE?`` -> LP, HP, BP or BS."""
        return self._enum_get(":FUNCtion:FILTer:TYPE?", FILTER_TYPES, "filter type")

    def set_filter_type(self, filter_type: str) -> None:
        """``:FUNCtion:FILTer:TYPE {LP|HP|BP|BS}``."""
        self._write(f":FUNCtion:FILTer:TYPE {self._enum_set(FILTER_TYPES, filter_type, 'filter type')}")

    def _get_filter_cutoff(self, cmd: str) -> float:
        raw = self._query(cmd)
        try:
            return float(raw)
        except ValueError as exc:
            raise VerificationError(f"unparseable filter cutoff readback {raw!r}") from exc

    def get_filter_high_hz(self) -> float:
        """``:FUNCtion:FILTer:FREQuency:HIGH?`` in Hz."""
        return self._get_filter_cutoff(":FUNCtion:FILTer:FREQuency:HIGH?")

    def set_filter_high_hz(self, hz: float) -> None:
        """``:FUNCtion:FILTer:FREQuency:HIGH <Hz>`` (positive, finite)."""
        value = self._positive_float(hz, "filter high cutoff")
        self._write(f":FUNCtion:FILTer:FREQuency:HIGH {_fmt_num(value)}")

    def get_filter_low_hz(self) -> float:
        """``:FUNCtion:FILTer:FREQuency:LOW?`` in Hz."""
        return self._get_filter_cutoff(":FUNCtion:FILTer:FREQuency:LOW?")

    def set_filter_low_hz(self, hz: float) -> None:
        """``:FUNCtion:FILTer:FREQuency:LOW <Hz>`` (positive, finite)."""
        value = self._positive_float(hz, "filter low cutoff")
        self._write(f":FUNCtion:FILTer:FREQuency:LOW {_fmt_num(value)}")

    # -- screen capture (read-only query, no state change) -----------------------

    def capture_screen_bmp(self) -> bytes:
        """``:DISPlay:DATA?`` - returns the current screen image as a BMP
        file, as an IEEE 488.2 definite-length block. Manual describes
        this as a pure query ("For query the current image data on the
        screen") with no corresponding setter - it is read-only and does
        not alter any instrument state.
        """
        return self._query_raw_block(":DISPlay:DATA?")
