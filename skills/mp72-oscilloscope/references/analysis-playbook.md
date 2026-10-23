# Expert waveform analysis playbook

Read this for quantitative interpretation of MP720115 waveform data.

## Quality gates before measurement

Reject or qualify a result when any relevant gate fails:

1. **Integrity:** nonempty samples, finite values, strictly increasing time, no malformed rows.
2. **Sampling:** report median `dt`, sample rate, maximum `dt` deviation, and record duration. Standard FFT
   assumes uniform sampling; resample or avoid FFT when timing variation is material.
3. **Vertical headroom:** detect raw BYTE codes at 0 or 255 and plateaus at the extrema. Clipped records
   invalidate amplitude, rise/fall, THD, and many timing results.
4. **Dynamic range:** a nearly flat record cannot support period or edge metrics. Quantization-dominated
   signals need more vertical sensitivity or averaging appropriate to the goal.
5. **Record support:** require enough complete cycles/events for the requested statistic. Report the actual
   number used.
6. **Aliasing:** components near Nyquist and implausible disagreement between edge and FFT frequencies are
   warnings. A sampled record cannot prove that no out-of-band energy folded into it.
7. **Calibration:** converted voltage is provisional. Raw-code analysis is authoritative for shape and
   relative changes until a calibrated source validates volts/div, probe factor, gain, and offset.

## Amplitude and noise metrics

- `Vpp = max - min` is sensitive to noise and isolated spikes. Also report robust p01-p99 or p05-p95 span.
- Mean estimates DC level; median is more robust to rare spikes.
- RMS about zero includes DC: `sqrt(mean(x^2))`.
- AC RMS removes the mean first: `sqrt(mean((x-mean)^2))`.
- Crest factor is `max(abs(x-mean)) / AC_RMS`; qualify it for clipped or short records.
- For noise, state measurement bandwidth, coupling, acquisition type, averaging, and whether deterministic
  waveform content was removed. RMS without bandwidth is incomplete.
- Averaged acquisitions characterize the averaged waveform, not the original sample-to-sample noise.

## Frequency and period

Use two independent estimators when the data allows:

1. **Hysteretic edge intervals:** derive robust low/high levels (percentiles), arm below a low threshold and
   count a rising crossing above a high threshold. Interpolate the midpoint crossing. Median period is
   robust; report period spread and observed intervals.
2. **Windowed FFT:** remove the mean, apply Hann, find the dominant non-DC peak, and use local parabolic
   interpolation. Report record-limited bin width `df = 1/T` and window.

Agreement within the expected resolution increases confidence. Disagreement can indicate harmonics larger
than the fundamental, subharmonics, missed/double edges, modulation, nonstationarity, or aliasing. Do not
average incompatible estimates into a single unexplained number.

## Duty cycle, pulse width, rise/fall, and jitter

- Use a robust midpoint between low/high plateaus. Pair each rising edge with the following falling edge
  and normalize high time by the corresponding period. Report median and spread across complete cycles.
- Rise time is the interval from 10% to 90% of robust amplitude; fall time is 90% to 10%. Reject transitions
  with too few samples, clipping, ringing that creates ambiguous crossings, or insufficient bandwidth.
- When both measured scope rise time and known instrument-system rise time are trustworthy, a common first
  correction is `tr_signal ~= sqrt(tr_measured^2 - tr_system^2)`. Do not apply it without characterized
  bandwidth/probe response.
- Period jitter statistics require many independent cycles and no averaging. Report sample count, period
  count, mean/median period, standard deviation, peak-to-peak, and the estimator. Separate deterministic
  modulation/drift from random jitter when the record is long enough; otherwise say they are conflated.

## Spectrum, THD, SNR, SINAD, and SFDR

Use spectrum metrics only for a stable, unclipped, uniformly sampled record with enough duration.

- Remove DC unless DC is part of the question. Use a declared window; Hann is a safe general default.
- Frequency resolution is `df = fs/N`; zero-padding interpolates the display but does not improve true
  resolution.
- Prefer coherent sampling for precision amplitude/THD. Without it, integrate the window main lobe around
  each tone instead of reading one bin.
- Identify the fundamental using a trusted expected frequency when supplied; otherwise distinguish the
  lowest fundamental from a stronger harmonic using waveform/edge evidence.
- `THD = sqrt(sum(V_harmonic_rms^2)) / V_fundamental_rms`; state included harmonic order and band.
- `SNR` excludes harmonic distortion from noise; `SINAD` includes noise plus distortion; `SFDR` compares
  the fundamental with the largest spur. Avoid these labels if the implementation cannot separate bins,
  leakage, harmonics, and noise reliably.
- State Nyquist, analysis band, window, bin width, fundamental bins, excluded DC, and whether results are
  lower/upper bounds. Harmonics above Nyquist cannot be measured and may alias.

The bundled analyzer reports estimates based on a Hann spectrum with guarded bins. Treat them as screening
metrics unless coherent acquisition and calibration requirements are met.

## Cross-checks and uncertainty

- Compare computed Vpp/frequency/period/rise/fall with `mp72-control measure`. A returned `*` is unavailable,
  never zero.
- A disagreement larger than sampling resolution and plausible noise demands investigation of channel,
  probe factor, coupling, acquisition type, trigger stability, waveform stationarity, and estimator choice.
- Quantization, sample-time interpolation, finite record length, trigger jitter, probe loading, bandwidth,
  noise, calibration, and algorithm selection all contribute uncertainty. Report the dominant terms rather
  than false precision.
- Limit displayed digits to what the sampling interval, record duration, noise, and calibration support.

## Professional conclusion template

State:

1. whether the capture is fit for the requested measurement;
2. settings and actual sampling geometry;
3. quality-gate results;
4. primary value plus a cross-check and observed variation;
5. calibration and bandwidth limitations;
6. the next configuration change if confidence is insufficient.

Separate observations (what the record contains), inferences (what likely caused it), and recommendations
(what to change). Never present an inference as an instrument readback.
