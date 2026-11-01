"""Offline, rule-based squat detector for chest-mounted CindySense recordings.

Requires numpy, scipy, and matplotlib. Times printed by this tool are seconds
since the first CSV sample; they are not wall-clock timestamps.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from enum import Enum
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import gaussian_filter1d, percentile_filter
from scipy.signal import butter, find_peaks, sosfiltfilt


REQUIRED = ("sample_time_seconds", "ax", "ay", "az", "gx", "gy", "gz")
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "plots"


class State(Enum):
    STANDING = "standing"
    DESCENDING = "descending"
    BOTTOM = "bottom"
    ASCENDING = "ascending"
    COMPLETED = "completed"


@dataclass(frozen=True)
class Thresholds:
    lowpass_hz: float = 1.2
    baseline_seconds: float = 8.0
    baseline_window_seconds: float = 10.0
    baseline_percentile: float = 20.0
    orientation: str = "auto"
    gyro_axis: str = "auto"
    gyro_sign: int = 0  # 0 infers the sign from tilt-rate correlation.
    quiet_gyro_deg_s: float = 8.0
    orientation_peak_prominence_deg: float = 8.0
    orientation_excursion_deg: float = 6.0
    min_bout_reps: int = 3
    max_between_reps_seconds: float = 6.0
    bout_peak_rebound_seconds: float = 1.5
    enter_deg: float = 5.0
    bottom_deg: float = 10.0
    exit_deg: float = 4.5
    turn_hysteresis_deg: float = 3.0
    min_descent_seconds: float = 0.20
    min_ascent_seconds: float = 0.20
    min_cycle_seconds: float = 0.70
    max_cycle_seconds: float = 6.0
    cooldown_seconds: float = 0.75
    gyro_evidence_deg_s: float = 5.0
    bout_bottom_grace_deg: float = 1.0
    weak_gyro_fraction: float = 0.6
    strong_tilt_margin_deg: float = 3.0
    min_raw_return_deg: float = 4.0
    max_gap_ms: float = 40.0

    def validate(self, sampling_hz: float) -> None:
        if not 0 < self.lowpass_hz < sampling_hz / 2:
            raise ValueError("lowpass_hz must be between 0 and the Nyquist frequency")
        if min(self.baseline_seconds, self.baseline_window_seconds, self.max_gap_ms,
               self.quiet_gyro_deg_s, self.orientation_peak_prominence_deg,
               self.max_between_reps_seconds, self.bout_peak_rebound_seconds) <= 0:
            raise ValueError("Calibration windows, gap, and motion thresholds must be positive")
        if not 0 < self.baseline_percentile < 50:
            raise ValueError("baseline_percentile must be between 0 and 50")
        if self.orientation not in ("auto", "positive", "negative"):
            raise ValueError("orientation must be auto, positive, or negative")
        if self.gyro_axis not in ("auto", "gx", "gy", "gz") or self.gyro_sign not in (-1, 0, 1):
            raise ValueError("gyro_axis must be auto/gx/gy/gz; gyro_sign must be -1/0/+1")
        if self.orientation_excursion_deg < 0 or self.min_bout_reps < 1:
            raise ValueError("orientation_excursion_deg must be nonnegative; min_bout_reps >= 1")
        if not 0 <= self.exit_deg <= self.enter_deg < self.bottom_deg:
            raise ValueError("Require 0 <= exit_deg <= enter_deg < bottom_deg")
        if self.turn_hysteresis_deg <= 0 or self.gyro_evidence_deg_s < 0:
            raise ValueError("turn_hysteresis_deg must be positive; gyro evidence cannot be negative")
        if not 0 <= self.bout_bottom_grace_deg < self.bottom_deg:
            raise ValueError("bout_bottom_grace_deg must be between 0 and bottom_deg")
        if not 0 < self.weak_gyro_fraction <= 1 or min(
                self.strong_tilt_margin_deg, self.min_raw_return_deg) < 0:
            raise ValueError("Invalid weak-gyro fallback thresholds")
        if min(self.min_descent_seconds, self.min_ascent_seconds,
               self.min_cycle_seconds, self.cooldown_seconds) < 0:
            raise ValueError("Durations and cooldown cannot be negative")
        if self.max_cycle_seconds <= self.min_cycle_seconds:
            raise ValueError("max_cycle_seconds must exceed min_cycle_seconds")


@dataclass(frozen=True)
class Repetition:
    start: float
    bottom: float
    completed: float
    peak_tilt: float
    evidence: str = "standard"


def read_csv(path: Path, max_gap_ms: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    times: list[float] = []
    acceleration: list[list[float]] = []
    gyroscope: list[list[float]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = [column for column in REQUIRED if column not in (reader.fieldnames or ())]
        if missing:
            raise ValueError(f"Missing CSV columns: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=2):
            try:
                values = [float(row[column]) for column in REQUIRED]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid number on row {row_number}: {exc}") from exc
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"Non-finite number on row {row_number}")
            times.append(values[0])
            acceleration.append(values[1:4])
            gyroscope.append(values[4:7])
    if len(times) < 10:
        raise ValueError("At least 10 samples are required")
    elapsed = np.asarray(times) - times[0]
    intervals = np.diff(elapsed)
    if np.any(intervals <= 0):
        index = int(np.flatnonzero(intervals <= 0)[0])
        raise ValueError(f"Timestamps are not strictly increasing at CSV row {index + 3}")
    gaps = np.flatnonzero(intervals > max_gap_ms / 1000 + 1e-9)
    if len(gaps):
        index = int(gaps[0])
        raise ValueError(f"Timestamp gap of {intervals[index]*1000:.1f} ms before CSV row "
                         f"{index + 3}; limit is {max_gap_ms:g} ms")
    sampling_hz = 1 / float(np.median(intervals))
    acceleration_array = np.asarray(acceleration)
    if np.median(np.linalg.norm(acceleration_array[:, :2], axis=1)) < 1e-6:
        raise ValueError("Accelerometer X/Y signal is unusable for torso tilt")
    return elapsed, acceleration_array, np.asarray(gyroscope), sampling_hz


def filtered_signals(
    acceleration: np.ndarray, gyroscope: np.ndarray, sampling_hz: float,
    config: Thresholds,
) -> tuple[np.ndarray, np.ndarray]:
    """Filter sensor noise; keep tilt signed so opposite motions stay distinct."""
    config.validate(sampling_hz)
    sos = butter(2, config.lowpass_hz, btype="low", fs=sampling_hz, output="sos")
    smooth_acc = sosfiltfilt(sos, acceleration, axis=0)
    smooth_gyro = sosfiltfilt(sos, gyroscope, axis=0)
    # Relative rotation in the sensor X/Y gravity plane, not squat depth.
    raw_tilt = np.degrees(np.unwrap(np.arctan2(smooth_acc[:, 0], -smooth_acc[:, 1])))
    return raw_tilt, smooth_gyro


def gyro_direction(
    elapsed: np.ndarray, raw_tilt: np.ndarray, smooth_gyro: np.ndarray,
    config: Thresholds,
) -> tuple[int, int, float]:
    """Find the gyro component and sign most consistent with tilt velocity."""
    tilt_velocity = np.gradient(raw_tilt, elapsed)
    moving = np.abs(tilt_velocity) >= 3.0
    if np.count_nonzero(moving) < 10:
        raise ValueError("Too little angular motion to calibrate the gyroscope")
    correlations = []
    for axis in range(3):
        gyro_values = smooth_gyro[moving, axis]
        if np.std(gyro_values) < 1e-6:
            correlations.append(0.0)
        else:
            correlations.append(float(np.corrcoef(tilt_velocity[moving], gyro_values)[0, 1]))
    axis = int(np.argmax(np.abs(correlations))) if config.gyro_axis == "auto" else (
        "gx", "gy", "gz").index(config.gyro_axis)
    correlation = correlations[axis]
    if config.gyro_sign == 0 and abs(correlation) < 0.25:
        raise ValueError("Gyroscope direction is ambiguous; set --gyro-axis and --gyro-sign")
    sign = config.gyro_sign or (1 if correlation > 0 else -1)
    return axis, sign, correlation


def quiet_reference(elapsed: np.ndarray, raw_tilt: np.ndarray, smooth_gyro: np.ndarray,
                    config: Thresholds) -> float:
    """Find a common early posture across several seconds, not one still instant."""
    early = elapsed < config.baseline_seconds
    if np.count_nonzero(early) < 10:
        raise ValueError("Recording is too short for the calibration window")
    quiet = early & (np.linalg.norm(smooth_gyro, axis=1) < config.quiet_gyro_deg_s)
    samples = raw_tilt[quiet] if np.count_nonzero(quiet) >= 20 else raw_tilt[early]
    edges = np.arange(np.floor(samples.min()) - 1, np.ceil(samples.max()) + 2, 2)
    counts, edges = np.histogram(samples, bins=edges)
    index = int(np.argmax(counts))
    return float((edges[index] + edges[index + 1]) / 2)


def motion_bouts(
    elapsed: np.ndarray, raw_tilt: np.ndarray, reference: float,
    sign: int, sampling_hz: float, config: Thresholds,
) -> list[list[int]]:
    """Find runs of prominent excursions away from the common quiet posture."""
    peaks, _ = find_peaks(sign * raw_tilt,
                          prominence=config.orientation_peak_prominence_deg,
                          distance=max(1, round(1.5 * sampling_hz)))
    peaks = [int(index) for index in peaks
             if sign * (raw_tilt[index] - reference) >= config.orientation_excursion_deg]
    bouts: list[list[int]] = []
    for index in peaks:
        if not bouts or elapsed[index] - elapsed[bouts[-1][-1]] > config.max_between_reps_seconds:
            bouts.append([])
        bouts[-1].append(index)
    return [bout for bout in bouts if len(bout) >= config.min_bout_reps]


def choose_orientation(
    elapsed: np.ndarray, raw_tilt: np.ndarray, reference: float,
    sampling_hz: float, config: Thresholds,
) -> tuple[int | None, list[list[int]]]:
    """Choose the direction with the stronger sustained excursion pattern."""
    candidates = {}
    for sign in (1, -1):
        bouts = motion_bouts(elapsed, raw_tilt, reference, sign, sampling_hz, config)
        strongest = max((float(np.median(sign * (raw_tilt[bout] - reference)))
                         for bout in bouts), default=-math.inf)
        candidates[sign] = (bouts, strongest)
    if config.orientation == "auto":
        sign = max((1, -1), key=lambda candidate: candidates[candidate][1])
        if not candidates[sign][0]:
            return None, []
    else:
        sign = 1 if config.orientation == "positive" else -1
    return sign, candidates[sign][0]


def adaptive_tilt(
    elapsed: np.ndarray, raw_tilt: np.ndarray, sign: int,
    sampling_hz: float, config: Thresholds,
) -> tuple[np.ndarray, np.ndarray]:
    """Use a slow standing envelope; do not assume any initial sample is still."""
    oriented = sign * raw_tilt
    initial = oriented[elapsed < config.baseline_seconds]
    if len(initial) < 10:
        raise ValueError("Recording is too short for the calibration window")
    initial_reference = float(np.percentile(initial, config.baseline_percentile))
    window_samples = max(3, round(config.baseline_window_seconds * sampling_hz))
    rolling = percentile_filter(oriented, percentile=config.baseline_percentile,
                                size=window_samples, mode="nearest")
    rolling = gaussian_filter1d(rolling, sigma=max(1, round(0.4 * sampling_hz)))
    # Freeze the first half of the calibration window, then blend gradually.
    weight = np.clip((elapsed - config.baseline_seconds / 2) /
                     (config.baseline_seconds / 2), 0, 1)
    baseline = (1 - weight) * initial_reference + weight * rolling
    return oriented - baseline, baseline


def keep_consistent_bouts(
    repetitions: list[Repetition], bouts: list[list[int]], elapsed: np.ndarray,
    config: Thresholds | None = None,
) -> tuple[list[Repetition], list[str]]:
    """Match valid cycles to a bout peak, including a brief rebound before standing."""
    config = config or Thresholds()
    peak_times = [float(elapsed[index]) for bout in bouts for index in bout]
    kept, rejected = [], []
    for repetition in repetitions:
        if any(abs(repetition.bottom - peak_time) <= 0.5 or (
            repetition.bottom < peak_time <= repetition.completed
            and peak_time - repetition.bottom <= config.bout_peak_rebound_seconds
        ) for peak_time in peak_times):
            kept.append(repetition)
        else:
            rejected.append(f"{repetition.start:.2f}s: isolated cycle outside a consistent motion bout")
    return kept, rejected


def detect_repetitions(
    elapsed: np.ndarray, tilt: np.ndarray, gyro: np.ndarray, config: Thresholds,
    *, bout_peak_times: tuple[float, ...] = (),
    established_peak_times: tuple[float, ...] = (),
    oriented_raw_tilt: np.ndarray | None = None,
) -> tuple[list[Repetition], list[str]]:
    """Require stand -> descent -> bottom -> ascent -> stand for each rep."""
    if oriented_raw_tilt is not None and len(oriented_raw_tilt) != len(elapsed):
        raise ValueError("oriented_raw_tilt must match the timestamp array")
    state = State.STANDING
    armed = True
    cooldown_until = -math.inf
    start = bottom = peak = peak_time = 0.0
    descent_gyro = ascent_gyro = 0.0
    repetitions: list[Repetition] = []
    rejected: list[str] = []

    for time, angle, rate in zip(elapsed, tilt, gyro, strict=True):
        time, angle, rate = float(time), float(angle), float(rate)
        if not armed and angle <= config.exit_deg:
            armed = True
        if state == State.COMPLETED:
            if time >= cooldown_until and armed:
                state = State.STANDING
            continue
        if state == State.STANDING:
            if armed and time >= cooldown_until and angle >= config.enter_deg:
                state = State.DESCENDING
                armed = False
                start = peak_time = time
                peak = angle
                descent_gyro = max(rate, 0.0)
                ascent_gyro = 0.0
            continue

        if time - start > config.max_cycle_seconds:
            rejected.append(f"{start:.2f}s: movement exceeded {config.max_cycle_seconds:g}s")
            state = State.STANDING
            armed = False
            continue

        if state in (State.DESCENDING, State.BOTTOM) and angle > peak:
            peak, peak_time = angle, time
        if state in (State.DESCENDING, State.BOTTOM):
            descent_gyro = max(descent_gyro, rate)
        if state == State.DESCENDING:
            if angle <= config.exit_deg and peak < config.bottom_deg:
                state = State.STANDING  # Incomplete lean; rearm only at standing.
                armed = True
            elif angle >= config.bottom_deg or (
                angle >= config.bottom_deg - config.bout_bottom_grace_deg
                and any(time <= peak_time <= start + config.max_cycle_seconds
                        for peak_time in bout_peak_times)
            ):
                if time - start < config.min_descent_seconds:
                    continue
                state = State.BOTTOM
                bottom = peak_time
        elif state == State.BOTTOM:
            if angle <= peak - config.turn_hysteresis_deg and rate < 0:
                state = State.ASCENDING
                bottom = peak_time
                ascent_gyro = max(-rate, 0.0)
        elif state == State.ASCENDING:
            ascent_gyro = max(ascent_gyro, -rate)
            if angle <= config.exit_deg:
                reasons = []
                if time - bottom < config.min_ascent_seconds:
                    reasons.append("ascent too short")
                if not config.min_cycle_seconds <= time - start <= config.max_cycle_seconds:
                    reasons.append("cycle duration outside limits")
                raw_return = math.inf
                if oriented_raw_tilt is not None:
                    bottom_index = int(np.searchsorted(elapsed, bottom))
                    current_index = int(np.searchsorted(elapsed, time))
                    raw_return = float(oriented_raw_tilt[bottom_index]
                                       - oriented_raw_tilt[current_index])
                    if raw_return < config.min_raw_return_deg:
                        reasons.append(f"no raw tilt reversal ({raw_return:.1f} deg)")
                gyro_strength = min(descent_gyro, ascent_gyro)
                in_established_bout = any(
                    abs(bottom - peak_time) <= 0.5 for peak_time in established_peak_times)
                weak_gyro_fallback = (
                    oriented_raw_tilt is not None
                    and gyro_strength < config.gyro_evidence_deg_s
                    and gyro_strength >= (
                        config.gyro_evidence_deg_s * config.weak_gyro_fraction)
                    and peak >= config.bottom_deg + config.strong_tilt_margin_deg
                    and raw_return >= config.min_raw_return_deg
                    and in_established_bout
                    and bool(repetitions)
                    and 0 < bottom - repetitions[-1].bottom <= config.max_between_reps_seconds
                )
                if gyro_strength < config.gyro_evidence_deg_s and not weak_gyro_fallback:
                    reasons.append(f"weak directional gyro evidence ({gyro_strength:.2f} deg/s)")
                if reasons:
                    rejected.append(f"{start:.2f}s: {', '.join(reasons)}")
                    state = State.STANDING
                    armed = True
                else:
                    evidence = ("weak-gyro bout fallback" if weak_gyro_fallback else
                                "near-bottom bout" if peak < config.bottom_deg else "standard")
                    repetitions.append(Repetition(start, bottom, time, peak, evidence))
                    cooldown_until = time + config.cooldown_seconds
                    state = State.COMPLETED
                    armed = True  # The completed cycle has already returned to standing.
    if state in (State.DESCENDING, State.BOTTOM, State.ASCENDING):
        rejected.append(f"{start:.2f}s: recording ended before a complete cycle")
    return repetitions, rejected


def plot_detection(
    path: Path, elapsed: np.ndarray, tilt: np.ndarray, gyro: np.ndarray,
    repetitions: list[Repetition], config: Thresholds, orientation: str,
    gyro_axis: str,
) -> None:
    fig, (top, detail, bottom) = plt.subplots(3, 1, figsize=(15, 10),
                                              layout="constrained")
    orientation_known = orientation != "undetermined"
    for axis in (top, detail):
        axis.plot(elapsed, tilt, color="tab:blue", linewidth=1.3,
                  label="Filtered relative tilt" if orientation_known else
                        "Signed sensor tilt from reference")
        if orientation_known:
            axis.axhline(config.enter_deg, color="tab:orange", linestyle="--", linewidth=1,
                         label="Descent threshold")
            axis.axhline(config.bottom_deg, color="tab:red", linestyle="--", linewidth=1,
                         label="Bottom threshold")
            axis.axhline(config.exit_deg, color="tab:green", linestyle=":", linewidth=1,
                         label="Standing threshold")
    bottom.plot(elapsed, gyro, color="tab:purple", linewidth=1,
                label=f"Filtered directional {gyro_axis.upper()}" if orientation_known else
                      "Filtered gyro magnitude")
    bottom.axhline(0, color="gray", linewidth=0.8)
    for index, repetition in enumerate(repetitions, start=1):
        color = "tab:orange" if repetition.evidence != "standard" else "tab:green"
        for axis in (top, detail, bottom):
            axis.axvline(repetition.completed, color=color, alpha=0.8, linewidth=1.1)
        detail.annotate(str(index), (repetition.completed, config.exit_deg), xytext=(3, 8),
                        textcoords="offset points", color=color, fontsize=8)
    top.set(ylabel="Relative torso tilt (deg)" if orientation_known else
                   "Signed sensor tilt (deg)",
            title=f"{path.stem}: {len(repetitions)} complete cycles "
                  f"({orientation} tilt orientation)" if orientation_known else
                  f"{path.stem}: 0 repetitions; no qualifying motion bout")
    top.set_xlim(0, float(elapsed[-1]))
    if repetitions:
        left = max(0.0, repetitions[0].start - 2)
        right = min(float(elapsed[-1]), repetitions[-1].completed
                    + config.max_between_reps_seconds + 2)
        detail.set_xlim(left, right)
        visible = tilt[(elapsed >= left) & (elapsed <= right)]
        detail.set_ylim(float(np.min(visible)) - 3, float(np.max(visible)) + 3)
    else:
        detail.set_xlim(0, float(elapsed[-1]))
    detail.set(ylabel="Relative tilt (deg)" if orientation_known else
                      "Signed sensor tilt (deg)", xlabel="Elapsed time (s)",
               title="Detected bout detail" if orientation_known else
                     "Full recording; no qualifying bout")
    bottom.set(xlabel="Elapsed time (s)",
               ylabel="Directional angular rate (deg/s)" if orientation_known else
                      "Angular rate magnitude (deg/s)")
    bottom.set_xlim(0, float(elapsed[-1]))
    for axis in (top, detail, bottom):
        axis.grid(alpha=0.3)
        axis.legend(loc="upper right")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    defaults = Thresholds()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    for field, value in vars(defaults).items():
        option = "--" + field.replace("_", "-")
        value_type = str if isinstance(value, str) else int if isinstance(value, int) else float
        parser.add_argument(option, type=value_type, default=value)
    args = parser.parse_args()
    config = Thresholds(**{field: getattr(args, field) for field in vars(defaults)})
    try:
        elapsed, acceleration, gyroscope, sampling_hz = read_csv(args.csv_path, config.max_gap_ms)
        raw_tilt, smooth_gyro = filtered_signals(acceleration, gyroscope, sampling_hz, config)
        reference = quiet_reference(elapsed, raw_tilt, smooth_gyro, config)
        tilt_sign, bouts = choose_orientation(elapsed, raw_tilt, reference, sampling_hz, config)
        if tilt_sign is None:
            # No squat orientation is justified. Show sensor-frame signals for diagnosis.
            tilt = raw_tilt - reference
            directional_gyro = np.linalg.norm(smooth_gyro, axis=1)
            repetitions, rejected = [], []
            orientation_label, gyro_axis_label = "undetermined", "magnitude"
        else:
            gyro_axis, gyro_sign, gyro_correlation = gyro_direction(
                elapsed, raw_tilt, smooth_gyro, config)
            tilt, _baseline = adaptive_tilt(elapsed, raw_tilt, tilt_sign, sampling_hz, config)
            directional_gyro = tilt_sign * gyro_sign * smooth_gyro[:, gyro_axis]
            bout_peak_times = tuple(float(elapsed[index]) for bout in bouts for index in bout)
            established_peak_times = tuple(float(elapsed[index]) for bout in bouts
                                           for index in bout[2:])
            repetitions, rejected = detect_repetitions(
                elapsed, tilt, directional_gyro, config,
                bout_peak_times=bout_peak_times,
                established_peak_times=established_peak_times,
                oriented_raw_tilt=tilt_sign * raw_tilt,
            )
            repetitions, isolated = keep_consistent_bouts(repetitions, bouts, elapsed, config)
            rejected.extend(isolated)
            orientation_label = "positive" if tilt_sign > 0 else "negative"
            gyro_axis_label = ("gx", "gy", "gz")[gyro_axis]
        args.output_dir.mkdir(parents=True, exist_ok=True)
        plot_path = args.output_dir / f"{args.csv_path.stem}_squat_detection.png"
        plot_detection(plot_path, elapsed, tilt, directional_gyro, repetitions, config,
                       orientation_label, gyro_axis_label)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    intervals_ms = np.diff(elapsed) * 1000
    short_gaps = int(np.count_nonzero(intervals_ms > 40.0 + 1e-6))
    print(f"Samples: {len(elapsed)}; duration: {elapsed[-1]:.2f}s; "
          f"median interval: {1000/sampling_hz:.2f}ms; "
          f"gaps > 40ms: {short_gaps}; longest interval: {np.max(intervals_ms):.2f}ms")
    if short_gaps:
        print(f"Warning: {short_gaps} short timestamp gaps remain "
              f"(longest {np.max(intervals_ms):.2f}ms); accepted with "
              f"--max-gap-ms {config.max_gap_ms:g}. Samples may be missing.")
    if tilt_sign is None:
        print("Orientation: undetermined; no qualifying motion bout was found. "
              f"Quiet reference: {reference:.2f} deg")
    else:
        print(f"Orientation: {orientation_label} "
              f"({'automatic' if config.orientation == 'auto' else 'manual'}); "
              f"quiet reference: {reference:.2f} deg")
        print(f"Calibration: {config.baseline_percentile:g}th-percentile standing envelope, "
              f"{config.baseline_seconds:g}s initial / {config.baseline_window_seconds:g}s rolling window; "
              f"filter cutoff: {config.lowpass_hz:g} Hz")
        print(f"Gyro support: {gyro_axis_label.upper()}, sign {gyro_sign:+d}, "
              f"tilt-rate correlation {gyro_correlation:.2f}")
        print(f"Thresholds: enter {config.enter_deg:g} deg, bottom {config.bottom_deg:g} deg, "
              f"return {config.exit_deg:g} deg, gyro evidence {config.gyro_evidence_deg_s:g} deg/s; "
              f"cycle {config.min_cycle_seconds:g}-{config.max_cycle_seconds:g}s, "
              f"cooldown {config.cooldown_seconds:g}s, "
              f"bout >= {config.min_bout_reps} cycles")
        print(f"Bout exceptions: bottom grace {config.bout_bottom_grace_deg:g} deg; "
              f"rebound match <= {config.bout_peak_rebound_seconds:g}s; "
              f"weak gyro >= {config.weak_gyro_fraction * config.gyro_evidence_deg_s:.1f} deg/s "
              f"requires peak >= {config.bottom_deg + config.strong_tilt_margin_deg:g} deg, "
              f"raw return >= {config.min_raw_return_deg:g} deg, and recent bout cadence")
    print(f"Detected repetitions: {len(repetitions)}")
    for index, repetition in enumerate(repetitions, start=1):
        print(f"  {index:2d}. start {repetition.start:.2f}s, bottom {repetition.bottom:.2f}s, "
              f"complete {repetition.completed:.2f}s, peak tilt {repetition.peak_tilt:.1f} deg "
              f"[{repetition.evidence}]")
    for item in rejected:
        print(f"Rejected candidate: {item}")
    print(f"Saved graph: {plot_path}")


if __name__ == "__main__":
    main()
