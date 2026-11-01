"""Causal, read-only squat detection for sequential chest-mounted MPU6050 samples.

Input samples use the collector's CSV names (sample_time_seconds, ax..gz).
process_sample and process_batch return only newly completed repetition events.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import hashlib
import math
from statistics import median, pstdev
from typing import Iterable, Mapping


AXES = ("gx", "gy", "gz")


def _parse_values(sample: Mapping[str, object]) -> tuple[float, list[float], list[float], float | None]:
    try:
        t = float(sample["sample_time_seconds"])
        acc = [float(sample[key]) for key in ("ax", "ay", "az")]
        gyro = [float(sample[key]) for key in AXES]
        device_t = (float(sample["sample_t"]) if sample.get("sample_t") not in
                    (None, "") else None)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid sensor sample: {exc}") from exc
    if not all(math.isfinite(value) for value in (t, *acc, *gyro)) or (
        device_t is not None and not math.isfinite(device_t)
    ):
        raise ValueError("Non-finite sensor sample")
    if math.hypot(acc[0], acc[1]) < 1e-6:
        raise ValueError("Accelerometer X/Y signal is unusable")
    return t, acc, gyro, device_t


@dataclass(frozen=True)
class StreamingConfig:
    sample_hz: float = 50.0
    lowpass_hz: float = 1.2
    warmup_seconds: float = 1.0
    max_warmup_seconds: float = 8.0
    quiet_gyro_deg_s: float = 8.0
    min_quiet_samples: int = 20
    orientation: str = "auto"  # auto, positive, negative
    gyro_axis: str = "auto"
    gyro_sign: int = 0
    enter_deg: float = 5.0
    bottom_deg: float = 10.0
    exit_deg: float = 4.5
    turn_hysteresis_deg: float = 3.0
    min_descent_seconds: float = 0.2
    min_ascent_seconds: float = 0.4
    min_cycle_seconds: float = 0.7
    max_cycle_seconds: float = 6.0
    cooldown_seconds: float = 0.75
    gyro_evidence_deg_s: float = 5.0
    min_raw_return_deg: float = 4.0
    max_gap_ms: float = 40.0
    reset_gap_seconds: float = 1.0
    baseline_adaptation_hz: float = 0.02
    calibration_mode: str = "adaptive"  # adaptive or initial_standing
    standing_calibration_seconds: float = 2.0
    standing_angle_std_deg: float = 2.0

    def __post_init__(self) -> None:
        if self.orientation not in ("auto", "positive", "negative"):
            raise ValueError("orientation must be auto, positive or negative")
        if self.gyro_axis not in ("auto", *AXES) or self.gyro_sign not in (-1, 0, 1):
            raise ValueError("Invalid gyroscope axis or sign")
        if self.calibration_mode not in ("adaptive", "initial_standing"):
            raise ValueError("calibration_mode must be adaptive or initial_standing")
        if not 0 < self.lowpass_hz < self.sample_hz / 2:
            raise ValueError("Invalid low-pass cutoff")
        if not 0 <= self.exit_deg <= self.enter_deg < self.bottom_deg:
            raise ValueError("Invalid tilt thresholds")
        if min(self.warmup_seconds, self.max_warmup_seconds, self.quiet_gyro_deg_s,
               self.turn_hysteresis_deg, self.max_cycle_seconds, self.max_gap_ms,
               self.reset_gap_seconds, self.standing_calibration_seconds,
               self.standing_angle_std_deg) <= 0 or self.min_quiet_samples < 1:
            raise ValueError("Calibration and timing values must be positive")
        if self.max_warmup_seconds < self.warmup_seconds or (
            self.max_cycle_seconds <= self.min_cycle_seconds
        ):
            raise ValueError("Invalid warm-up or cycle duration")
        if min(self.min_descent_seconds, self.min_ascent_seconds,
               self.min_cycle_seconds, self.cooldown_seconds,
               self.gyro_evidence_deg_s, self.min_raw_return_deg,
               self.baseline_adaptation_hz) < 0:
            raise ValueError("Durations and evidence thresholds cannot be negative")


@dataclass(frozen=True)
class RepetitionEvent:
    session_id: str
    repetition: int
    start_time_seconds: float
    bottom_time_seconds: float
    completed_time_seconds: float
    peak_tilt_deg: float
    raw_return_deg: float
    descent_gyro_deg_s: float
    ascent_gyro_deg_s: float
    orientation: str
    gyro_axis: str
    gyro_sign: int
    bottom_to_confirmation_seconds: float
    cycle_duration_seconds: float
    descent_duration_seconds: float
    ascent_duration_seconds: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Cycle:
    phase: str = "standing"
    armed: bool = False
    start: float = 0.0
    bottom: float = 0.0
    peak: float = 0.0
    peak_raw: float = 0.0
    peak_time: float = 0.0
    descent_max: list[list[float]] = field(
        default_factory=lambda: [[0.0, 0.0] for _ in AXES])
    ascent_max: list[list[float]] = field(
        default_factory=lambda: [[0.0, 0.0] for _ in AXES])
    correlation_products: list[float] = field(default_factory=lambda: [0.0] * 3)
    gyro_energy: list[float] = field(default_factory=lambda: [0.0] * 3)
    tilt_energy: float = 0.0

    def clear(self) -> None:
        self.phase = "standing"
        self.armed = False
        self.descent_max = [[0.0, 0.0] for _ in AXES]
        self.ascent_max = [[0.0, 0.0] for _ in AXES]
        self.correlation_products = [0.0] * 3
        self.gyro_energy = [0.0] * 3
        self.tilt_energy = 0.0


class StreamingSquatDetector:
    """One session per instance; no file, network, database, or future-sample access."""

    def __init__(self, session_id: str, config: StreamingConfig | None = None) -> None:
        self.config = config or StreamingConfig()
        self.reset_session(session_id)

    def reset_session(self, session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id is required")
        self.session_id = session_id
        self.count = 0
        self.calibration = "warming_up"
        self.calibration_note = "Waiting for observed quiet samples"
        self.reference: float | None = None
        self._calibration_sample_count = 0
        self._calibration_angle_std: float | None = None
        self._best_standing_angles: list[float] = []
        self.orientation: int | None = (
            None if self.config.orientation == "auto" else
            1 if self.config.orientation == "positive" else -1)
        self._first_time: float | None = None
        self._last_time: float | None = None
        self._last_device_time: float | None = None
        self._raw_angle: float | None = None
        self._filtered_acc: list[float] | None = None
        self._filtered_gyro: list[float] | None = None
        self._quiet: deque[float] = deque(maxlen=round(self.config.sample_hz * 8))
        self._standing: deque[tuple[float, float]] = deque(
            maxlen=round(self.config.sample_hz * self.config.standing_calibration_seconds))
        self._warmup_angles: deque[float] = deque(
            maxlen=round(self.config.sample_hz * self.config.max_warmup_seconds))
        self._seen_times: set[float] = set()
        self._batch_fingerprints: dict[str, str] = {}
        self._cycles = {1: _Cycle(), -1: _Cycle()}
        self._cooldown_until = -math.inf
        self._last_relative: float | None = None
        self.diagnostics = {
            "duplicate_batches": 0, "duplicate_samples": 0, "out_of_order": 0,
            "gaps": 0, "sensor_resets": 0, "aborted_cycles": 0,
            "rejected_cycles": 0, "invalid_samples": 0, "processed_samples": 0,
        }
        self._recent_rejections: deque[str] = deque(maxlen=30)
        self._recent_confirmed_gyro_peaks: deque[float] = deque(maxlen=10)
        self._uncertain_candidates: deque[dict] = deque(maxlen=20)

    def get_state(self) -> dict:
        return {
            "session_id": self.session_id, "repetitions": self.count,
            "calibration": self.calibration, "calibration_note": self.calibration_note,
            "orientation": ("positive" if self.orientation == 1 else
                            "negative" if self.orientation == -1 else "undetermined"),
            "phase": (self._cycles[self.orientation].phase if self.orientation else
                      "orientation_pending"),
            "standing_reference_deg": self.reference,
            "calibration_quality": {
                "mode": self.config.calibration_mode,
                "quiet_samples": self._calibration_sample_count or len(self._quiet),
                "required_quiet_samples": self.config.min_quiet_samples,
                "standing_window_samples": (len(self._standing) if
                                            self.config.calibration_mode == "initial_standing"
                                            else self._calibration_sample_count),
                "standing_angle_std_deg": self._calibration_angle_std,
                "longest_standing_run_samples": len(self._best_standing_angles),
                "longest_standing_run_angle_std_deg": (
                    pstdev(self._best_standing_angles)
                    if len(self._best_standing_angles) > 1 else
                    0.0 if self._best_standing_angles else None),
                "counts_enabled": self.calibration == "ready",
            },
            "last_sample_time_seconds": self._last_time,
            "diagnostics": self.diagnostics.copy(),
            "recent_rejections": list(self._recent_rejections),
            "uncertain_candidates": list(self._uncertain_candidates),
        }

    def process_batch(
        self, samples: Iterable[Mapping[str, object]], batch_id: str | None = None,
    ) -> list[RepetitionEvent]:
        # A CIN resource identifier/name is suitable; X-M2M-RI identifies a request.
        if batch_id == "":
            raise ValueError("batch_id must not be empty")
        rows = list(samples)  # Bound this to one incoming CIN/batch, never a recording.
        try:
            values = [_parse_values(row) for row in rows]
        except ValueError:
            self.diagnostics["invalid_samples"] += 1
            raise
        fingerprint = hashlib.sha256(repr(values).encode("utf-8")).hexdigest()
        if batch_id is not None:
            previous = self._batch_fingerprints.get(batch_id)
            if previous is not None:
                if previous != fingerprint:
                    raise ValueError(f"Batch identity {batch_id!r} has conflicting content")
                self.diagnostics["duplicate_batches"] += 1
                return []
        events: list[RepetitionEvent] = []
        for sample in rows:
            events.extend(self.process_sample(sample))
        if batch_id is not None:
            self._batch_fingerprints[batch_id] = fingerprint
        return events

    def _abort(self) -> None:
        if any(cycle.phase != "standing" for cycle in self._cycles.values()):
            self.diagnostics["aborted_cycles"] += 1
        for cycle in self._cycles.values():
            cycle.clear()

    def _reject(self, cycle: _Cycle, t: float, reason: str) -> None:
        self.diagnostics["rejected_cycles"] += 1
        self._recent_rejections.append(f"{t:.2f}s: {reason}")
        cycle.clear()

    def _recalibrate(self) -> None:
        self._abort()
        self.calibration = "warming_up"
        self.calibration_note = "Recalibrating after sensor reset"
        self.reference = None
        self._calibration_sample_count = 0
        self._calibration_angle_std = None
        self._best_standing_angles.clear()
        self._quiet.clear()
        self._standing.clear()
        self._warmup_angles.clear()
        self._first_time = None
        self._filtered_acc = self._filtered_gyro = None
        self._raw_angle = self._last_relative = None
        if self.config.orientation == "auto":
            self.orientation = None

    def process_sample(self, sample: Mapping[str, object]) -> list[RepetitionEvent]:
        try:
            t, acc, gyro, device_t = _parse_values(sample)
        except ValueError:
            self.diagnostics["invalid_samples"] += 1
            raise
        device_reset = (device_t is not None and self._last_device_time is not None
                        and device_t < self._last_device_time - 1000)
        clock_reset = (self._last_time is not None and
                       t < self._last_time - self.config.reset_gap_seconds)
        if device_reset or clock_reset:
            self.diagnostics["sensor_resets"] += 1
            self._recalibrate()
            self._seen_times.clear()  # The device clock may reuse previous timestamps.
            self._last_time = self._last_device_time = None
        if t in self._seen_times:
            self.diagnostics["duplicate_samples"] += 1
            return []
        if self._last_time is not None and t < self._last_time:
            self.diagnostics["out_of_order"] += 1
            return []
        dt = 1 / self.config.sample_hz if self._last_time is None else t - self._last_time
        if dt > self.config.max_gap_ms / 1000 + 1e-9:
            self.diagnostics["gaps"] += 1
            self._abort()
            self._filtered_acc = self._filtered_gyro = None
            self._raw_angle = self._last_relative = None
            if dt >= self.config.reset_gap_seconds:
                self._recalibrate()
        self._seen_times.add(t)
        if self._first_time is None:
            self._first_time = t
        self._last_time = t
        self._last_device_time = device_t
        self.diagnostics["processed_samples"] += 1
        alpha = 1 - math.exp(-2 * math.pi * self.config.lowpass_hz * min(
            dt, self.config.max_gap_ms / 1000))
        if self._filtered_acc is None:
            self._filtered_acc, self._filtered_gyro = acc.copy(), gyro.copy()
        else:
            for i in range(3):
                self._filtered_acc[i] += alpha * (acc[i] - self._filtered_acc[i])
                self._filtered_gyro[i] += alpha * (gyro[i] - self._filtered_gyro[i])
        angle = math.degrees(math.atan2(self._filtered_acc[0], -self._filtered_acc[1]))
        if self._raw_angle is not None:
            angle += 360 * round((self._raw_angle - angle) / 360)
        self._raw_angle = angle
        gyro_norm = math.sqrt(sum(value * value for value in self._filtered_gyro))
        if self.reference is None:
            # An initial-standing timeout is terminal for calibration. Continue
            # processing timestamps and diagnostics, but do not let later exercise
            # samples rewrite the quality evidence from the calibration attempt.
            if (self.config.calibration_mode == "initial_standing" and
                    self.calibration == "uncertain"):
                return []
            self._warmup_angles.append(angle)
            if gyro_norm < self.config.quiet_gyro_deg_s:
                self._quiet.append(angle)
                self._standing.append((t, angle))
                if len(self._standing) > len(self._best_standing_angles):
                    self._best_standing_angles = [value for _, value in self._standing]
            else:
                self._standing.clear()
            elapsed = t - self._first_time
            if self.config.calibration_mode == "initial_standing":
                window_complete = (len(self._standing) == self._standing.maxlen and
                                   self._standing[-1][0] - self._standing[0][0] >=
                                   self.config.standing_calibration_seconds - 1 / self.config.sample_hz)
                if window_complete and pstdev(v for _, v in self._standing) <= self.config.standing_angle_std_deg:
                    self.reference = median(v for _, v in self._standing)
                    self._calibration_sample_count = len(self._standing)
                    self._calibration_angle_std = pstdev(v for _, v in self._standing)
                    self.calibration = "ready"
                    self.calibration_note = "Explicit initial standing window observed"
                elif elapsed >= self.config.max_warmup_seconds:
                    self.calibration = "uncertain"
                    self._calibration_sample_count = len(self._quiet)
                    self.calibration_note = "No stable initial standing window; counting disabled"
                else:
                    self.calibration = "warming_up"
                    self.calibration_note = "Waiting for a stable initial standing window"
                return []
            if elapsed >= self.config.warmup_seconds:
                if len(self._quiet) >= self.config.min_quiet_samples:
                    self.reference = median(self._quiet)
                    self._calibration_sample_count = len(self._quiet)
                    self._calibration_angle_std = (pstdev(self._quiet)
                                                    if len(self._quiet) > 1 else 0.0)
                    self.calibration = "ready"
                    self.calibration_note = "Quiet standing reference observed"
                elif elapsed >= self.config.max_warmup_seconds:
                    # A moving sensor may have no quiet window. Use the observed
                    # posture provisionally and expose the uncertainty to callers.
                    self.reference = median(self._warmup_angles)
                    self._calibration_sample_count = len(self._quiet)
                    self._calibration_angle_std = (pstdev(self._warmup_angles)
                                                    if len(self._warmup_angles) > 1 else None)
                    self.calibration = "uncertain"
                    self.calibration_note = (
                        f"Only {len(self._quiet)} quiet samples; counting disabled")
                else:
                    self.calibration = "uncertain"
                    self.calibration_note = (
                        f"Only {len(self._quiet)} quiet samples; waiting for "
                        f"{self.config.min_quiet_samples}")
            return []
        if self.calibration != "ready":
            # An uncertain reference cannot support a confirmed repetition.
            return []
        assert self.reference is not None
        relative = angle - self.reference
        rate = (0.0 if self._last_relative is None else
                (relative - self._last_relative) / max(dt, 1e-6))
        self._last_relative = relative
        if (all(c.phase == "standing" for c in self._cycles.values())
                and gyro_norm < self.config.quiet_gyro_deg_s):
            lower_standing_envelope = (self.orientation is not None and
                                       self.orientation * relative < -self.config.exit_deg)
            adaptation_hz = (0.5 if lower_standing_envelope else
                             self.config.baseline_adaptation_hz)
            weight = 1 - math.exp(-2 * math.pi * adaptation_hz * dt)
            self.reference += weight * (angle - self.reference)
        signs = (self.orientation,) if self.orientation is not None else (1, -1)
        for sign in signs:
            event = self._step(sign, t, sign * relative, sign * angle,
                               sign * rate, self._filtered_gyro)
            if event is not None:
                if self.orientation is None:
                    self.orientation = sign
                    self._cycles[-sign].clear()
                self.count += 1
                return [event]
        return []

    def _step(self, sign: int, t: float, tilt: float, raw: float, rate: float,
              gyro: list[float]) -> RepetitionEvent | None:
        config = self.config
        cycle = self._cycles[sign]
        if t < self._cooldown_until:
            return None
        if cycle.phase == "standing":
            if tilt <= config.exit_deg:
                cycle.armed = True
            if cycle.armed and tilt >= config.enter_deg and rate > 0:
                cycle.phase = "descending"
                cycle.armed = False
                cycle.start = cycle.peak_time = t
                cycle.peak = tilt
                cycle.peak_raw = raw
            return None
        if t - cycle.start > config.max_cycle_seconds:
            self._reject(cycle, t, "cycle exceeded maximum duration")
            return None
        if cycle.phase in ("descending", "bottom") and tilt > cycle.peak:
            cycle.peak, cycle.peak_raw, cycle.peak_time = tilt, raw, t
        maxima = cycle.ascent_max if cycle.phase == "ascending" else cycle.descent_max
        if abs(rate) >= 3.0:
            cycle.tilt_energy += rate * rate
            for axis, value in enumerate(gyro):
                cycle.correlation_products[axis] += rate * value
                cycle.gyro_energy[axis] += value * value
        for axis, value in enumerate(gyro):
            maxima[axis][0] = max(maxima[axis][0], value)
            maxima[axis][1] = max(maxima[axis][1], -value)
        if cycle.phase == "descending":
            if tilt <= config.exit_deg and cycle.peak < config.bottom_deg:
                cycle.clear()
            elif (tilt >= config.bottom_deg and
                  t - cycle.start >= config.min_descent_seconds):
                cycle.phase = "bottom"
        elif cycle.phase == "bottom":
            if tilt <= cycle.peak - config.turn_hysteresis_deg and rate < 0:
                cycle.phase = "ascending"
                cycle.bottom = cycle.peak_time
        elif cycle.phase == "ascending" and tilt <= config.exit_deg:
            raw_return = cycle.peak_raw - raw
            if (t - cycle.bottom < config.min_ascent_seconds or
                    not config.min_cycle_seconds <= t - cycle.start <= config.max_cycle_seconds or
                    raw_return < config.min_raw_return_deg):
                self._reject(cycle, t, (
                    f"incomplete timing/raw return (peak={cycle.peak:.1f}deg, "
                    f"raw_return={raw_return:.1f}deg, cycle={t-cycle.start:.2f}s, "
                    f"ascent={t-cycle.bottom:.2f}s)"))
                return None
            choices = []
            for axis, name in enumerate(AXES):
                if config.gyro_axis != "auto" and name != config.gyro_axis:
                    continue
                for gyro_sign in (-1, 1):
                    if config.gyro_sign and gyro_sign != config.gyro_sign:
                        continue
                    direction = 0 if gyro_sign * sign > 0 else 1
                    descent = cycle.descent_max[axis][direction]
                    ascent = cycle.ascent_max[axis][1 - direction]
                    strength = min(descent, ascent)
                    correlation = (cycle.correlation_products[axis] /
                                   math.sqrt(cycle.tilt_energy * cycle.gyro_energy[axis])
                                   if cycle.tilt_energy and cycle.gyro_energy[axis] else 0.0)
                    aligned_correlation = correlation * gyro_sign * sign
                    if strength >= config.gyro_evidence_deg_s:
                        choices.append((aligned_correlation, strength, axis,
                                        gyro_sign, descent, ascent))
            if not choices:
                self._reject(cycle, t, (
                    f"weak directional gyroscope evidence (peak={cycle.peak:.1f}deg, "
                    f"raw_return={raw_return:.1f}deg)"))
                return None
            correlation, strength, axis, gyro_sign, descent, ascent = max(choices)
            if correlation < 0.5:
                self._reject(cycle, t, f"tilt/gyroscope correlation {correlation:.2f} < 0.50")
                return None
            event = RepetitionEvent(
                self.session_id, self.count + 1, cycle.start, cycle.bottom, t,
                cycle.peak, raw_return, descent, ascent,
                "positive" if sign == 1 else "negative", AXES[axis], gyro_sign,
                t - cycle.bottom, t - cycle.start, cycle.bottom - cycle.start,
                t - cycle.bottom)
            gyro_peak = max(descent, ascent)
            prior_typical_peak = (median(self._recent_confirmed_gyro_peaks)
                                  if len(self._recent_confirmed_gyro_peaks) >= 3 else None)
            if prior_typical_peak is not None and gyro_peak > 3 * prior_typical_peak:
                candidate = event.as_dict()
                candidate["reason"] = (
                    f"directional gyro peak {gyro_peak:.1f}deg/s exceeds 3x "
                    f"prior confirmed median {prior_typical_peak:.1f}deg/s")
                self._uncertain_candidates.append(candidate)
                cycle.clear()
                self._cooldown_until = t + config.cooldown_seconds
                return None
            self._recent_confirmed_gyro_peaks.append(gyro_peak)
            cycle.clear()
            self._cooldown_until = t + config.cooldown_seconds
            return event
        return None
