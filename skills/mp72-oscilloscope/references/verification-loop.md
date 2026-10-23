# Closed-loop verification protocol

Read this before changing the MP720115 or running a multi-stage demonstration. The purpose is to prove
that each command produced the intended instrument state and observable measurement effect before the next
change hides or compounds an error.

## Verification ledger

Maintain a chronological Markdown or JSON ledger with one record per coherent action:

| Field | Required content |
|---|---|
| Step | Stable ID and human-readable name |
| Baseline | Pre-change readback plus screen/waveform artifact |
| Intent | Exact fields to change and why |
| Prediction | Quantitative or visual effect expected |
| Acceptance | PASS thresholds chosen before action |
| Command | Exact allowlisted CLI invocation |
| Readback | Normalized observed settings |
| Evidence | Fresh screen, waveform, analyzer JSON, and measurements |
| Verdict | PASS, WARN, FAIL, or INCONCLUSIVE |
| Recovery | Retry, correction, restoration, or stop |

Do not retroactively loosen acceptance criteria to make a failed step pass.

## Four independent layers

1. **Transport:** command exit code, complete file creation, nonzero size, parseable IEEE block/CSV/BMP.
2. **State:** changed settings equal readback after normalizing accepted abbreviations, case, numeric formats,
   and floating-point representation.
3. **Signal:** a fresh waveform/screen exhibits the predicted causal effect without failing quality gates.
4. **Restoration:** restored settings equal the baseline, and a fresh capture is consistent with baseline
   within expected signal variability.

A state readback can PASS while the physical signal check FAILS. Keep those verdicts separate.

## Evidence freshness

- Use unique filenames or timestamps for every stage. Never overwrite the only baseline.
- Confirm output modification time and nonzero size. For screens, record a cryptographic hash; identical
  hashes may be valid for a setting that does not visibly change the trace, but they are not proof of a
  visible effect.
- For waveforms, compare actual sample count, preamble geometry, time range, raw-code summary, analyzer
  verdict, and selected signal metrics. Do not compare only PNG appearance.
- Repeat stochastic noise captures at least three times when claiming that averaging, bandwidth limiting,
  HRESOLUTION, or PEAKDETECT changed noise. Compare medians and ranges, not one lucky record.

## Setting-specific checks

### Channel visibility, scale, offset, coupling, and bandwidth

- PASS state only when `channel-get` returns the intended values.
- Enabling a channel additionally requires a fresh screen or successful guarded waveform capture.
- A V/div adjustment intended to remove clipping must reduce rail occupancy to zero and place robust
  p01-p99 span in a useful fraction of the ADC/display range. Readback alone is insufficient.
- Offset should move baseline/mean in the predicted direction without clipping. Coupling and bandwidth
  changes need waveform/spectrum evidence; do not infer their effect only from the menu state.

### Timebase and memory

- Verify timebase readback and actual preamble-derived `dt`, duration, and returned sample count.
- A wider time span should increase observed duration/cycle count; a close-up should increase samples per
  selected feature when the device sampling geometry permits it.
- This firmware may ignore requested waveform point count. Never fail a step merely because returned
  points differ; judge actual geometry against the measurement goal.

### Acquisition type and averaging

- Verify TYPE, average count, and memory-depth readback. `acquire` does this itself: it sets the count
  under AVERAGE (the firmware ignores it otherwise), returns to the requested type, applies memory depth
  last, and exits 1 on any TYPE/count/depth mismatch. An AUTO depth request that reads back concrete
  (seen: `28K` in PEAKDETECT on 5.00.0.19) prints a WARNING and exits 0; record it as WARN.
- A requested non-AUTO depth that reads back differently (seen: `7K` reading `AUTO`) is FAIL. Keep the
  verified readback value.
- NORMAL is the reference. For noise comparisons acquire at least three records per mode.
- AVERAGE/HRESOLUTION are expected to reduce uncorrelated AC RMS or high-frequency spectral energy, but
  random variation can violate strict monotonicity. PASS on a meaningful median change with preserved CH1
  frequency/amplitude; otherwise WARN or INCONCLUSIVE.
- PEAKDETECT should preserve or increase observed extrema/glitch visibility relative to NORMAL. If it does
  not, report that no resolvable peak effect was observed rather than calling the command ineffective.
- Never interpret acquisition mode effects from CH2 noise unless vertical scale, coupling, bandwidth,
  timebase, and trigger reference were held constant.

### Edge trigger

- Verify mode, source, slope, level, sweep, and coupling readback. `trigger-edge --level` applies the
  level last and exits 1 on mismatch (default tolerance max(10 mV, 1 %); `--level-tolerance V` widens it).
  On 5.00.0.19 a 0.8 V request read back -11.84 V and then +8.16 V; treat that as FAIL, retry at most
  once, and report the final readback instead of claiming restoration.
- For a stable periodic CH1 signal, take multiple fresh captures. Trigger lock evidence includes a
  consistent selected edge location/phase and stable frequency; a static screenshot alone is weak evidence.
- Changing POSITIVE to NEGATIVE should change the selected edge phase while preserving frequency and
  amplitude within expected variation.
- AUTO/NORMAL changes can legitimately produce the same trace when triggering is already stable. Judge
  state via readback and do not invent a visual difference.

### RUN, STOP, and SINGLE

These lack direct state readback. A zero exit code proves only that the command was sent.

- For RUN, compare successive fresh captures or screens. Data evolution supports running acquisition;
  identical data is INCONCLUSIVE for a perfectly repetitive signal.
- For STOP, compare hashes/data across time. Stability supports a frozen state but cannot prove it for a
  perfectly repetitive input.
- For SINGLE, require a plausible event capture plus fresh evidence. If evidence cannot distinguish armed,
  triggered, or already-stopped states, mark INCONCLUSIVE and do not claim success.

## Causal comparison rules

- Change one acquisition dimension at a time unless settings form one inseparable configuration.
- Hold unrelated settings constant and verify that they stayed constant.
- Compare like with like: same channel, coupling, scale, timebase, trigger and analysis band.
- Use absolute tolerances based on sample interval/ADC step and relative tolerances based on signal
  variability. State the tolerance before comparing.
- Preserve raw evidence for both sides and use analyzer JSON for deterministic metric extraction.
- Correlation is not causation when the input itself changes. For CH2 floating noise, use repeated captures
  and qualify conclusions as observed under current electromagnetic conditions.

## Verdict and stopping logic

- `PASS`: state matches and all required observable criteria pass.
- `WARN`: state matches, core goal is usable, but a noncritical quality limitation remains.
- `FAIL`: state mismatch, corrupted/missing evidence, failed quality gate, or observed effect contradicts a
  required criterion.
- `INCONCLUSIVE`: the instrument exposes no decisive readback/effect or stochastic evidence is insufficient.

On FAIL, retry the readback/capture once on a fresh connection without adding new changes. If failure
persists, restore the last verified configuration when possible and stop that branch. On INCONCLUSIVE,
report honestly and continue only if the user's broader task remains safe and the uncertainty cannot
invalidate later conclusions.

## Final audit

Before reporting completion:

1. Check every planned action has a ledger verdict and linked artifacts.
2. Ensure every numerical conclusion came from a capture that passed relevant quality gates.
3. Verify restored readable settings against the saved baseline.
4. List unverified state explicitly, especially RUN/STOP and acquisition mode.
5. Distinguish command delivery, state readback, and physical-effect proof in the final report.
