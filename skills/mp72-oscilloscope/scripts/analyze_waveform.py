#!/usr/bin/env python3
"""Deterministic expert screening analysis for mp72-control waveform CSV files.

The tool never contacts or changes an instrument. It validates sampling, computes
amplitude/timing metrics, performs a guarded Hann-window spectrum, emits JSON,
and optionally creates a diagnostic plot. Voltage values from this project are
always labeled provisional because absolute calibration has not been verified.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover - exercised by deployment environment
    raise SystemExit("numpy is required; install the mp72-control 'plot' extra") from exc


ANALYSIS_VERSION = "1.0"


def _finite(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"non-finite value: {value!r}")
    return value


def load_csv(path: Path, force_raw: bool = False):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fields = set(reader.fieldnames or ())
        required = {"time_s", "raw_code"}
        missing = required - fields
        if missing:
            raise ValueError(f"missing CSV columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    if not rows:
        raise ValueError("CSV contains no samples")

    times = np.asarray([_finite(row["time_s"]) for row in rows], dtype=float)
    raw = np.asarray([_finite(row["raw_code"]) for row in rows], dtype=float)

    use_volts = not force_raw and "volts_provisional" in fields
    volts = None
    if use_volts:
        values = [row.get("volts_provisional", "").strip() for row in rows]
        if all(values):
            volts = np.asarray([_finite(value) for value in values], dtype=float)
        else:
            use_volts = False

    signal = volts if use_volts else raw
    return times, raw, signal, ("V" if use_volts else "code"), use_volts


def _safe_ratio_db(numerator: float, denominator: float) -> float | None:
    if numerator <= 0 or denominator <= 0:
        return None
    return 10.0 * math.log10(numerator / denominator)


def _summary(values: np.ndarray) -> dict:
    mean = float(np.mean(values))
    centered = values - mean
    rms = float(np.sqrt(np.mean(values * values)))
    ac_rms = float(np.sqrt(np.mean(centered * centered)))
    peak_ac = float(np.max(np.abs(centered)))
    return {
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "peak_to_peak": float(np.ptp(values)),
        "mean": mean,
        "median": float(np.median(values)),
        "rms_about_zero": rms,
        "ac_rms": ac_rms,
        "standard_deviation": float(np.std(values)),
        "p01": float(np.percentile(values, 1)),
        "p05": float(np.percentile(values, 5)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "robust_p01_p99_span": float(np.percentile(values, 99) - np.percentile(values, 1)),
        "crest_factor_ac": (peak_ac / ac_rms) if ac_rms > 0 else None,
    }


def _interpolated_crossings(t: np.ndarray, y: np.ndarray, level: float, rising: bool) -> np.ndarray:
    if rising:
        indices = np.flatnonzero((y[:-1] < level) & (y[1:] >= level))
    else:
        indices = np.flatnonzero((y[:-1] > level) & (y[1:] <= level))
    out = []
    for index in indices:
        dy = y[index + 1] - y[index]
        if dy == 0:
            continue
        fraction = (level - y[index]) / dy
        out.append(float(t[index] + fraction * (t[index + 1] - t[index])))
    return np.asarray(out, dtype=float)


def _hysteretic_crossings(
    t: np.ndarray, y: np.ndarray, low: float, midpoint: float, high: float, rising: bool
) -> np.ndarray:
    crossings = []
    armed = bool(y[0] <= low) if rising else bool(y[0] >= high)
    for index in range(1, len(y)):
        if rising:
            if y[index] <= low:
                armed = True
            if armed and y[index] >= high:
                start = index - 1
                while start > 0 and y[start] >= midpoint:
                    start -= 1
                dy = y[start + 1] - y[start]
                if dy > 0:
                    fraction = (midpoint - y[start]) / dy
                    crossings.append(float(t[start] + fraction * (t[start + 1] - t[start])))
                armed = False
        else:
            if y[index] >= high:
                armed = True
            if armed and y[index] <= low:
                start = index - 1
                while start > 0 and y[start] <= midpoint:
                    start -= 1
                dy = y[start + 1] - y[start]
                if dy < 0:
                    fraction = (midpoint - y[start]) / dy
                    crossings.append(float(t[start] + fraction * (t[start + 1] - t[start])))
                armed = False
    return np.asarray(crossings, dtype=float)


def _paired_durations(starts: np.ndarray, ends: np.ndarray, next_starts: np.ndarray | None = None):
    durations = []
    for index, start in enumerate(starts):
        limit = next_starts[index + 1] if next_starts is not None and index + 1 < len(next_starts) else math.inf
        candidates = ends[(ends > start) & (ends < limit)]
        if len(candidates):
            durations.append(float(candidates[0] - start))
    return np.asarray(durations, dtype=float)


def edge_analysis(t: np.ndarray, y: np.ndarray) -> tuple[dict, dict]:
    low_level = float(np.percentile(y, 10))
    high_level = float(np.percentile(y, 90))
    amplitude = high_level - low_level
    if amplitude <= max(1e-15, float(np.max(np.abs(y))) * 1e-12):
        return {"available": False, "reason": "insufficient dynamic range"}, {}

    midpoint = (low_level + high_level) / 2.0
    low_hysteresis = low_level + 0.40 * amplitude
    high_hysteresis = low_level + 0.60 * amplitude
    rising = _hysteretic_crossings(t, y, low_hysteresis, midpoint, high_hysteresis, True)
    falling = _hysteretic_crossings(t, y, low_hysteresis, midpoint, high_hysteresis, False)

    periods = np.diff(rising)
    result = {
        "available": bool(len(periods)),
        "robust_low_level": low_level,
        "robust_high_level": high_level,
        "midpoint_level": midpoint,
        "rising_crossings": int(len(rising)),
        "falling_crossings": int(len(falling)),
        "complete_periods": int(len(periods)),
    }
    if len(periods):
        median_period = float(np.median(periods))
        result.update({
            "median_period_s": median_period,
            "edge_frequency_hz": 1.0 / median_period if median_period > 0 else None,
            "mean_period_s": float(np.mean(periods)),
            "period_std_s": float(np.std(periods)),
            "period_peak_to_peak_s": float(np.ptp(periods)) if len(periods) > 1 else 0.0,
            "period_jitter_rms_ppm": (
                float(np.std(periods) / np.mean(periods) * 1e6) if np.mean(periods) > 0 else None
            ),
        })

        high_widths = _paired_durations(rising[:-1], falling, rising)
        pair_count = min(len(high_widths), len(periods))
        if pair_count:
            duties = high_widths[:pair_count] / periods[:pair_count]
            valid = duties[(duties > 0) & (duties < 1)]
            if len(valid):
                result.update({
                    "median_high_width_s": float(np.median(high_widths[:len(valid)])),
                    "median_duty_cycle": float(np.median(valid)),
                    "duty_cycle_std": float(np.std(valid)),
                    "duty_cycles_used": int(len(valid)),
                })

    rise10 = _interpolated_crossings(t, y, low_level + 0.10 * amplitude, True)
    rise90 = _interpolated_crossings(t, y, low_level + 0.90 * amplitude, True)
    fall90 = _interpolated_crossings(t, y, low_level + 0.90 * amplitude, False)
    fall10 = _interpolated_crossings(t, y, low_level + 0.10 * amplitude, False)
    rise_times = _paired_durations(rise10, rise90, rise10)
    fall_times = _paired_durations(fall90, fall10, fall90)
    if len(rise_times):
        result.update({
            "median_rise_time_10_90_s": float(np.median(rise_times)),
            "rise_times_used": int(len(rise_times)),
        })
    if len(fall_times):
        result.update({
            "median_fall_time_90_10_s": float(np.median(fall_times)),
            "fall_times_used": int(len(fall_times)),
        })
    return result, {"rising": rising, "falling": falling, "low": low_level, "high": high_level}


def spectrum_analysis(
    y: np.ndarray, sample_rate: float, expected_frequency: float | None
) -> tuple[dict, dict]:
    count = len(y)
    if count < 32:
        return {"available": False, "reason": "fewer than 32 samples"}, {}
    centered = y - np.mean(y)
    if float(np.ptp(centered)) == 0:
        return {"available": False, "reason": "flat signal"}, {}

    window = np.hanning(count)
    transformed = np.fft.rfft(centered * window)
    frequencies = np.fft.rfftfreq(count, d=1.0 / sample_rate)
    magnitudes = np.abs(transformed)
    power = magnitudes * magnitudes
    power[0] = 0.0
    bin_width = sample_rate / count

    if expected_frequency is not None:
        if not 0 < expected_frequency < sample_rate / 2:
            raise ValueError("expected frequency must be between 0 and Nyquist")
        fundamental_bin = int(round(expected_frequency / bin_width))
    else:
        fundamental_bin = int(np.argmax(power[1:]) + 1)

    fundamental_bin = max(1, min(fundamental_bin, len(power) - 1))
    delta = 0.0
    if 1 <= fundamental_bin < len(power) - 1:
        left, center, right = [math.log(max(float(power[i]), 1e-300))
                               for i in (fundamental_bin - 1, fundamental_bin, fundamental_bin + 1)]
        denominator = left - 2.0 * center + right
        if denominator != 0:
            delta = max(-0.5, min(0.5, 0.5 * (left - right) / denominator))
    peak_frequency = (fundamental_bin + delta) * bin_width
    coherent_amplitude = 2.0 * float(magnitudes[fundamental_bin]) / float(np.sum(window))

    mask_fundamental = np.zeros_like(power, dtype=bool)
    lo = max(1, fundamental_bin - 2)
    hi = min(len(power), fundamental_bin + 3)
    mask_fundamental[lo:hi] = True
    fundamental_power = float(np.sum(power[mask_fundamental]))

    harmonic_mask = np.zeros_like(power, dtype=bool)
    harmonic_bins = []
    for order in range(2, 6):
        harmonic_frequency = peak_frequency * order
        if harmonic_frequency >= sample_rate / 2:
            break
        index = int(round(harmonic_frequency / bin_width))
        h_lo, h_hi = max(1, index - 2), min(len(power), index + 3)
        harmonic_mask[h_lo:h_hi] = True
        harmonic_bins.append({"order": order, "frequency_hz": float(index * bin_width)})

    harmonic_mask &= ~mask_fundamental
    harmonic_power = float(np.sum(power[harmonic_mask]))
    analysis_mask = np.ones_like(power, dtype=bool)
    analysis_mask[0] = False
    noise_mask = analysis_mask & ~mask_fundamental & ~harmonic_mask
    noise_power = float(np.sum(power[noise_mask]))

    spur_power = float(np.max(power[noise_mask])) if np.any(noise_mask) else 0.0
    fundamental_peak_power = float(np.max(power[mask_fundamental]))
    result = {
        "available": True,
        "window": "Hann",
        "sample_rate_hz": sample_rate,
        "nyquist_hz": sample_rate / 2.0,
        "bin_width_hz": bin_width,
        "fundamental_source": "expected-frequency" if expected_frequency is not None else "largest non-DC peak",
        "fundamental_frequency_hz": peak_frequency,
        "fundamental_amplitude_peak_approx": coherent_amplitude,
        "harmonics_included": harmonic_bins,
        "thd_db_estimate": _safe_ratio_db(harmonic_power, fundamental_power),
        "thd_ratio_estimate": math.sqrt(harmonic_power / fundamental_power) if fundamental_power > 0 else None,
        "snr_db_estimate": _safe_ratio_db(fundamental_power, noise_power),
        "sinad_db_estimate": _safe_ratio_db(fundamental_power, noise_power + harmonic_power),
        "sfdr_db_estimate": _safe_ratio_db(fundamental_peak_power, spur_power),
    }
    amplitude_spectrum = 2.0 * magnitudes / float(np.sum(window))
    return result, {"frequency": frequencies, "amplitude": amplitude_spectrum}


def analyze(
    path: Path, force_raw: bool = False, expected_frequency: float | None = None
) -> tuple[dict, dict]:
    t, raw, y, unit, volts_provisional = load_csv(path, force_raw=force_raw)
    if len(t) < 2:
        raise ValueError("at least two samples are required")
    dt = np.diff(t)
    if np.any(dt <= 0):
        raise ValueError("time_s must be strictly increasing")

    median_dt = float(np.median(dt))
    sample_rate = 1.0 / median_dt
    max_dt_deviation = float(np.max(np.abs(dt - median_dt)))
    relative_dt_deviation = max_dt_deviation / median_dt
    signal_summary = _summary(y)
    raw_summary = _summary(raw)
    flatline = signal_summary["peak_to_peak"] <= max(
        1e-15, max(abs(signal_summary["min"]), abs(signal_summary["max"])) * 1e-12
    )

    raw_integral = bool(np.allclose(raw, np.round(raw), atol=1e-9))
    byte_like = raw_integral and float(np.min(raw)) >= 0 and float(np.max(raw)) <= 255
    rail_fraction = float(np.mean((raw <= 0) | (raw >= 255))) if byte_like else None
    clipping = bool(byte_like and rail_fraction and rail_fraction > 0)

    edges, edge_plot = edge_analysis(t, y)
    if relative_dt_deviation <= 0.01:
        spectrum, spectrum_plot = spectrum_analysis(y, sample_rate, expected_frequency)
    else:
        spectrum = {"available": False, "reason": "sample intervals vary by more than 1%"}
        spectrum_plot = {}

    edge_frequency = edges.get("edge_frequency_hz")
    fft_frequency = spectrum.get("fundamental_frequency_hz")
    crosscheck = None
    if edge_frequency and fft_frequency:
        crosscheck = abs(edge_frequency - fft_frequency) / edge_frequency

    selected_frequency = edge_frequency or fft_frequency
    samples_per_cycle = sample_rate / selected_frequency if selected_frequency else None
    cycles_in_record = (t[-1] - t[0]) * selected_frequency if selected_frequency else None

    warnings = []
    recommendations = []
    if volts_provisional:
        warnings.append("voltage conversion is provisional and not absolute-calibrated")
        recommendations.append("use raw ADC codes or validate scaling with a calibrated source before metrology")
    if flatline:
        warnings.append("flat or effectively constant record")
        recommendations.append("increase vertical sensitivity and verify channel, coupling, probe, and signal source")
    if clipping:
        warnings.append("raw BYTE samples touch an ADC rail; amplitude/timing/spectrum may be invalid")
        recommendations.append("increase V/div or adjust vertical offset, then recapture")
    if relative_dt_deviation > 0.01:
        warnings.append("sample intervals vary by more than 1%; uniform-sampling FFT disabled")
        recommendations.append("use edge-domain metrics or resample with a declared method before spectral analysis")
    if samples_per_cycle is not None and samples_per_cycle < 20:
        warnings.append("fewer than 20 samples per estimated cycle")
        recommendations.append("use a faster effective sampling geometry or shorter displayed time span")
    if cycles_in_record is not None and cycles_in_record < 3:
        warnings.append("fewer than 3 estimated cycles in the record")
        recommendations.append("capture a longer time span or deeper record for frequency statistics")
    if crosscheck is not None and crosscheck > 0.02:
        warnings.append("edge and FFT frequency estimates disagree by more than 2%")
        recommendations.append("inspect harmonics, missed/double edges, modulation, trigger stability, and aliasing")
    if fft_frequency and fft_frequency > 0.4 * sample_rate:
        warnings.append("dominant frequency exceeds 40% of sample rate; aliasing risk is high")
        recommendations.append("increase sample rate or limit input bandwidth before interpreting the spectrum")

    adequate = not any((flatline, clipping, relative_dt_deviation > 0.01))
    if selected_frequency:
        adequate = adequate and bool(samples_per_cycle >= 20 and cycles_in_record >= 3)

    report = {
        "analysis_version": ANALYSIS_VERSION,
        "source_file": str(path.resolve()),
        "signal_column": "raw_code" if unit == "code" else "volts_provisional",
        "unit": unit,
        "calibration_status": "provisional" if volts_provisional else "raw ADC code",
        "sampling": {
            "samples": int(len(t)),
            "start_time_s": float(t[0]),
            "end_time_s": float(t[-1]),
            "record_duration_s": float(t[-1] - t[0]),
            "median_dt_s": median_dt,
            "sample_rate_hz": sample_rate,
            "max_dt_deviation_s": max_dt_deviation,
            "dt_jitter_rms_ppm": float(np.std(dt) / median_dt * 1e6),
            "uniform_within_1_percent": bool(relative_dt_deviation <= 0.01),
        },
        "signal": signal_summary,
        "raw_codes": {
            **raw_summary,
            "byte_like": byte_like,
            "adc_rail_fraction": rail_fraction,
            "clipping_detected": clipping,
        },
        "edges": edges,
        "spectrum": spectrum,
        "cross_checks": {
            "edge_vs_fft_frequency_relative_difference": crosscheck,
            "samples_per_estimated_cycle": samples_per_cycle,
            "estimated_cycles_in_record": cycles_in_record,
        },
        "quality": {
            "flatline": flatline,
            "screening_fit_for_quantitative_analysis": adequate,
            "confidence_class": (
                "reject" if any((flatline, clipping, relative_dt_deviation > 0.01))
                else "limited" if warnings else "screening-good"
            ),
            "warnings": warnings,
            "recommendations": recommendations,
        },
    }
    plot_data = {"t": t, "y": y, "unit": unit, "edges": edge_plot, "spectrum": spectrum_plot}
    return report, plot_data


def save_plot(path: Path, plot_data: dict, report: dict) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - deployment dependent
        raise RuntimeError("matplotlib is required for --plot") from exc

    has_spectrum = bool(plot_data["spectrum"])
    rows = 2 if has_spectrum else 1
    fig, axes = plt.subplots(rows, 1, figsize=(11, 7 if has_spectrum else 4.5), constrained_layout=True)
    if rows == 1:
        axes = [axes]
    time_axis = plot_data["t"]
    signal = plot_data["y"]
    axes[0].plot(time_axis, signal, linewidth=1.0)
    axes[0].set_xlabel("Time (s)")
    axes[0].set_ylabel("Volts (provisional)" if plot_data["unit"] == "V" else "Raw ADC code")
    axes[0].grid(True, alpha=0.25)
    edges = plot_data["edges"]
    if edges:
        axes[0].axhline(edges["low"], color="tab:green", alpha=0.5, linestyle="--", label="robust low/high")
        axes[0].axhline(edges["high"], color="tab:green", alpha=0.5, linestyle="--")
        axes[0].legend(loc="best")

    if has_spectrum:
        frequency = plot_data["spectrum"]["frequency"]
        amplitude = plot_data["spectrum"]["amplitude"]
        axes[1].plot(frequency[1:], amplitude[1:], linewidth=1.0)
        axes[1].set_xlabel("Frequency (Hz)")
        axes[1].set_ylabel(f"Amplitude ({plot_data['unit']}, approx peak)")
        axes[1].set_yscale("log")
        axes[1].grid(True, which="both", alpha=0.25)

    quality = report["quality"]
    fig.suptitle(
        f"MP720115 waveform analysis - screening fit: {quality['screening_fit_for_quantitative_analysis']}"
    )
    fig.savefig(path, dpi=160)
    plt.close(fig)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="mp72-control waveform CSV")
    parser.add_argument("--raw", action="store_true", help="analyze raw_code even when provisional volts exist")
    parser.add_argument("--expected-frequency", type=float, help="trusted nominal fundamental frequency, Hz")
    parser.add_argument("--json", type=Path, dest="json_path", help="write JSON report to this path")
    parser.add_argument("--plot", type=Path, help="write diagnostic waveform/spectrum PNG")
    args = parser.parse_args(argv)

    try:
        report, plot_data = analyze(args.csv, force_raw=args.raw, expected_frequency=args.expected_frequency)
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        if args.json_path:
            args.json_path.write_text(encoded + "\n", encoding="utf-8")
        else:
            print(encoded)
        if args.plot:
            save_plot(args.plot, plot_data, report)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"analysis error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
