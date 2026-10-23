# Expert acquisition playbook

Read this before configuring the MP720115 or deciding whether a capture is adequate.

## Start from the measurand

| Goal | Initial acquisition strategy | Quality target |
|---|---|---|
| Amplitude/DC level | DC coupling, NORMAL acquisition, stable edge trigger | Trace uses about 50-80% of vertical range without clipping |
| Frequency/period | Show 5-20 cycles, edge trigger near mid-level | At least 20 samples/cycle; prefer 50+ for timing work |
| Duty/pulse width | Show 5-20 pulses, no averaging initially | Multiple complete high and low intervals, clean crossings |
| Rise/fall time | Fastest useful timebase, short record, NORMAL acquisition | At least 10 samples across the transition; instrument bandwidth materially above edge bandwidth |
| Single transient | SINGLE sweep, sufficient pre-trigger view, no averaging | Event visibly captured once with baseline before and settling after |
| Noise/ripple | AC coupling only if DC removal is intended; bandwidth limit is a measurement decision | Sampling and analog bandwidth cover the wanted noise band; no hidden averaging |
| Repetitive noisy waveform | Establish raw variability first, then AVERAGE with declared count | Preserve an unaveraged reference; averaging must not erase jitter/transients |
| Narrow glitches | PEAKDETECT when appropriate, deep memory, trigger near expected glitch | Glitch survives recapture and is not a one-sample artifact |
| Jitter | NORMAL acquisition, many cycles, no averaging | Stable sample interval, many independent edges, reported cycle count |
| Spectrum/THD | Coherent or long record, stable periodic source, no clipping | Enough cycles and frequency resolution; window and leakage documented |

Do not use auto-setup: it is intentionally unavailable because it changes too many settings without a
reversible contract.

## Vertical system

1. Confirm probe factor physically and in the channel setting. A 10X probe with a 1X setting produces a
   tenfold scale error.
2. Prefer the smallest V/div that keeps the waveform, overshoot, and expected transient inside the screen.
   A useful initial target is 6 vertical divisions peak-to-peak, leaving margin.
3. Use offset to center the signal without hiding excursions. Clipping or codes pinned at the BYTE rails
   invalidates amplitude, edge, and spectral metrics.
4. DC coupling preserves offset and low-frequency content. AC coupling intentionally removes DC and
   distorts content near its high-pass corner; declare that choice. GND is for reference/offset checks, not
   signal measurement.
5. Bandwidth limiting reduces wideband noise but also changes rise time and harmonic content. Use it only
   when the measurement bandwidth is explicitly bounded.

## Horizontal system and record length

- Frequency/period: target 5-20 complete cycles in the record. Too few cycles gives poor statistics and
  FFT resolution; too many can leave too few samples per edge.
- Edge timing: require at least 10 samples across a 10-90% edge, preferably 20+. A scope rise-time result
  is only meaningful when scope/probe bandwidth is comfortably higher than the signal bandwidth.
- Spectrum: frequency resolution is approximately `1 / record_duration`. Choose duration from the needed
  bin spacing, then verify the actual returned duration because this firmware may ignore requested points.
- Nyquist is only a hard mathematical ceiling, not a quality target. For waveform shape prefer at least
  10 samples/cycle; for accurate timing/harmonics use 20-100+ depending on edge content.
- During RUN the reported point count may drift. STOP can improve repeatability for snapshot comparison,
  but RUN/STOP state itself has no readback.

## Acquisition type

- `NORMAL`: first choice for honest waveform, timing, jitter, transient, and spectral inspection.
- `AVERAGE`: improves uncorrelated noise roughly with the square root of the count, but suppresses random
  events and can conceal timing variation. Preserve a NORMAL reference.
- `PEAKDETECT`: useful for narrow glitches at slower timebases; not a substitute for adequate sample rate.
- `ENVELOPE`: useful for variation bounds over repeated acquisitions, not a single-cycle waveform.
- `HRESOLUTION`: may trade bandwidth for vertical resolution. Treat results as filtered unless the exact
  implementation is characterized.
- Acquisition mode REALTIME/EQUIVALENTTIME cannot be queried on this firmware. Do not change it during an
  autonomous workflow.

## Trigger engineering

For a periodic signal, use EDGE trigger on the channel of interest, the slope matching the feature, and a
level near the midpoint between robust low/high levels. Avoid placing the trigger in a noisy plateau.

- `AUTO`: useful for discovery because the display continues without valid triggers; not proof of lock.
- `NORMAL`: preferred once the signal is found; no waveform may update if no valid event occurs.
- `SINGLE`: arm once for a transient. Since SCPI run/stop state lacks readback, confirm with the screen or
  changed waveform and never report firing based only on the `run` return code.
- Trigger coupling changes which components can trigger and must be declared separately from channel
  coupling.

## Closed-loop tuning

Use no more than three autonomous iterations:

1. Capture a conservative record and screen.
2. Assess exactly one dominant deficiency: vertical clipping/utilization, cycles visible, samples per
   feature, trigger stability, noise, record duration, or sample-time nonuniformity.
3. Change one acquisition dimension and recapture. Examples: V/div for clipping, s/div for cycle count,
   trigger level for lock, acquisition type for noise, memory depth for duration/resolution.
4. Stop when the requested measurement passes its quality gates. If improvements conflict, explain the
   tradeoff instead of silently choosing.

Never optimize merely for a visually attractive trace. Optimize for the requested measurement and retain
the evidence needed to reproduce it.

## State management

Before changes, record `status` and `channel-get N`. After capture, either leave the explicitly requested
configuration in place or restore readable settings when the user requested a temporary measurement.
Use `examples/configure_and_capture.py` for its supported EDGE workflow. Report every field that could not
be read, verified, or restored; acquisition mode and RUN/STOP state are known limitations.
