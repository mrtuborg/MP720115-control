"""Configure channel/timebase/edge trigger, capture, then restore settings.

    python examples/configure_and_capture.py --host 192.168.32.132 --channel 1 \
        --scale 0.5 --timebase 1e-3 --level 0 --csv out.csv

All readable settings are snapshotted first and restored in ``finally``, even
when configuration or capture fails. The acquisition mode is never changed
(:ACQuire:MODE cannot be read back on the tested firmware). RUN/STOP state has
no readback query and is not touched. A disabled channel is not enabled; the
capture guard rejects it.
"""

import argparse
import csv
import sys

from mp72_control import MP720115


def _settings(scope, ch):
    """(name, getter, setter) triples for every readable setting we may touch."""
    return [
        (f"ch{ch}.coupling", lambda: scope.get_channel_coupling(ch),
         lambda v: scope.set_channel_coupling(ch, v)),
        (f"ch{ch}.scale", lambda: scope.get_channel_scale(ch),
         lambda v: scope.set_channel_scale(ch, v)),
        (f"ch{ch}.offset", lambda: scope.get_channel_offset(ch),
         lambda v: scope.set_channel_offset(ch, v)),
        (f"ch{ch}.probe", lambda: scope.get_channel_probe(ch),
         lambda v: scope.set_channel_probe(ch, v)),
        (f"ch{ch}.bwlimit", lambda: scope.get_channel_bwlimit(ch),
         lambda v: scope.set_channel_bwlimit(ch, v)),
        ("timebase.mode", scope.get_timebase_mode, scope.set_timebase_mode),
        ("timebase.scale", scope.get_timebase_scale, scope.set_timebase_scale),
        ("timebase.offset", scope.get_timebase_offset, scope.set_timebase_offset),
        ("trigger.source", scope.get_trigger_source, scope.set_trigger_source),
        ("trigger.slope", scope.get_trigger_edge_slope, scope.set_trigger_edge_slope),
        ("trigger.sweep", scope.get_trigger_sweep, scope.set_trigger_sweep),
        ("trigger.coupling", scope.get_trigger_coupling, scope.set_trigger_coupling),
        # Last: other trigger settings can move or clamp the level.
        ("trigger.level", scope.get_trigger_level, scope.set_trigger_level_verified),
    ]


def snapshot(scope, ch):
    """Read every readable setting; unreadable ones are skipped with a warning."""
    saved = {"trigger.mode": None}
    for name, getter, _ in _settings(scope, ch):
        try:
            saved[name] = getter()
        except Exception as exc:  # noqa: BLE001
            print(f"warning: cannot snapshot {name}: {exc}", file=sys.stderr)
    try:
        saved["trigger.mode"] = scope.get_trigger_mode()
    except Exception as exc:  # noqa: BLE001
        print(f"warning: cannot snapshot trigger.mode: {exc}", file=sys.stderr)
    return saved


def restore(scope, ch, saved):
    """Write back changed settings; returns a list of names that failed."""
    failures = []
    if str(saved.get("trigger.mode")).upper() == "EDGE":
        try:
            scope.set_edge_trigger_mode()
        except Exception as exc:  # noqa: BLE001
            failures.append("trigger.mode")
            print(f"warning: cannot restore trigger.mode: {exc}", file=sys.stderr)
    for name, getter, setter in _settings(scope, ch):
        if name not in saved:
            continue
        try:
            current = getter()
        except Exception:  # noqa: BLE001
            current = None
        if current == saved[name]:
            continue
        try:
            setter(saved[name])
        except Exception as exc:  # noqa: BLE001
            failures.append(name)
            print(f"warning: cannot restore {name}: {exc}", file=sys.stderr)
    return failures


def run(scope, args):
    ch = args.channel
    saved = snapshot(scope, ch)
    if str(saved.get("trigger.mode", "")).upper() != "EDGE":
        raise RuntimeError(
            "example requires the current trigger mode to be EDGE; refusing "
            "to change it because this package cannot restore other trigger modes"
        )
    try:
        scope.set_channel_scale(ch, args.scale)
        scope.set_timebase_scale(args.timebase)
        scope.set_trigger_source(f"CHANNEL{ch}")
        scope.set_trigger_edge_slope("POSITIVE")
        scope.set_trigger_level_verified(args.level)
        result = scope.capture_waveform(ch, points=args.points, fmt="BYTE")
        with open(args.csv, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["index", "time_s", "raw_code", "volts_provisional"])
            for i, code in enumerate(result.raw_codes):
                writer.writerow([i, result.times[i], code, result.volts[i]])
        print(f"wrote {len(result.raw_codes)} points to {args.csv} (volts are provisional)")
    finally:
        failures = restore(scope, ch, saved)
        if failures:
            print(f"warning: not restored: {', '.join(failures)}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", required=True, help="oscilloscope IP address or hostname")
    parser.add_argument("--channel", type=int, choices=[1, 2, 3, 4], default=1)
    parser.add_argument("--scale", type=float, default=0.5, help="V/div")
    parser.add_argument("--timebase", type=float, default=1e-3, help="s/div")
    parser.add_argument("--level", type=float, default=0.0, help="trigger level, V")
    parser.add_argument("--points", type=int, default=120)
    parser.add_argument("--csv", default="waveform.csv")
    args = parser.parse_args(argv)

    with MP720115.connect_visa(args.host) as scope:
        return run(scope, args)


if __name__ == "__main__":
    raise SystemExit(main())
