# Fast cinematic front-panel show

Use `examples/cinematic_show.py` only for a user-requested visual show where fast CH1..CH4 lamp changes
matter more than per-frame proof. This is an explicit exception to the measurement verification loop:
frames contain no readback or BMP capture and are logged as `SENT`, never `PASS`.

## Run

```bash
python examples/cinematic_show.py --dry-run
python examples/cinematic_show.py --host 192.168.32.132 --transport raw-vxi11 \
  --cycles 0 --dwell 1 --seed 720115
python examples/cinematic_show.py --restore output/cinematic-.../recovery.json
```

One cycle has 25 scenes: 8 reproducible pseudo-random nonempty channel masks, then blackout, CH1..CH4
solo/pair/all-channel patterns, relative timebase zoom, EDGE trigger source/slope/AUTO-NORMAL, FFT,
MATH, XY12, LP/HP filter views and TIME/AMPLITUDE cursors. `--dwell` is close to wall-clock scene time
relative to the verified demos because frames do not query the instrument. It is still a post-send pause,
not a hard total duration: live-smoke with `--dwell 0.25` took about 3–5 seconds per scene because each
scene contains several sequential VXI-11 writes.

## Safety and evidence boundary

- Require the exact MP720115 identity prefix and an existing EDGE trigger before changes. Refuse other
  trigger types because the typed project API cannot restore them.
- Snapshot channel visibility, timebase, EDGE trigger, display/FUNCtion/memory-depth and the complete
  cursor baseline before the first show frame. Preserve the partial cursor-recovery ordering required
  when `TYPE=CLOSE`.
- Do not write trigger level, acquisition type/mode, RUN/STOP, waveform transfer, storage, calibration,
  network or arbitrary SCPI.
- A frame line such as `[0001] ...: SENT` proves command delivery only. Do not infer a physical result,
  trigger lock, measurement accuracy, or successful LED state from it.
- Ctrl-C/SIGTERM, normal completion and ordinary errors restore the baseline with readback. Success is
  `status=restored` and `restore_report.ok=true`. SIGKILL/power loss requires `--restore PATH` after the
  original VXI-11 owner has ended.
- Never run another demo or restore concurrently. If speed is no longer required or results will be
  interpreted as measurements, use the verified visual/cursor workflow instead.
- Live-smoke `mp72-cinematic-live-smoke-20261023-01`: 25 SENT frames, exit 0, and verified restoration
  (`cursor` 5/5, `visual` 14/14, `controls` 13/13; overall `restore_report.ok=true`).
