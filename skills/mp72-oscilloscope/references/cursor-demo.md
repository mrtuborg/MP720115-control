# Measurement cursor demo

Use `examples/cursor_demo.py` for a front-panel-only cursor show. It writes only documented
`:CURSor:MODE/TYPE/SOURce/CURA/CURB`; it never changes channel visibility or signal configuration,
timebase, trigger, acquisition, RUN/STOP, waveform transfer, storage, calibration, or network state.

## Run

```bash
python examples/cursor_demo.py --dry-run
python examples/cursor_demo.py --cycles 1 --dwell 2 --seed 720115
python examples/cursor_demo.py --cycles 0 --dwell 5
```

`--dwell` is the minimum time per view. `--cycles 0` repeats until Ctrl-C/SIGTERM. `--seed` makes the
per-cycle pseudo-random order reproducible. Frames are planned for every already-enabled CH1..CH4 across
TIME, AMPLITUDE, TRACK, INDEPENDENT, several separations, and cursor blackout/reappearance. Disabled
channels are never enabled and receive no frames.

## Verification contract

- Require ID prefix `UNI-T,MP720115` before the first write.
- On firmware 5.00.0.19 MODE?/SOURCE? time out while TYPE=CLOSE. Read TYPE/A/B first and atomically write
  a partial recovery before the first change; temporarily open TIME, read MODE/SOURCE on fresh links,
  restore CLOSE, then atomically complete the baseline. If activation fails, restore/verify the partial
  baseline without opening cursors again when it already matches.
- For every frame: write a field, immediately read it back, dwell, capture a fresh screen in memory,
  parse the BMP, hash it, compare pixels with the preceding frame, then read all requested fields again.
- Correct state with negligible pixel change is WARN. A mismatch or screen failure gets one retry, then
  stop and restore. Do not stack another change on a failed frame.
- No BMP is persisted. `frames.jsonl`, `ledger.md`, and `recovery.json` are the evidence.
- Restore and verify the complete baseline on normal completion, exception, Ctrl-C or SIGTERM. SIGKILL,
  power loss or a dead host requires `--restore PATH`; prove the old owner has released VXI-11 first.

## Coordinate and measurement semantics

- TIME uses vertical cursor columns 0..699.
- AMPLITUDE uses horizontal cursor rows 28..227.
- Always pass the cursor type into `set_cursor_a/b`; never send a coordinate validated for another type.
- In TRACK, do not write A/B. CURB moves CURA and the mapping is type-dependent (live observations:
  TIME requested A=300/B=400 ended with A=400; AMPLITUDE A=B=128 ended with A=29). TRACK frames verify
  TYPE/MODE/SOURCE and pixels only; separated cursor positions belong to INDEPENDENT.
- The manual documents position/state queries, but no SCPI query for the numeric result displayed by the
  panel (`delta-T`, `1/delta-T`, `delta-V`). Never invent or claim a remote numeric cursor measurement.
  Report the verified type/mode/source/positions and pixel change; the operator may read the panel value.

## Recover

```bash
python examples/cursor_demo.py --restore <output-dir>/recovery.json
```

Recovery requires exact instrument ID equality. When the baseline TYPE is CLOSE, the implementation
temporarily uses TIME to restore hidden A/B coordinates and restores CLOSE last. Success means
`status=restored` and `restore_report.ok=true`, not merely exit from the script.
