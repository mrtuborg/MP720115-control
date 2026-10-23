---
name: mp72-oscilloscope
description: Expert closed-loop control and measurement workflow for the Multicomp Pro MP720115 over Ethernet/VXI-11, including verified channel/timebase/edge-trigger changes, acquisition design, waveform and screen capture, signal-integrity checks, timing and spectral analysis, causal before/after checks, and evidence-based iterative tuning. Use for oscilloscope operation or analysis of its CSV/NPZ captures; not for USB control or unrelated instruments.
---

# MP720115 expert operator

Operate as a measurement engineer, not as a command relay. Translate the user's signal question into an
acquisition plan, obtain evidence, assess whether the record is technically adequate, and only then report
measurements. If it is inadequate, make a bounded, justified adjustment and recapture.

Known instrument: `192.168.32.132`, identity `UNI-T,MP720115`, firmware `5.00.0.19`. The reference project
is `~/ws/mp72-control`; if copied elsewhere, locate `pyproject.toml` and `src/mp72_control`. Prefer an
installed `mp72-control`; otherwise run `PYTHONPATH=src python3 -m mp72_control` from the project root.

## Load only the expertise needed

- For any configuration, trigger, acquisition, or iterative capture task, read
  [references/acquisition-playbook.md](references/acquisition-playbook.md).
- For waveform interpretation, timing, jitter, rise/fall, spectrum, SNR, SINAD, THD, uncertainty, or
  aliasing questions, read [references/analysis-playbook.md](references/analysis-playbook.md).
- For every state-changing task and every multi-mode demonstration, read
  [references/verification-loop.md](references/verification-loop.md). Verification is part of each action,
  not a final optional audit.
- For the display/FFT/MATH/filter visual screensaver (`examples/visual_screensaver.py`: run, stop, recover
  with `--restore PATH`; documented but not live-verified until the first live test), read
  [references/visual-screensaver.md](references/visual-screensaver.md).
- For measurement-cursor choreography (`examples/cursor_demo.py`: TIME/AMPLITUDE,
  TRACK/INDEPENDENT, verified screen changes and recovery), read
  [references/cursor-demo.md](references/cursor-demo.md).
- For the deliberately fast channel-lamp/front-panel show (`examples/cinematic_show.py`: CH1..CH4,
  trigger/timebase, FFT/MATH/XY/FILTER/cursors, SENT frames), read
  [references/cinematic-show.md](references/cinematic-show.md). Use it only when the user explicitly
  accepts no per-frame readback; never cite SENT frames as verified measurements.
- For repeatable offline analysis, run [scripts/analyze_waveform.py](scripts/analyze_waveform.py) on the
  captured CSV. Use its JSON as evidence, not as a substitute for engineering review.

## Non-negotiable safety and evidence rules

- `status`, `channel-get`, `measure`, `waveform`, and `screen` are observation/capture operations.
  `waveform` temporarily changes `:WAVeform:*` transfer state and restores it.
- Change channel, timebase, acquisition, trigger, RUN, or STOP only when needed by the user's requested
  measurement. Snapshot readable relevant state first; verify readable state afterward.
- Before the first state change, require the ID to match `UNI-T,MP720115`. Stop on a different identity
  unless the user confirms the target.
- Never send arbitrary SCPI, reset, clear, calibration, storage deletion, LAN configuration, auto-setup,
  service, or telnet commands. Never add a raw-SCPI escape hatch.
- Do not change acquisition mode (`acquire --mode`) unless explicitly requested. This firmware times out
  on `:ACQuire:MODE?`; its old value cannot be read or restored.
- `trigger-edge --level` is applied after all other trigger settings and verified by readback; a mismatch
  exits 1 with `trigger level mismatch`. `acquire --average-count` is applied via a temporary AVERAGE
  type and then the requested/previous type; `acquire --memory-depth` is verified and a mismatch exits 1.
  A nonzero exit is a FAIL: do not retry blindly (see firmware facts) and restore only verified fields.
- Never request waveform DATA from a disabled channel. Enable it explicitly when authorized; otherwise
  stop. A disabled-channel DATA query times out and poisons the current session.
- `run` and `stop` have no state readback. A successful write is not proof that acquisition is running,
  stopped, or that a SINGLE event occurred. Use screen/waveform evidence and report the limitation.
- Use at most three autonomous configure-capture-assess iterations. Change one acquisition dimension at a
  time when diagnosing quality. Stop earlier when the record is adequate, a requested limit is reached,
  or a change would exceed the user's scope.

## Mandatory closed-loop verification

Never treat a successful command exit as proof that the intended physical result occurred. For every
state-changing step, execute this loop before continuing:

1. **Predict:** write down the intended state and an observable effect or invariant. Define PASS criteria.
2. **Act:** send one coherent setting change or one intentionally grouped configuration.
3. **Read back:** query every changed field that has readback and normalize aliases/case before comparison.
4. **Observe:** capture a fresh screen and/or waveform when the change should affect displayed or measured
   behavior. Do not reuse pre-change evidence.
5. **Analyze:** run the waveform analyzer and relevant instrument measurements; verify data integrity and
   quality gates before comparing metrics.
6. **Decide:** record `PASS`, `WARN`, `FAIL`, or `INCONCLUSIVE`, with expected versus observed evidence.
   Continue only on PASS or an explicitly acceptable WARN.
7. **Recover:** on FAIL, do not stack more changes. Retry the readback/capture once on a fresh connection;
   if still wrong, restore the last verified state where possible and report the failure.

For settings with no readback, require independent evidence. RUN/STOP and SINGLE trigger need a changed
screen/waveform, fresh timestamps or hashes, and signal-consistent data; a returned command alone is
`INCONCLUSIVE`. Verify restoration with the same rigor as forward changes.

## Measurement workflow

1. Restate the measurand: amplitude, frequency, timing, transient, noise/ripple, rise/fall, jitter, or
   spectrum. Record expected range/bandwidth if known and distinguish exploratory from metrology work.
2. Run read-only discovery:

   ```bash
   mp72-control --host 192.168.32.132 status
   mp72-control --host 192.168.32.132 channel-get 1
   ```

3. Design vertical scale, timebase, record length, acquisition type, coupling, probe factor, and trigger
   from the playbook. State inferred choices, rationale, and explicit acceptance criteria before changing
   the instrument.
4. Apply only those settings and immediately execute the closed-loop verification. Prefer BYTE for the
   first capture because BYTE/CH1 is live-verified. Keep raw CSV evidence even when producing plots.
5. Capture waveform, relevant scalar measurements, and a screen when visual trigger/channel evidence is
   useful. Analyze quality before trusting numbers. Compare observed effects with the predeclared criteria.
6. If inadequate, adjust one dimension and recapture within the iteration bound. Preserve before/after
   records and never continue from an unverified state.
7. Report settings, returned sample count, record duration/rate, quality gates, metrics, uncertainty and
   calibration status, artifacts, verification verdicts, and anything not verified or restored.

## Command patterns

Read-only capture and evidence:

```bash
mp72-control --host 192.168.32.132 measure VPP --channel 1
mp72-control --host 192.168.32.132 waveform \
  --channel 1 --points 120 --format BYTE \
  --csv waveform-ch1.csv --png waveform-ch1.png
mp72-control --host 192.168.32.132 screen scope-screen.bmp
```

Explicit configuration examples; substitute engineered values rather than copying them blindly:

```bash
mp72-control --host 192.168.32.132 channel-set 1 \
  --on --coupling DC --probe 10X --scale 0.5 --offset 0
mp72-control --host 192.168.32.132 timebase \
  --mode MAIN --scale 1e-6 --offset 0
mp72-control --host 192.168.32.132 acquire \
  --type AVERAGE --average-count 16 --memory-depth 7K
mp72-control --host 192.168.32.132 trigger-edge \
  --select --source CHANNEL1 --slope POSITIVE --level 0.2 \
  --sweep NORMAL --coupling DC
```

For configure-capture-restore, prefer `examples/configure_and_capture.py`. It deliberately refuses to
replace a non-EDGE trigger mode because other trigger modes cannot be restored safely by this package.

Run the expert analyzer from the skill directory or by absolute path:

```bash
python3 scripts/analyze_waveform.py waveform-ch1.csv \
  --json waveform-analysis.json --plot waveform-analysis.png
```

Add `--expected-frequency HZ` when a trusted nominal frequency is known. Add `--raw` when voltage
calibration cannot be assumed or raw ADC-code analysis is explicitly required.

## Firmware facts that change conclusions

- Use actual payload length/`returned_points`, never the requested count. The verified firmware returned
  697 BYTE samples for requests of 50, 120, and 500 points.
- Long IEEE 488.2 responses may set VXI-11 END on every 1024-byte chunk. Use the project reader, which
  continues to the declared block length; one `read_raw()` can truncate the result.
- Time is derived from `x_increment`, `x_origin`, and `x_reference` in the preamble.
- Voltage is `(raw - y_reference) * y_increment - y_origin`. Its offset sign was checked live, but absolute
  accuracy was not verified with a calibrated source. Label all converted voltage values `provisional`.
- `FREQ`, `PER`, `RIS`, and `FALL` may return `*` when unavailable. This means unavailable, never zero.
- Firmware 5.00.0.19 live-demo observations (one session, one unit; not a general guarantee):
  - `:ACQuire:AVERages:COUNt` took effect only while TYPE was AVERAGE; set while NORMAL it was ignored.
  - `:ACQuire:MEMory:DEPTh 7K` read back `AUTO` on two attempts (request not applied). In PEAKDETECT an
    AUTO depth read back as `28K`, so a concrete readback for an AUTO request is a WARN, not a failure.
  - `:TRIGger:LEVel 0.8` read back `-11.84 V`, then `+8.16 V` on retry (firmware range limits); the
    cause was not established. Restoring a level may fail; never retry blindly more than once.
  - RUN, STOP, and SINGLE remain unproven: no readback, and the SINGLE freeze was inconclusive.
  - Noise-reduction effects of BW limit/HRES/AVERAGE were inconclusive on a nonstationary floating probe.
- WORD/ASCII and CH2-CH4 capture are documented but were not live-verified in the reference validation.

## Professional reporting contract

Do not merely say that a waveform was captured. State whether it is fit for the requested measurement.
Include:

- identity, transport, channel/probe/coupling, scale/offset, timebase, acquisition, and trigger state;
- requested versus returned samples, duration, median sample interval/rate, and timebase nonuniformity;
- clipping, flatline, aliasing, record-length, cycle-count, and trigger-stability assessments;
- amplitude/timing/spectral metrics with method, usable bandwidth, and uncertainty/limitations;
- explicit `provisional` marking for voltage-derived results until calibrated;
- comparison with instrument measurements when available, including disagreement analysis;
- a verification ledger for every state change: intended effect, readback, observed effect, artifact,
  verdict, and any recovery action;
- paths to raw data, plot, screen, and JSON; and any setting not verified or restored.

Never claim metrology-grade accuracy from this uncalibrated conversion. Never calculate a polished number
from an acquisition that fails the quality gates; explain what must change and, when authorized, recapture.
