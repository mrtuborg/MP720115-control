# Correct operation of the MP720115

These rules were verified on one Multicomp Pro MP720115 running firmware `5.00.0.19` over Ethernet/VXI-11. The UNI-T manual remains the syntax authority, but it does not guarantee actual firmware behavior.

## Core control loop

1. Confirm that the instrument identifies as `UNI-T,MP720115...`.
2. Keep exactly one VXI-11 owner.
3. Before writing, read every field that may change. Never change an unreadable field.
4. Apply one coherent setting at a time.
5. Read it back immediately. A successful `write()` does not prove application.
6. Verify the observable result with a waveform, measurement, or screen. The verified screensaver analyzes screens in memory; BMP persistence is optional.
7. Permit at most one retry after mismatch. On a second mismatch, stop and restore the baseline.
8. On completion, Ctrl-C, SIGTERM, or ordinary error, restore and read back the baseline.

## Visual screensaver timing

`examples/visual_screensaver.py` accepts `--dwell SECONDS`, the minimum display time before screen verification. Default is 2.0 seconds. Actual duration is longer because of writes, readback, transport reconnection, and screen transfer.

```bash
python examples/visual_screensaver.py --dwell 5 --cycles 1
python examples/visual_screensaver.py --dwell 0.5 --cycles 0
```

On the tested unit, `:DISPlay:GRID:MODE?` does not answer, so four grid-mode frames are automatically excluded. With CH1 and CH2 enabled, 25 frames remain: 8 display brightness/format, 9 FFT, 3 MATH, 1 XY, and 4 FILTER. With `--dwell 2`, a cycle takes at least 50 seconds plus transport overhead. Channel-dependent frames are omitted when their channel is off.

The same dwell applies to all groups; there are no separate FFT/MATH/XY/FILTER durations.

## Fast channel and button show

`examples/cinematic_show.py` is a visual front-panel show, not a measurement workflow. It switches CH1-CH4 visibility and button lamps, EDGE trigger source/slope/AUTO-NORMAL, relative timebase zoom, FFT/MATH/XY/FILTER, and cursors. Each cycle contains 25 scenes: 8 pseudo-random lamp-chase masks and 17 fixed scenes. `--seed` makes the order reproducible.

```bash
python examples/cinematic_show.py --dry-run
python examples/cinematic_show.py --transport raw-vxi11 --cycles 0 --dwell 1 --seed 720115
```

For speed, scenes omit readback and BMP transfer. The log records `SENT`, never `PASS`. This is an explicit show-only exception, not a measurement protocol. The script still reads and atomically stores everything needed for recovery before the first scene, then verifies restoration on Ctrl-C/SIGTERM. It refuses to start unless the current trigger type is EDGE because other trigger types cannot be restored by the typed API. Trigger level and RUN/STOP are not changed.

`--dwell` is a post-send pause, not a strict total duration. Each scene contains multiple VXI-11 writes. In live smoke testing, `--dwell 0.25` produced roughly 3-5 seconds per scene. Increasing dwell adds the requested pause to command-transfer time.

After SIGKILL or power loss, once the previous VXI-11 owner has definitely exited:

```bash
python examples/cinematic_show.py --restore PATH/TO/recovery.json
```

## Measurement cursors

`examples/cursor_demo.py` shows TIME/AMPLITUDE and TRACK/INDEPENDENT modes on every already-enabled CH1-CH4. `--dwell` sets minimum seconds per view, `--cycles` sets cycle count, and `--seed` controls reproducible ordering.

```bash
python examples/cursor_demo.py --dry-run
python examples/cursor_demo.py --cycles 2 --dwell 4 --seed 720115
```

The script does not change channels, signal path, timebase, trigger, acquisition, or RUN/STOP. Before writing, it requires the identity prefix, reads cursor state, and atomically stores `recovery.json`. Every write is followed by TYPE/MODE/SOURCE/CURA/CURB readback and a fresh screen. TIME coordinates are 0..699; AMPLITUDE coordinates are 28..227. BMP data remains in memory.

The instrument displays `delta-T`, `1/delta-T`, and `delta-V`, but the manual documents no SCPI query for those numbers. Agents must not claim a remotely read numeric result. Evidence consists of cursor readback and pixel difference.

Firmware 5.00.0.19 does not answer MODE?/SOURCE? while TYPE=CLOSE. Correct sequencing is: read TYPE/A/B, write partial recovery, temporarily select TIME, read MODE/SOURCE, return CLOSE, and complete recovery. Never write A/B in TRACK: CURB moves CURA and the mapping differs between TIME and AMPLITUDE. Use INDEPENDENT for separated positions.

## Screen verification without BMP files

Default `--retain-screens 0` still reads every screen, parses its BMP bytes in memory, hashes it, and compares it with the previous frame, but writes no `.bmp` or `.bad` file. `frames.jsonl`, `ledger.md`, and `recovery.json` remain.

```bash
python examples/visual_screensaver.py --retain-screens 3
```

With N > 0, the baseline is retained and only the newest N frame captures remain. File persistence does not affect PASS/WARN/FAIL, which is based on readback and screen bytes.

## VXI-11 firmware behavior

| Observation | Why it matters | Correct action |
|---|---|---|
| A late response remains queued after timeout | the next readback can receive the previous response | stop using that link; close it and open a fresh link |
| `create_link` returns device error 3 | another process or unreleased slot owns the link | stop the owner gracefully, wait for `destroy_link`, then retry once |
| Long `:DISPlay:DATA?` was corrupt after short-query bursts | IEEE block became truncated or shifted | open a fresh link before the long read-only screen transfer |
| END appears on every 1024-byte fragment | ordinary `read_raw()` can return only the beginning | use the declared IEEE 488.2 block length, not END, as completion |
| Disabled-channel `:WAVeform:DATA?` times out | the link becomes unusable | query `:CHANn:DISPlay?` first; never request data from a disabled channel |

After any timeout, do not trust request/response alignment on that link. Transport reconnection does not change instrument settings; it only restores protocol alignment.

## Display and FUNCtion behavior

| Field/mode | Observation on 5.00.0.19 | Project policy |
|---|---|---|
| `:DISPlay:GRID:MODE?` | timeout | mark unreadable and never write it in that run |
| `:DISPlay:TYPE?` | `YT` plus two trailing NUL bytes | strip only transport padding, then normalize |
| FILTER cutoff | high=2000 Hz read back as 1500 Hz | preserve existing cutoffs by default; explicit `--filter-frequency` must pass readback |
| FUNCtion mode | can change memory depth from AUTO to 28K | include memory depth in the baseline guard and restore it |
| Screen hash/diff | proves pixels changed, not physical meaning | combine with state readback; unchanged pixels with correct state are WARN |

## Waveform and acquisition

- `:WAVeform:POINts` can be ignored; use the returned point count.
- During RUN, point count can drift. STOP can improve snapshot repeatability, but RUN/STOP itself has no readback and is not automatically restored.
- Voltage remains `volts_provisional` until compared with a calibrated source.
- `:ACQuire:MODE?` does not answer; mode writes cannot be verified.
- Memory depth and trigger level may reject requested values. A readback mismatch is failure, not success.

## Normal stop and recovery

Stop an infinite demo with Ctrl-C in its terminal. The script handles SIGINT/SIGTERM, restores the baseline, reads it back, then exits.

If a crashed process leaves `recovery.json` with `status: "armed"`:

1. Confirm that the old process is gone and no longer owns VXI-11.
2. Never start multiple restore attempts concurrently.
3. Run `--restore` with the same demo that created the recovery file: `visual_screensaver.py`, `cursor_demo.py`, or `cinematic_show.py`.
4. Success requires `status: "restored"` and `restore_report.ok: true`.

`create_link` error 3 during restore is not a reason to retry immediately; first find the active owner. SIGKILL prevents in-process cleanup and is a last resort.

## Intentionally unavailable

The project has no arbitrary SCPI, `*RST`, `*CLS`, `:AUTO`, screen clear, calibration, storage, or LAN-setting operations. Verified screensavers never change a setting that cannot be read and restored.
