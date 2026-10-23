"""
mp72-control - CLI for the Multicomp Pro MP720115 oscilloscope (UNI-T
UPO3000E/UPO2000E SCPI dialect).

No subcommand = read-only `status`. Settings change only through explicit
channel-set / timebase / acquire / trigger-edge / run / stop subcommands with
validated values. There is no arbitrary SCPI subcommand and no reset, clear,
calibration, storage, LAN, auto-setup or service command.

Examples
--------
    mp72-control --host 192.0.2.10 status
    mp72-control --host 192.0.2.10 channel-set 1 --coupling DC --probe 10X --scale 0.5
    mp72-control --host 192.0.2.10 trigger-edge --select --source CHANNEL1 --slope POSITIVE --level 0
    mp72-control --host 192.0.2.10 waveform --channel 1 --csv out.csv
    mp72-control --host 192.0.2.10 screen out.bmp
"""

from __future__ import annotations

import argparse
import csv
import sys
from typing import Optional

from .controller import (
    check_trigger_level,
    ACQUIRE_MODES,
    ACQUIRE_TYPES,
    CHANNEL_COUPLING,
    MEASUREMENTS,
    MEMORY_DEPTHS,
    PROBE_FACTORS,
    TIMEBASE_MODES,
    TRIGGER_COUPLING,
    TRIGGER_SLOPES,
    TRIGGER_SOURCES,
    TRIGGER_SWEEP,
    WAVEFORM_FORMATS,
    MP720115,
)
from .exceptions import MP720115Error


def connect(args) -> MP720115:
    if args.transport == "raw-vxi11":
        return MP720115.connect_raw_vxi11(args.host, timeout_ms=args.timeout_ms)
    return MP720115.connect_visa(args.host, backend=args.visa_backend, timeout_ms=args.timeout_ms)


def cmd_status(scope: MP720115, args) -> int:
    print(f"*IDN?            {scope.idn()}")
    print(f":SYSTem:VERSion? {scope.system_version()}")
    for ch in (1, 2, 3, 4):
        try:
            disp = scope.get_channel_display(ch)
            coup = scope.get_channel_coupling(ch)
            scale = scope.get_channel_scale(ch)
            offset = scope.get_channel_offset(ch)
            probe = scope.get_channel_probe(ch)
            print(f"CH{ch}: display={disp} coupling={coup} scale={scale}V/div offset={offset}V probe={probe}")
        except MP720115Error as exc:
            print(f"CH{ch}: ERROR {exc}")
    try:
        print(f"Timebase: mode={scope.get_timebase_mode()} scale={scope.get_timebase_scale()}s/div "
              f"offset={scope.get_timebase_offset()}s")
    except MP720115Error as exc:
        print(f"Timebase: ERROR {exc}")
    try:
        print(f"Acquire: type={scope.get_acquire_type()} "
              f"mode=<query unsupported on tested firmware> "
              f"memory_depth={scope.get_memory_depth()} "
              f"average_count={scope.get_average_count()}")
    except MP720115Error as exc:
        print(f"Acquire: ERROR {exc}")
    try:
        print(f"Trigger: mode={scope.get_trigger_mode()} source={scope.get_trigger_source()} "
              f"slope={scope.get_trigger_edge_slope()} level={scope.get_trigger_level()}V "
              f"sweep={scope.get_trigger_sweep()} coupling={scope.get_trigger_coupling()}")
    except MP720115Error as exc:
        print(f"Trigger: ERROR {exc}")
    return 0


def cmd_channel_get(scope: MP720115, args) -> int:
    ch = args.channel
    print(f"CH{ch} display={scope.get_channel_display(ch)}")
    print(f"CH{ch} coupling={scope.get_channel_coupling(ch)}")
    print(f"CH{ch} scale={scope.get_channel_scale(ch)} V/div")
    print(f"CH{ch} offset={scope.get_channel_offset(ch)} V")
    print(f"CH{ch} probe={scope.get_channel_probe(ch)}")
    print(f"CH{ch} bwlimit={scope.get_channel_bwlimit(ch)}")
    return 0


def cmd_channel_set(scope: MP720115, args) -> int:
    ch = args.channel
    if args.on is not None:
        scope.set_channel_display(ch, args.on)
    if args.coupling is not None:
        scope.set_channel_coupling(ch, args.coupling)
    if args.scale is not None:
        scope.set_channel_scale(ch, args.scale)
    if args.offset is not None:
        scope.set_channel_offset(ch, args.offset)
    if args.probe is not None:
        scope.set_channel_probe(ch, args.probe)
    if args.bwlimit is not None:
        scope.set_channel_bwlimit(ch, args.bwlimit)
    return cmd_channel_get(scope, args)


def cmd_timebase(scope: MP720115, args) -> int:
    if args.mode is not None:
        scope.set_timebase_mode(args.mode)
    if args.scale is not None:
        scope.set_timebase_scale(args.scale)
    if args.offset is not None:
        scope.set_timebase_offset(args.offset)
    print(f"Timebase mode={scope.get_timebase_mode()} scale={scope.get_timebase_scale()}s/div "
          f"offset={scope.get_timebase_offset()}s")
    return 0


def cmd_acquire(scope: MP720115, args) -> int:
    report = scope.apply_acquisition(
        acq_type=args.type,
        mode=args.mode,
        memory_depth=args.memory_depth,
        average_count=args.average_count,
    )
    mode_status = (
        f"{args.mode} (command sent; query unsupported on tested firmware)"
        if args.mode is not None
        else "<query unsupported on tested firmware>"
    )
    print(f"Acquire type={report.acquire_type} mode={mode_status} "
          f"memory_depth={report.memory_depth} average_count={report.average_count}")
    for warning in report.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return 0


def cmd_trigger_edge(scope: MP720115, args) -> int:
    if args.select:
        scope.set_edge_trigger_mode()
    if args.source is not None:
        scope.set_trigger_source(args.source)
    if args.slope is not None:
        scope.set_trigger_edge_slope(args.slope)
    if args.sweep is not None:
        scope.set_trigger_sweep(args.sweep)
    if args.coupling is not None:
        scope.set_trigger_coupling(args.coupling)
    # Level is applied last: source/coupling changes can move or clamp it.
    if args.level is not None:
        scope.set_trigger_level(args.level)
    level = scope.get_trigger_level()
    print(f"Trigger mode={scope.get_trigger_mode()} source={scope.get_trigger_source()} "
          f"slope={scope.get_trigger_edge_slope()} level={level}V "
          f"sweep={scope.get_trigger_sweep()} coupling={scope.get_trigger_coupling()}")
    if args.level is not None:
        check_trigger_level(args.level, level, args.level_tolerance)
    return 0


def cmd_run(scope: MP720115, args) -> int:
    scope.run()
    print("RUN sent")
    return 0


def cmd_stop(scope: MP720115, args) -> int:
    scope.stop()
    print("STOP sent")
    return 0


def cmd_measure(scope: MP720115, args) -> int:
    value = scope.measure(args.measurement, source=args.channel)
    print(f"{args.measurement}{'' if args.channel is None else f'(CH{args.channel})'} = {value}")
    return 0


def cmd_waveform(scope: MP720115, args) -> int:
    result = scope.capture_waveform(
        args.channel,
        points=args.points,
        fmt=args.format,
        compute_volts=not args.raw_only,
    )
    print(f"source={result.source} format={result.format} requested_points={result.requested_points} "
          f"returned_points={len(result.raw_codes)}")
    print(f"preamble: format={result.preamble.format_name} type={result.preamble.type_name} "
          f"points={result.preamble.points} count={result.preamble.count} "
          f"x_increment={result.preamble.x_increment} x_origin={result.preamble.x_origin} "
          f"y_increment={result.preamble.y_increment} y_origin={result.preamble.y_origin} "
          f"y_reference={result.preamble.y_reference}")
    if result.volts_provisional:
        print("NOTE: voltage scaling is PROVISIONAL (offset sign checked live; "
              "absolute accuracy not checked against a calibrated input).")

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["index", "time_s", "raw_code", "volts_provisional"])
            for i, code in enumerate(result.raw_codes):
                v = result.volts[i] if result.volts else ""
                writer.writerow([i, result.times[i], code, v])
        print(f"wrote {args.csv}")

    if args.npz:
        import numpy as np  # local import: optional dependency

        np.savez(
            args.npz,
            raw_codes=result.raw_codes,
            volts=result.volts if result.volts else [],
            times=result.times,
            x_increment=result.preamble.x_increment,
            x_origin=result.preamble.x_origin,
            y_increment=result.preamble.y_increment,
            y_origin=result.preamble.y_origin,
            y_reference=result.preamble.y_reference,
            volts_provisional=result.volts_provisional,
        )
        print(f"wrote {args.npz}")

    if args.png:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # local import: optional dependency

        fig, ax = plt.subplots()
        if result.volts:
            ax.plot(result.times, result.volts)
            ax.set_ylabel("Volts (provisional)" if result.volts_provisional else "Volts")
        else:
            ax.plot(result.times, result.raw_codes)
            ax.set_ylabel("Raw code")
        ax.set_xlabel("Time (s)")
        ax.set_title(f"{result.source} waveform ({result.format})")
        fig.savefig(args.png)
        print(f"wrote {args.png}")

    return 0


def cmd_screen(scope: MP720115, args) -> int:
    bmp = scope.capture_screen_bmp()
    with open(args.outfile, "wb") as fh:
        fh.write(bmp)
    print(f"wrote {len(bmp)} bytes to {args.outfile}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", required=True, help="Instrument IP address/hostname")
    p.add_argument("--transport", choices=["visa", "raw-vxi11"], default="visa",
                    help="visa = production PyVISA+pyvisa-py path (default); "
                         "raw-vxi11 = dependency-free fallback used when pyvisa "
                         "cannot be installed (see docs/ARCHITECTURE.md)")
    p.add_argument("--visa-backend", default="@py", help="PyVISA backend string (default: @py)")
    p.add_argument("--timeout-ms", type=int, default=5000, help="I/O timeout in milliseconds")

    sub = p.add_subparsers(dest="command")

    sub.add_parser("status", help="Read-only status report (default action)")

    chg = sub.add_parser("channel-get", help="Read-only channel status")
    chg.add_argument("channel", type=int, choices=[1, 2, 3, 4])

    chs = sub.add_parser("channel-set", help="Configure a channel (validated setters)")
    chs.add_argument("channel", type=int, choices=[1, 2, 3, 4])
    on_group = chs.add_mutually_exclusive_group()
    on_group.add_argument("--on", dest="on", action="store_true", default=None)
    on_group.add_argument("--off", dest="on", action="store_false")
    chs.add_argument("--coupling", choices=sorted(CHANNEL_COUPLING))
    chs.add_argument("--scale", type=float, help="Volts/div")
    chs.add_argument("--offset", type=float, help="Volts")
    chs.add_argument("--probe", choices=sorted(PROBE_FACTORS))
    bw_group = chs.add_mutually_exclusive_group()
    bw_group.add_argument("--bwlimit-on", dest="bwlimit", action="store_true", default=None)
    bw_group.add_argument("--bwlimit-off", dest="bwlimit", action="store_false")

    tb = sub.add_parser("timebase", help="Read/configure timebase")
    tb.add_argument("--mode", choices=sorted(TIMEBASE_MODES))
    tb.add_argument("--scale", type=float, help="Seconds/div")
    tb.add_argument("--offset", type=float, help="Seconds")

    acq = sub.add_parser("acquire", help="Read/configure acquisition")
    acq.add_argument("--type", dest="type", choices=sorted(ACQUIRE_TYPES))
    acq.add_argument("--mode", choices=sorted(ACQUIRE_MODES))
    acq.add_argument("--memory-depth", choices=sorted(MEMORY_DEPTHS))
    acq.add_argument("--average-count", type=int)

    trg = sub.add_parser("trigger-edge", help="Read/configure edge trigger")
    trg.add_argument("--select", action="store_true", help="Send :TRIGger:MODE EDGE to select edge trigger")
    trg.add_argument("--source", choices=sorted(TRIGGER_SOURCES))
    trg.add_argument("--slope", choices=sorted(TRIGGER_SLOPES))
    trg.add_argument("--level", type=float,
                     help="Volts; applied last and verified by readback (exit 1 on mismatch)")
    trg.add_argument("--level-tolerance", type=float, default=None,
                     help="Allowed |readback - level| in volts (default max(0.01, 1%% of level))")
    trg.add_argument("--sweep", choices=sorted(TRIGGER_SWEEP))
    trg.add_argument("--coupling", choices=sorted(TRIGGER_COUPLING))

    sub.add_parser("run", help="Start sampling (:RUN)")
    sub.add_parser("stop", help="Stop sampling (:STOP)")

    meas = sub.add_parser("measure", help="Read-only scalar measurement query")
    meas.add_argument("measurement", choices=sorted(MEASUREMENTS))
    meas.add_argument("--channel", type=int, choices=[1, 2, 3, 4], default=None)

    wav = sub.add_parser("waveform", help="Capture waveform data")
    wav.add_argument("--channel", type=int, choices=[1, 2, 3, 4], required=True)
    wav.add_argument("--points", type=int, default=120)
    wav.add_argument("--format", choices=sorted(WAVEFORM_FORMATS), default="BYTE")
    wav.add_argument("--raw-only", action="store_true",
                     help="Do not convert ADC codes to provisional volts")
    wav.add_argument("--csv")
    wav.add_argument("--npz")
    wav.add_argument("--png")

    scr = sub.add_parser("screen", help="Read-only screen capture to BMP file")
    scr.add_argument("outfile")

    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "status"

    try:
        scope = connect(args)
    except MP720115Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        handlers = {
            "status": cmd_status,
            "channel-get": cmd_channel_get,
            "channel-set": cmd_channel_set,
            "timebase": cmd_timebase,
            "acquire": cmd_acquire,
            "trigger-edge": cmd_trigger_edge,
            "run": cmd_run,
            "stop": cmd_stop,
            "measure": cmd_measure,
            "waveform": cmd_waveform,
            "screen": cmd_screen,
        }
        handler = handlers[command]
        return handler(scope, args)
    except MP720115Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        scope.close()


if __name__ == "__main__":
    sys.exit(main())
