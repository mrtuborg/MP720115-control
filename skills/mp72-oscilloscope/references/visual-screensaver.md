# Visual screensaver (display / FUNCtion styling)

STATUS: the active frame set is documented and was live-verified on MP720115 firmware 5.00.0.19:
25/25 PASS across display/FFT/MATH/XY/FILTER, followed by verified baseline and memory-depth restore.
Every run must still perform its own readback and fresh in-memory screen checks.

## Run

```bash
python examples/visual_screensaver.py --dry-run                 # frame plan only, never connects
python examples/visual_screensaver.py --cycles 1 --dwell 2      # default host 192.168.32.132
python examples/visual_screensaver.py --cycles 0                # infinite until stopped
python examples/visual_screensaver.py --cycles 1 --dwell 5      # minimum 5 s per frame
python examples/visual_screensaver.py --retain-screens 3        # optional diagnostic BMP files
python examples/visual_screensaver.py --filter-frequency 1000   # enable CH1 filter frames explicitly
```

Options: `--host --transport {visa,raw-vxi11} --timeout-ms --cycles (0=infinite) --dwell --output-dir
--retain-screens N --filter-frequency HZ --no-restore --restore PATH --dry-run`. Output directory:
`recovery.json`, `frames.jsonl` and `ledger.md`. `--dwell` is the minimum time per frame; actual time is
slightly longer because of write/readback, reconnect and screen verification. With CH1/CH2 enabled the
tested firmware runs 25 supported frames (about 50 seconds plus transport overhead at the default
`--dwell 2`). `--retain-screens 0` is the default: screen bytes are parsed, hashed and compared in memory
without `.bmp/.bad` files. With N > 0 the baseline and only the newest N frame captures are kept.

## Stop

Ctrl-C (SIGINT) or `kill -TERM <pid>`. Both, plus ordinary exceptions and normal completion, restore the
baseline in dependency order and verify it by readback. Never use `kill -9`.

## Recover

SIGKILL, power loss or a crashed host process cannot be handled in-process. Run:

```bash
python examples/visual_screensaver.py --restore <output-dir>/recovery.json
```

`recovery.json` is written atomically before the first change and holds the baseline. `--restore`
requires the instrument ID to equal the recorded one, exits 3 when any field is not restored, and updates
the file's `status` (`armed`, `restored`, `restore_failed`, `not_restored`).
Before a separate restore, prove the original owner is no longer running. `create_link` device error 3
usually means an active owner or occupied link slot; do not start parallel restore attempts. Stop the
owner gracefully, wait for link destruction, then retry once.

## Rules for agents

- Never replace this with raw SCPI. Only the allowlisted controller methods are used; there is no
  display-clear and no `:FUNCtion:EXPRession` (no documented readback/restoration contract).
- Never change channels, visibility, timebase, acquisition, trigger, RUN/STOP, waveform transfer or
  network from the screensaver. CH2-dependent frames are skipped when CH2 is off (same for CH1).
- Frames never write acquisition controls, but guard and restore memory depth because FUNCtion modes
  changed it from AUTO to 28K as a firmware side effect on the tested unit.
- `--no-restore` only when the user explicitly asks; it prints a loud warning and leaves the style.
- Command return is never proof: each setting is read back immediately (aliases normalised, e.g. source
  `1`/`CHAN1`), a fresh screen is decoded and compared in memory, and PASS/WARN/FAIL is recorded. One retry on a mismatch or a
  corrupt/missing screen, then stop and restore. Unchanged screen with correct readback is WARN.
- With raw VXI-11, use the script's fresh-link screen path. Firmware 5.00.0.19 was observed corrupting a
  long display response after bursts of short queries; reconnecting the transport before capture fixed
  the read-only transfer without changing instrument state. A timeout poisons request/response alignment:
  never continue interpreting responses from that link; destroy it before reconnecting.
- Treat unreadable baseline fields as unavailable. On the tested firmware `:DISPlay:GRID:MODE?` timed
  out, so the script never writes grid mode; trailing NUL padding such as `YT\x00\x00` is normalized.
- Screen hash/difference ratio shows pixels changed, not physical meaning. FILTER frames are not a Bode
  plot. By default they switch LP/HP/BP/BS while preserving existing cutoffs. `--filter-frequency`
  explicitly requests low = 0.5 f and high = 2 f, but is experimental: a live 2000 Hz request read back
  as 1500 Hz and correctly stopped the run.
- A FAIL or an unexpected readback in the first live run is a finding to report, not something to retry
  blindly.
