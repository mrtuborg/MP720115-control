"""Read-only example: print identity and save one waveform to CSV.

    python examples/basic_capture.py --host 192.168.32.132 --channel 1 --csv out.csv

The channel must already be enabled; a disabled channel is rejected before the
instrument is asked for data. No instrument setting is changed except the
temporary :WAVeform transfer state, which the library restores.
"""

import argparse
import csv

from mp72_control import MP720115


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="oscilloscope IP address or hostname")
    parser.add_argument("--channel", type=int, choices=[1, 2, 3, 4], default=1)
    parser.add_argument("--csv", default="waveform.csv")
    args = parser.parse_args(argv)

    with MP720115.connect_visa(args.host) as scope:
        print(scope.idn())
        result = scope.capture_waveform(args.channel, points=120, fmt="BYTE")

    with open(args.csv, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["index", "time_s", "raw_code", "volts_provisional"])
        for i, code in enumerate(result.raw_codes):
            writer.writerow([i, result.times[i], code, result.volts[i]])
    print(f"wrote {len(result.raw_codes)} points to {args.csv} (volts are provisional)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
