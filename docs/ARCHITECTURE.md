# Architecture

```text
src/mp72_control/
|-- __init__.py                exports MP720115, exceptions, and parsers
|-- __main__.py                python -m mp72_control
|-- cli.py                     argparse interface and mp72-control entry point
|-- controller.py              MP720115 validation, commands, and capture logic
|-- scpi_blocks.py             pure IEEE 488.2 block and preamble parsing
|-- vxi11_raw_transport.py     socket-based VXI-11 fallback transport
`-- exceptions.py              package exception hierarchy
```

## Readback verification

`VerificationError` means that a command was sent but the instrument did not apply the requested value. The CLI prints `ERROR:` and exits with code 1.

- `apply_acquisition(acq_type, mode, memory_depth, average_count)` applies settings in dependency order. The instrument accepts average count only while type is AVERAGE, so the method temporarily selects AVERAGE, writes and reads the count, then selects the requested or previous type. Memory depth is applied last. Final type, count, and depth are read back; a mismatch raises `VerificationError`. An AUTO request that reads back as a concrete depth becomes an `AcquisitionReport.warnings` entry instead of an error. `:ACQuire:MODE` has no usable readback and is not verified. Inputs are validated before the first write.
- `set_trigger_level_verified(volts, tolerance=None)` writes the level, reads it, and calls `check_trigger_level`. The default tolerance is max(0.01 V, 1%). `trigger-edge` applies level after the other trigger settings. The lower-level `set_trigger_level` remains unverified for backward compatibility.
- These behaviors characterize one firmware 5.00.0.19 session. They do not verify RUN, STOP, or SINGLE state.

## Transport

`MP720115` requires only `write`, `query`, `read_raw`, `read_bytes` for block continuation, `timeout`, and `close` from its resource. Any compatible PyVISA `MessageBasedResource` or `VXI11RawTransport` can be used. Constructors are `connect_visa(host, ...)` and `connect_raw_vxi11(host, ...)`; `host` is mandatory.

## IEEE 488.2 blocks

`scpi_blocks.py` parses definite-length `#<n><length><data>` blocks and rejects indefinite blocks, nonnumeric fields, and truncation. `peek_declared_length` and `header_total_len` derive the expected size from a partial header. The `:WAVeform:PREamble?` response has 10 fields: format, type, points, count, xinc, xorig, xref, yinc, yorig, and yref.

## Block completion firmware behavior

The firmware sets VXI-11 END on every 1024-byte fragment, so pyvisa-py `read_raw()` can return only the first part of a long block. `_query_raw_block` parses the header, calculates the complete size, and calls `read_bytes(remaining, chunk_size=1024, break_on_termchar=False)`. A transport without `read_bytes` raises `TransportError`. Once the fallback transport recognizes a header, it also trusts the declared length instead of END.

## Waveform capture

1. Validate format and point count before contacting the instrument.
2. Query `:CHANn:DISPlay?`. A disabled channel raises `ValidationError` before transfer state changes because `:WAVeform:DATA?` times out on disabled channels.
3. Read the previous `SOURce`, `FORMat`, and `POINts` values.
4. Write the requested values, read the preamble, and transfer data.
5. Restore `POINts`, `FORMat`, and `SOURce` in every outcome. After I/O failure, a transport with `reconnect()` receives one retry on a fresh link.

Voltage is `(raw - yref) * yinc - yorigin` and remains marked `volts_provisional`. Time is derived from `x_origin` and `x_increment`.

## Safety by construction

The public API contains only explicitly implemented commands. Enumerations use allowlists, and numeric values must be finite and positive where required. Tests scan source for prohibited commands and ensure every write belongs to the intended command surface.

## Display, FUNCtion, cursors, and demos

`controller.py` provides table-driven `set_`/`get_` methods for `:DISPlay:FORMat/GRID:*/WAVE:BRIGhtness/TYPE` and `:FUNCtion:MATH:MODE/OPERation/SOURce1,2/FFT:*/FILTer:*`. Values use enumerated tables, brightness is integer 1..100, and filter frequencies are positive finite values. Readbacks normalize to canonical names; source spellings such as `1`, `1.0`, and `CHAN1` normalize to integer 1..4. Screen clear and `:FUNCtion:EXPRession` are absent by design.

The typed cursor API covers MODE, TYPE, SOURCE, CURA, and CURB. A coordinate is always validated with its type: TIME accepts 0..699, AMPLITUDE accepts 28..227, and positions under CLOSE are rejected before transmission.

`examples/visual_screensaver.py` uses only typed methods and `capture_screen_bmp`:

1. Check the `UNI-T,MP720115` identity prefix, snapshot CH1/CH2 display and every readable field, capture a baseline screen in memory, and atomically create `recovery.json` before the first write. Raw VXI-11 isolates every display/FUNCtion baseline query on a fresh link because one timeout can otherwise shift subsequent responses. An unreadable field is never changed.
2. For each frame: write, read back and compare, dwell, parse a fresh BMP response in memory, and read back again. Raw VXI-11 opens a fresh transport link before the long screen transfer because firmware 5.00.0.19 sometimes corrupted it after short queries. The old link is destroyed first to avoid exhausting link slots. Baseline and frame capture each receive at most one retry. Persistent mismatch or corrupt/empty screen stops the run and restores.
3. Restore in dependency groups under temporary FUNCtion modes: FILTER type and cutoffs, FFT window/vtype, MATH sources/operation, then math mode, memory depth, and display fields. A complete readback pass gets one corrective write. Cleanup runs on completion, SIGINT, SIGTERM, KeyboardInterrupt, and ordinary exceptions while signals are blocked.

`examples/cursor_demo.py` applies the same fail-closed lifecycle to cursor fields. When the baseline type is CLOSE, it restores coordinates under temporary TIME and puts CLOSE back last. BMP validity, SHA-256, and pixel difference are computed in memory without writing screen files.

`examples/cinematic_show.py` deliberately omits per-frame queries for a faster physical show. It snapshots all readable state before the first scene, logs each scene as `SENT`, and verifies only final restoration. It never writes trigger level or RUN/STOP.

SIGKILL or power loss requires the matching `--restore PATH` after the previous VXI-11 owner has exited. `create_link` device error 3 indicates an occupied link or slot, not permission to start concurrent restore attempts.

Screen bytes are always analyzed in memory in verified demos. `--retain-screens 0` creates no `.bmp` or `.bad` files. With N > 0, rotation removes only exact frame paths created by that process inside `output_dir`; the baseline, recovery JSON, JSONL, and ledger remain.

## Examples

`configure_and_capture.py` snapshots every readable supported field, changes channel scale, timebase, and EDGE trigger, captures a waveform, and restores changed values in `finally`. It does not touch `:ACQuire:MODE`, and it neither reads nor changes RUN/STOP state.
