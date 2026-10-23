# mp72-control

A safety-focused Python library and command-line utility for controlling the **Multicomp Pro MP720115** digital oscilloscope over VXI-11/SCPI. The instrument identifies itself as `UNI-T,MP720115` (firmware `5.00.0.19`) and uses the SCPI dialect documented for the UNI-T UPO3000E/UPO2000E family. The exact OEM model has not been proven; the command-family match is strong evidence, not proof.

Official programming reference: [local copy](docs/reference/UNI-T_UPO3000E_UPO2000E_Programming_Manual.pdf) of the *UPO3000E & UPO2000E Series Oscilloscope Programming Manual* by Uni-Trend, with the [official source](https://unitrend.oss-cn-hongkong.aliyuncs.com/uploads/attach/20250624/171046f30ffda67ff849c78002191.pdf).

## Features

- `status`, `channel-get`, `channel-set`, `timebase`, `acquire`, `trigger-edge`, `run`, `stop`, and `measure`;
- `waveform` export to CSV, NPZ, and PNG; `screen` capture to BMP;
- IEEE 488.2 block parser, raw VXI-11 fallback transport, and exact-length block completion through PyVISA;
- protection against waveform requests from disabled channels and restoration of `:WAVeform:*` transfer state;
- **provisional** voltage conversion: `(raw - yref) * yinc - yorigin`.

## Safety

The package has no arbitrary SCPI pass-through and exposes no destructive operations: reset (`*RST`), clear (`*CLS`, `:SYSTem:CLEAr`), calibration, storage operations, LAN configuration, auto-setup (`:AUTO`), service commands, or telnet commands. The instrument address is always explicit through `--host` or the library `host` parameter; there is no CLI default. Every value is validated before transmission.

## Installation

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .            # PyVISA and pyvisa-py
pip install -e ".[plot]"    # numpy and matplotlib for NPZ/PNG
```

See [docs/INSTALLATION.md](docs/INSTALLATION.md) for details.

## Quick start

```bash
mp72-control --host 192.168.32.132 status
python -m mp72_control --host 192.168.32.132 measure VPP --channel 1
mp72-control --host 192.168.32.132 waveform --channel 1 --csv out.csv --png out.png
mp72-control --host 192.168.32.132 screen screen.bmp
```

With no subcommand, the CLI runs the read-only `status` command. Use `--transport raw-vxi11` for the dependency-free fallback transport.

```python
from mp72_control import MP720115

with MP720115.connect_visa("192.168.32.132") as scope:
    print(scope.idn())
    result = scope.capture_waveform(1, points=120)
```

## Documentation

| File | Contents |
|---|---|
| [docs/COMMAND_REFERENCE.md](docs/COMMAND_REFERENCE.md) | CLI and SCPI command reference |
| [docs/INSTALLATION.md](docs/INSTALLATION.md) | installation and startup |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | package design |
| [docs/VALIDATION.md](docs/VALIDATION.md) | manual, live, and firmware validation status |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | diagnostics and recovery |
| [docs/OPERATOR_RUNBOOK.md](docs/OPERATOR_RUNBOOK.md) | correct control workflow and safe recovery |
| [docs/PDF_SOURCE.md](docs/PDF_SOURCE.md) | locally generated guide structure and build instructions |
| [docs/reference/README.md](docs/reference/README.md) | official manual download, checksum, and verification |
| [skills/mp72-oscilloscope/SKILL.md](skills/mp72-oscilloscope/SKILL.md) | expert acquisition, trigger, waveform, DSP, and iterative-tuning skill |
| [skills/mp72-oscilloscope.zip](skills/mp72-oscilloscope.zip) | portable skill archive |

PDFs are intentionally excluded from version control. Download the original vendor manual using
`docs/reference/README.md`, and build the project guide locally with `python tools/build_pdf.py`.

### Verified visual screensaver (`examples/visual_screensaver.py`)

This animation uses documented display and FUNCtion controls: format, brightness, FFT, MATH, XY, and CH1 filters. The active frame set was live-verified on an MP720115 running firmware 5.00.0.19; every run still repeats readback and BMP verification. `GRID:MODE?` does not answer on the tested unit, so grid mode is not changed.

```bash
python examples/visual_screensaver.py --dry-run                      # frame plan only; no connection
python examples/visual_screensaver.py --cycles 1 --dwell 2           # at least 2 s per frame
python examples/visual_screensaver.py --cycles 0                     # infinite; stop with Ctrl-C/SIGTERM
python examples/visual_screensaver.py --retain-screens 3             # optionally keep the latest 3 BMPs
python examples/visual_screensaver.py --restore output/screensaver-.../recovery.json
```

- Before the first write, the ID must begin with `UNI-T,MP720115`. The script snapshots every readable field it may change, CH1/CH2 display state, and the screen, then atomically writes `recovery.json`.
- Frames do not change channel configuration or visibility, timebase, acquisition, trigger, RUN/STOP, waveform transfer, or network state. FUNCtion modes can change memory depth as a firmware side effect, so the script guards and restores that field. There is no screen-clear or arbitrary SCPI command. Frames that require a disabled CH1/CH2 are skipped.
- Every write is checked by readback, and each frame is verified from a fresh screen in memory. BMP files are not retained by default (`--retain-screens 0`), but SHA-256 and pixel-difference ratios remain in `frames.jsonl` and `ledger.md`. A visually unchanged screen with correct readback is WARN. A mismatch or corrupt/empty screen gets one retry, then the run stops and restores.
- Normal completion, Ctrl-C, SIGTERM, and ordinary exceptions restore the baseline. **SIGKILL, power loss, or host failure cannot run cleanup; use `--restore PATH`.** `--no-restore` deliberately leaves the last frame active and prints a prominent warning.
- `--dwell SECONDS` sets the minimum frame duration (default 2 s). Actual duration is longer because of readback and screen verification. With CH1 and CH2 enabled, the tested firmware runs 25 supported frames: at least 50 seconds per cycle with `--dwell 2`, plus VXI-11 overhead.
- `--retain-screens N` keeps the baseline and only the latest N diagnostic frames. `N=0` creates no BMP files; recovery and logs are always retained.
- CH1 filter frames switch LP/HP/BP/BS while preserving existing cutoffs. `--filter-frequency HZ` explicitly enables experimental cutoff writes; a 2000 Hz request read back as 1500 Hz on the tested instrument and correctly stopped the run. These frames are not a Bode plot.

### Cursor demo (`examples/cursor_demo.py`)

This measurement-cursor choreography covers TIME/AMPLITUDE, TRACK/INDEPENDENT, every already-enabled CH1-CH4, wide/medium/narrow intervals, and overlay disappearance/reappearance. It does not change channels, timebase, trigger, acquisition, or RUN/STOP. The order is pseudo-random but reproducible through `--seed`; `--dwell` sets the minimum view duration, and `--cycles 0` repeats indefinitely.

```bash
python examples/cursor_demo.py --dry-run
python examples/cursor_demo.py --cycles 3 --dwell 3 --seed 720115
python examples/cursor_demo.py --restore output/cursors-.../recovery.json
```

Every field is checked by SCPI readback, then the screen is read and compared in memory; BMP files are never saved. The programming manual does not document a SCPI query for the on-screen `delta-T`, `1/delta-T`, or `delta-V` values. The script therefore verifies type, mode, source, coordinates, and pixel changes without inventing a numeric result. Cursor fields are written to `recovery.json` before the first change and restored on completion, Ctrl-C, SIGTERM, or error.

On firmware 5.00.0.19, `MODE?` and `SOURce?` time out while `TYPE=CLOSE`. The demo atomically saves a partial TYPE/A/B baseline, temporarily opens TIME, reads the hidden fields, and returns to CLOSE. In TRACK mode, A/B are coupled in type-dependent and unpredictable ways, so TRACK frames verify mode/source/screen without writing positions. Separated cursors are set only in INDEPENDENT mode.

### Fast cinematic show (`examples/cinematic_show.py`)

This separate front-panel show animates the physical display and button lamps: eight reproducible pseudo-random CH1-CH4 masks followed by blackout/solo/pairs/all, EDGE trigger source/slope/sweep, relative timebase zoom, FFT, MATH, XY, FILTER, and cursors. Unlike measurement demos, frames deliberately omit per-frame readback and BMP capture so they switch near the requested `--dwell`; their verdict is `SENT`, never `PASS`.

```bash
python examples/cinematic_show.py --dry-run
python examples/cinematic_show.py --host 192.168.32.132 --transport raw-vxi11 --cycles 0 --dwell 1
python examples/cinematic_show.py --restore output/cinematic-.../recovery.json
```

Before the first scene, the script checks identity, requires an already-selected EDGE trigger, and saves channel, timebase, trigger, display/FUNCtion/memory-depth, and cursor baselines. Ctrl-C/SIGTERM restores and verifies the baseline. Trigger level and RUN/STOP are never written. SIGKILL or power loss requires `--restore`.

Other examples: `examples/basic_capture.py` and `examples/configure_and_capture.py` (snapshot and restoration in `finally`, with acquisition mode unchanged). Live validation artifacts: `docs/examples/live_waveform_ch1.csv` and `docs/examples/live_screen_test.png`.

## Tests

```bash
python3 -m pytest -q
```

The 164 tests are offline and never connect to the instrument.

## Limitations

`trigger-edge --level` is applied last and verified by readback. `acquire --memory-depth` and `--average-count` are also verified; mismatches return exit code 1. Firmware 5.00.0.19 rejected one trigger level and `7K` depth during live work. Average count is set under a temporary AVERAGE type before returning to the requested type. These are observations from one instrument session; RUN/STOP/SINGLE remain unverified.

The voltage conversion has not been compared with a calibrated source. Firmware can return more points than requested and does not answer `:ACQuire:MODE?`. See [docs/VALIDATION.md](docs/VALIDATION.md).
