# Command reference

Global options before the subcommand are `--host HOST` (**required**), `--transport {visa,raw-vxi11}` (default `visa`), `--visa-backend` (default `@py`), and `--timeout-ms` (default 5000). Run with `mp72-control ...` or `python -m mp72_control ...`. With no subcommand, the CLI runs `status`.

Channels are numbered 1-4. Enumeration values are case-insensitive.

| Subcommand | Options | SCPI | Mutates instrument |
|---|---|---|---|
| `status` | - | `*IDN?`, `:SYSTem:VERSion?`, channel/timebase/acquisition/trigger queries | no |
| `channel-get N` | - | `:CHANn:DISPlay?`, `COUPling?`, `SCALe?`, `OFFSet?`, `PROBe?`, `BWLimit?` | no |
| `channel-set N` | `--on/--off`, `--coupling DC\|AC\|GND`, `--scale V/div`, `--offset V`, `--probe 0.001X...1000X`, `--bwlimit-on/--bwlimit-off` | corresponding `:CHANn:*` setters | yes |
| `timebase` | `--mode MAIN\|WINDOW`, `--scale s/div`, `--offset s` | `:TIMebase:MODE/SCALe/OFFSet` | yes |
| `acquire` | `--type NORMAL\|AVERAGE\|PEAKDETECT\|ENVELOPE\|HRESOLUTION`, `--mode REALTIME\|EQUIVALENTTIME`, `--memory-depth AUTO\|700\|7K\|70K\|700K\|7M\|70M`, `--average-count 2...8192` (power of two). Count is set under temporary AVERAGE, requested/previous type follows, and depth is last. TYPE/count/depth mismatch returns 1; AUTO reading as a concrete value produces WARNING and returns 0. | `:ACQuire:TYPE/MODE/MEMory:DEPTh/AVERages:COUNt` | yes |
| `trigger-edge` | `--select`, `--source CHANNEL1..4\|EXT\|EXT5\|ACLINE`, `--slope POSITIVE\|NEGATIVE\|ALTERNATION`, `--level V` (last and verified; mismatch returns 1), `--level-tolerance V` (default max(0.01 V, 1%)), `--sweep AUTO\|NORMAL\|SINGLE`, `--coupling DC\|AC\|LF\|HF\|NOISE` | `:TRIGger:MODE EDGE`, `SOURce`, `EDGE:SLOPe`, `LEVel`, `SWEep`, `COUPling` | yes |
| `run` | - | `:RUN` | yes |
| `stop` | - | `:STOP` | yes |
| `measure NAME [--channel N]` | VPP, VMAX, VMIN, VAMPLITUDE, VTOP, VBASE, VMIDDLE, VAVERAGE, VRMS, FREQUENCY, PERIOD, RISETIME, FALLTIME, OVERSHOOT | `:MEASure:<X>? [CHANn]` | no |
| `waveform` | required `--channel N`, `--points` (120), `--format BYTE\|WORD\|ASCII`, `--raw-only`, `--csv F`, `--npz F`, `--png F` | `:WAVeform:SOURce/FORMat/POINts`, `PREamble?`, `DATA?` | temporarily changes and restores `:WAVeform:*` |
| `screen FILE` | BMP path | `:DISPlay:DATA?` | no |

`acquire --mode` sends the command, but the mode cannot be read back on this firmware. See [VALIDATION.md](VALIDATION.md).

## Display, FUNCtion, and cursor library methods

These are Python `MP720115` methods without CLI subcommands. Values are case-insensitive and accept full or documented short forms. Readback normalizes aliases such as `VECT`/`VECTors` to `VECTORS`. Unknown readbacks raise `VerificationError`.

| Set/get method | SCPI | Values |
|---|---|---|
| `set_display_format` / `get_display_format` | `:DISPlay:FORMat` | VECTors, DOTS |
| `set_grid_brightness` / `get_grid_brightness` | `:DISPlay:GRID:BRIGhtness` | integer 1..100 |
| `set_wave_brightness` / `get_wave_brightness` | `:DISPlay:WAVE:BRIGhtness` | integer 1..100 |
| `set_grid_mode` / `get_grid_mode` | `:DISPlay:GRID:MODE` | FULL, GRID, CROSS, NONE |
| `set_display_type` / `get_display_type` | `:DISPlay:TYPE` | XY12, XY34, YT |
| `set_math_mode` / `get_math_mode` | `:FUNCtion:MATH:MODE` | MATH, FFT, LOGic, FILTer, ADVance |
| `set_math_operation` / `get_math_operation` | `:FUNCtion:OPERation` | ADD, SUBTract, MULTiply, DIVide, AND, OR, NOT, XOR |
| `set_function_source(m, ch)` / `get_function_source(m)` | `:FUNCtion:SOURce1/2` | m = 1 or 2; channel 1..4. `1`, `1.0`, and `CHAN1` normalize to an integer; other sources raise `VerificationError`. |
| `set_fft_window` / `get_fft_window` | `:FUNCtion:FFT:WINDow` | RECTangular, HANNing, HAMMing, BMAN |
| `set_fft_vtype` / `get_fft_vtype` | `:FUNCtion:FFT:VTYPe` | VRMS, DBRMS |
| `get_fft_frequency` | `:FUNCtion:FFT:FREQuency?` | read-only, Hz |
| `set_filter_type` / `get_filter_type` | `:FUNCtion:FILTer:TYPE` | LP, HP, BP, BS |
| `set_filter_high_hz` / `get_filter_high_hz` | `:FUNCtion:FILTer:FREQuency:HIGH` | positive finite Hz |
| `set_filter_low_hz` / `get_filter_low_hz` | `:FUNCtion:FILTer:FREQuency:LOW` | positive finite Hz |
| `set_cursor_mode` / `get_cursor_mode` | `:CURSor:MODE` | TRACK, INDependent |
| `set_cursor_type` / `get_cursor_type` | `:CURSor:TYPE` | AMPlitude, TIME, CLOSe |
| `set_cursor_source` / `get_cursor_source` | `:CURSor:SOURce` | channel 1..4; readback normalizes to integer |
| `set_cursor_a(position, type)` / `get_cursor_a` | `:CURSor:CURA` | TIME integer 0..699; AMPLITUDE integer 28..227 |
| `set_cursor_b(position, type)` / `get_cursor_b` | `:CURSor:CURB` | TIME integer 0..699; AMPLITUDE integer 28..227 |

Intentionally absent: screen clear (`:DISPlay:CLEar`) and `:FUNCtion:EXPRession`, which lacks a documented readback/restoration contract.

### `examples/visual_screensaver.py`

Options: `--host` (192.168.32.132), `--transport {visa,raw-vxi11}`, `--timeout-ms` (5000), `--cycles N` (0 infinite; default 1), `--dwell SECONDS` (2), `--output-dir`, `--retain-screens N` (default 0), `--filter-frequency HZ` (experimental cutoff writes), `--no-restore`, `--restore PATH`, and `--dry-run`. Required outputs are `recovery.json`, `frames.jsonl`, and `ledger.md`. `NNNN-<frame>.bmp` exists only when N > 0. Exit codes: 0 without FAIL; 1 error/FAIL with restoration; 2 identity or recovery error; 3 incomplete restoration; 130/143 SIGINT/SIGTERM after restoration. SIGKILL requires `--restore PATH`.

### `examples/cursor_demo.py`

Options: `--host`, `--transport`, `--timeout-ms`, `--cycles N`, `--dwell SECONDS`, `--seed N`, `--output-dir`, `--no-restore`, `--restore PATH`, and `--dry-run`. It writes only the five allowlisted `:CURSor:*` fields and checks every write plus a fresh screen in memory. It always keeps `recovery.json`, `frames.jsonl`, and `ledger.md`. The instrument has no documented numeric SCPI readback for `delta-T`, `1/delta-T`, or `delta-V`.

### `examples/cinematic_show.py`

Options: `--host`, `--transport`, `--timeout-ms`, `--cycles N`, `--dwell SECONDS` (1.0), `--seed N`, `--output-dir`, `--restore PATH`, and `--dry-run`. It changes channel display, timebase scale, EDGE trigger source/slope/sweep, display/FUNCtion, and cursor state. Frames omit readback/BMP and are logged as `SENT`; startup baseline and final restoration remain verified. It never changes trigger level, acquisition type/mode, RUN/STOP, waveform transfer, storage, calibration, or LAN. The starting trigger type must be EDGE.

## Output formats

- CSV: `index,time_s,raw_code,volts_provisional`; voltage is provisional.
- NPZ: `raw_codes`, `volts`, `times`, `x_increment`, `x_origin`, `y_increment`, `y_origin`, `y_reference`, and `volts_provisional`.
- PNG: Matplotlib plot using the Agg backend.
- BMP: raw 800x480 instrument screen.

## Exit codes

`0` success; `1` library validation or transport error; `2` connection failure; argparse also returns `2` for invalid arguments.

## Intentionally unavailable

There are no subcommands for arbitrary SCPI, `*RST`, `*CLS`, `:SYSTem:CLEAr`, `:SYSTem:CAL`, `:SYSTem:COMMunicate:LAN:*`, `:AUTO`, `:SYSTem:SETup`, service operations, or telnet commands. `test_forbidden_or_raw_subcommands_rejected` verifies parser refusal.
