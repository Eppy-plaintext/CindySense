"""Small, dependency-free repetition state machines and Cindy workout state."""

import math
import time

MIN_VISIBILITY = 0.50
STABLE_FRAMES = 3
BODY_DOWN_DELTA = 0.05
BODY_RETURN_DELTA = 0.02
SQUAT_FAST_TRAVEL = 0.05
SQUAT_FAST_RETURN = 0.03
FAST_FRAME_GAP_MS = 80
FAST_MOTION_WINDOW_MS = 250
SQUAT_COOLDOWN_FRAMES = 4


def visibility(point):
    score = getattr(point, "visibility", None)
    return 1.0 if score is None else float(score)


def angle_degrees(a, b, c, width, height):
    ax, ay = (a.x - b.x) * width, (a.y - b.y) * height
    cx, cy = (c.x - b.x) * width, (c.y - b.y) * height
    denom = math.hypot(ax, ay) * math.hypot(cx, cy)
    if denom < 1e-6:
        return None
    cosine = max(-1.0, min(1.0, (ax * cx + ay * cy) / denom))
    return math.degrees(math.acos(cosine))


def _visible_angle(landmarks, indices, width, height):
    points = [landmarks[i] for i in indices]
    confidence = min(visibility(p) for p in points)
    if confidence < MIN_VISIBILITY:
        return None, confidence
    return angle_degrees(*points, width, height), confidence


def exercise_measurement(exercise, landmarks, width, height, include_reason=False):
    """Return (angle, confidence, label); confidence is 0 when pose is unusable."""
    def result(angle, confidence, label, reason):
        value = (angle, confidence, label)
        return value + (reason,) if include_reason else value

    if exercise == "squat":
        candidates = []
        rejected = []
        for indices, label in (((23, 25, 27), "LEFT"), ((24, 26, 28), "RIGHT")):
            angle, confidence = _visible_angle(landmarks, indices, width, height)
            if angle is not None:
                candidates.append((confidence, angle, label))
            else:
                rejected.append(f"{label} leg visibility {confidence:.2f} < {MIN_VISIBILITY:.2f}")
        if not candidates:
            return result(None, 0.0, "NONE", "; ".join(rejected))
        confidence, angle, label = max(candidates, key=lambda item: item[0])
        return result(angle, confidence, label, "knee landmarks visible")

    if exercise not in ("pushup", "pullup"):
        raise ValueError(f"Unknown exercise: {exercise}")
    candidates = []
    rejected = []
    for shoulder, elbow, wrist, hip, ankle, side in (
        (11, 13, 15, 23, 27, "LEFT"), (12, 14, 16, 24, 28, "RIGHT")
    ):
        elbow_angle, confidence = _visible_angle(
            landmarks, (shoulder, elbow, wrist), width, height
        )
        if elbow_angle is None:
            rejected.append(f"{side} elbow visibility {confidence:.2f} < {MIN_VISIBILITY:.2f}")
            continue
        if exercise == "pushup":
            alignment, align_conf = _visible_angle(
                landmarks, (shoulder, hip, ankle), width, height
            )
            if alignment is None:
                rejected.append(f"{side} shoulder/hip/ankle visibility {align_conf:.2f} < {MIN_VISIBILITY:.2f}")
                continue
            if alignment < 145.0:
                rejected.append(f"{side} body alignment {alignment:.0f}<145 deg")
                continue
            confidence = min(confidence, align_conf)
        # Pull-ups require the elbow triplet. Wrist image height and nose
        # visibility vary by view; full-cycle vertical motion is checked later.
        candidates.append((confidence, elbow_angle, side))
    if not candidates:
        return result(None, 0.0, "NONE", "; ".join(rejected) or "no usable arm landmarks")
    confidence, angle, side = max(candidates, key=lambda item: item[0])
    return result(angle, confidence, side, "elbow and exercise posture visible")


def body_movement_measurement(exercise, landmarks, tracked_side=None):
    """Return normalized vertical torso position, visibility, and signal description.

    A fixed camera is assumed. Squat/push-up descent moves hips/shoulders down in
    the image; pull-up ascent moves the torso up, so its signal is inverted later.
    """
    if exercise == "squat":
        indices = (23, 24)
        label = "hip midpoint"
    elif exercise == "pushup":
        indices = (11,) if tracked_side == "LEFT" else (12,) if tracked_side == "RIGHT" else (11, 12)
        label = "shoulder midpoint"
    elif exercise == "pullup":
        indices = (11,) if tracked_side == "LEFT" else (12,) if tracked_side == "RIGHT" else (11, 12)
        label = "shoulder midpoint"
    else:
        raise ValueError(f"Unknown exercise: {exercise}")
    visible = [landmarks[index] for index in indices if visibility(landmarks[index]) >= MIN_VISIBILITY]
    if not visible:
        score = min(visibility(landmarks[index]) for index in indices)
        return None, score, f"{label} landmarks below visibility {MIN_VISIBILITY:.2f}"
    position = sum(float(point.y) for point in visible) / len(visible)
    confidence = min(visibility(point) for point in visible)
    return position, confidence, label


def pullup_auxiliary_measurement(landmarks):
    """Optional head height and shoulder horizontal position for pull-up evidence."""
    head_y = float(landmarks[0].y) if visibility(landmarks[0]) >= MIN_VISIBILITY else None
    shoulders = [landmarks[i] for i in (11, 12)
                 if visibility(landmarks[i]) >= MIN_VISIBILITY]
    shoulder_x = (sum(float(point.x) for point in shoulders) / 2
                  if len(shoulders) == 2 else None)
    return head_y, shoulder_x


class ExerciseCounter:
    """Count a complete joint-angle cycle with exercise-specific posture evidence."""
    CONFIG = {
        "squat": (115.0, 155.0),
        "pushup": (95.0, 155.0),
        "pullup": (100.0, 150.0),
    }
    MAX_MISSING_FRAMES = 8
    COOLDOWN_FRAMES = 10
    MOVEMENT_DOWN = {"pushup": 0.035, "pullup": 0.04}
    ARM_DOWN = {"pushup": 95.0, "pullup": 120.0}
    ARM_UP = {"pushup": 155.0, "pullup": 145.0}

    def __init__(self, exercise):
        if exercise not in self.CONFIG:
            raise ValueError(f"Unknown exercise: {exercise}")
        self.exercise = exercise
        self.reps = 0
        self.phase = "UNKNOWN"
        self.down_frames = self.up_frames = 0
        self.fast_down_frames = self.fast_up_frames = 0
        self.last_sample_ms = self.fast_motion_until_ms = None
        self.last_squat_angle = self.last_squat_position = None
        self.fast_squat_cycle = False
        self.last_raw_angle = None
        self.fast_arm_candidate = False
        self.smoothed_angle = None
        self.smoothed_movement = None
        self.movement_baseline = None
        self.body_descent_seen = False
        self.position_calibration_frames = 0
        self.missing_frames = 0
        self.cooldown_frames = 0
        self.last_reason = "waiting_for_extended_pose"
        self.last_rejection = None
        self.rejected_candidates = 0
        self.arm_peak = 0.0
        self.head_baseline = self.head_peak = None
        self.lateral_baseline = None
        self.max_lateral = 0.0
        self.extended_frames = 0
        self.arm_phase_frames = 0
        self.arm_start_angle = None
        self.last_raw_position = self.last_head_position = None

    def reset_phase(self):
        """Re-arm after a mode switch without changing the accumulated count."""
        self.phase = "UNKNOWN"
        self.down_frames = self.up_frames = self.missing_frames = 0
        self.fast_down_frames = self.fast_up_frames = 0
        self.last_sample_ms = self.fast_motion_until_ms = None
        self.last_squat_angle = self.last_squat_position = None
        self.fast_squat_cycle = False
        self.last_raw_angle = None
        self.fast_arm_candidate = False
        self.smoothed_angle = self.smoothed_movement = self.movement_baseline = None
        self.body_descent_seen = False
        self.position_calibration_frames = 0
        self.cooldown_frames = 0
        self.last_reason = "waiting_for_extended_pose"
        self.last_rejection = None
        self.arm_peak = 0.0
        self.head_baseline = self.head_peak = None
        self.lateral_baseline = None
        self.max_lateral = 0.0
        self.extended_frames = 0
        self.arm_phase_frames = 0
        self.arm_start_angle = None
        self.last_raw_position = self.last_head_position = None

    def update(self, angle, movement=None, head_position=None, lateral_position=None, timestamp_ms=None):
        if self.exercise != "squat":
            return self._update_arm(angle, movement, head_position, lateral_position, timestamp_ms)
        if angle is None or (self.exercise != "squat" and movement is None):
            self.down_frames = self.up_frames = 0
            self.fast_down_frames = self.fast_up_frames = 0
            self.last_squat_angle = self.last_squat_position = None
            self.fast_motion_until_ms = None
            self.smoothed_angle = None
            self.smoothed_movement = None
            self.missing_frames += 1
            if self.missing_frames >= self.MAX_MISSING_FRAMES:
                self.phase = "UNKNOWN"
                self.movement_baseline = None
                self.position_calibration_frames = 0
                self.body_descent_seen = False
                self.fast_squat_cycle = False
            self.last_reason = (
                "awaiting_first_visible_extended_pose" if self.phase == "UNKNOWN"
                else f"no_measurement; preserving {self.phase} through occlusion"
            )
            return False
        self.missing_frames = 0
        fast_sample = (timestamp_ms is not None and self.last_sample_ms is not None
                       and 0 < timestamp_ms - self.last_sample_ms <= FAST_FRAME_GAP_MS
                       and movement is not None and self.last_squat_position is not None
                       and self.last_squat_angle is not None
                       and 25 <= abs(angle - self.last_squat_angle) <= 80
                       and 0.02 <= abs(movement - self.last_squat_position) <= 0.08)
        if fast_sample:
            self.fast_motion_until_ms = timestamp_ms + FAST_MOTION_WINDOW_MS
        fast_window = (timestamp_ms is not None and self.fast_motion_until_ms is not None
                       and timestamp_ms <= self.fast_motion_until_ms)
        self.last_sample_ms = timestamp_ms
        self.last_squat_angle, self.last_squat_position = angle, movement
        smoothing = 0.65 if fast_window else 0.4
        if angle is not None:
            self.smoothed_angle = angle if self.smoothed_angle is None else (
                smoothing * angle + (1 - smoothing) * self.smoothed_angle
            )
        if movement is not None:
            self.smoothed_movement = movement if self.smoothed_movement is None else (
                smoothing * movement + (1 - smoothing) * self.smoothed_movement
            )
        down, up = self.CONFIG[self.exercise]
        movement_sign = -1.0 if self.exercise == "pullup" else 1.0
        if (self.phase == "UP" and self.movement_baseline is None
                and self.smoothed_movement is not None):
            self.movement_baseline = self.smoothed_movement
        movement_delta = None
        if self.smoothed_movement is not None and self.movement_baseline is not None:
            movement_delta = movement_sign * (self.smoothed_movement - self.movement_baseline)
        if movement_delta is not None and movement_delta >= self.MOVEMENT_DOWN.get(self.exercise, BODY_DOWN_DELTA):
            self.body_descent_seen = True
        if self.phase == "UNKNOWN":
            angle_calibrated = self.smoothed_angle is not None and self.smoothed_angle >= up
            if angle_calibrated:
                self.phase = "UP"
                if self.smoothed_movement is not None:
                    self.movement_baseline = self.smoothed_movement
                self.last_reason = "extended angle calibrated"
            else:
                self.last_reason = f"waiting for extended angle>={up:.0f} and required landmarks"
            return False
        if self.cooldown_frames:
            self.cooldown_frames -= 1
            self.last_reason = "cooldown"
            return False
        if self.phase == "UP":
            angle_down = self.smoothed_angle is not None and self.smoothed_angle <= down
            position_down = movement_delta is not None and movement_delta >= self.MOVEMENT_DOWN.get(self.exercise, BODY_DOWN_DELTA)
            accepted_down = angle_down and (self.exercise == "squat" or position_down)
            self.down_frames = self.down_frames + 1 if accepted_down else 0
            fast_down = (fast_window and accepted_down and angle <= 90
                         and movement_delta is not None and movement_delta >= SQUAT_FAST_TRAVEL)
            self.fast_down_frames = self.fast_down_frames + 1 if fast_down else 0
            accelerated = self.fast_down_frames >= 2
            if self.down_frames >= STABLE_FRAMES or accelerated:
                self.phase, self.down_frames = "DOWN", 0
                self.fast_down_frames = self.fast_up_frames = 0
                self.fast_squat_cycle = accelerated
                self.last_reason = "down transition accepted by joint angle and posture"
            else:
                signal = f"angle<={down:.0f}" + (" and torso motion" if self.exercise != "squat" else "")
                self.last_reason = f"waiting for down {signal} ({self.down_frames}/{STABLE_FRAMES})"
            if (self.down_frames == 0 and self.smoothed_movement is not None
                    and self.smoothed_angle is not None and self.smoothed_angle >= up):
                self.movement_baseline = 0.98 * self.movement_baseline + 0.02 * self.smoothed_movement
        else:
            angle_up = self.smoothed_angle is not None and self.smoothed_angle >= up
            position_up = (self.body_descent_seen and movement_delta is not None
                           and movement_delta <= BODY_RETURN_DELTA)
            fast_squat_return = (self.fast_squat_cycle and self.body_descent_seen
                                 and movement_delta is not None
                                 and movement_delta <= SQUAT_FAST_RETURN)
            accepted_up = angle_up and (position_up if self.exercise != "squat"
                                       else not self.fast_squat_cycle or fast_squat_return)
            self.up_frames = self.up_frames + 1 if accepted_up else 0
            fast_up = self.fast_squat_cycle and fast_window and accepted_up
            self.fast_up_frames = self.fast_up_frames + 1 if fast_up else 0
            if self.up_frames >= STABLE_FRAMES or self.fast_up_frames >= 2:
                self.phase, self.up_frames = "UP", 0
                self.fast_down_frames = self.fast_up_frames = 0
                self.reps += 1
                self.cooldown_frames = 0 if self.fast_squat_cycle else SQUAT_COOLDOWN_FRAMES
                self.fast_squat_cycle = False
                self.last_reason = "full joint-angle cycle with posture return"
                if self.smoothed_movement is not None:
                    self.movement_baseline = self.smoothed_movement
                self.body_descent_seen = False
                return True
            signal = f"angle>={up:.0f}" + (" and torso return" if self.exercise != "squat" else "")
            self.last_reason = f"waiting for up {signal} ({self.up_frames}/{STABLE_FRAMES})"
        return False


    def _reject_arm(self, reason, angle, movement, head, lateral):
        self.rejected_candidates += 1
        self.last_rejection = reason
        self.last_reason = f"rejected candidate: {reason}"
        self.phase = "UP" if angle is not None and angle >= self.ARM_UP[self.exercise] else "UNKNOWN"
        self.arm_start_angle = angle if self.phase == "UP" else None
        self.movement_baseline = movement if self.phase == "UP" else None
        self.head_baseline = head if self.phase == "UP" else None
        self.last_raw_position = movement
        self.last_head_position = head
        self.lateral_baseline = lateral if self.phase == "UP" else None
        self.arm_peak = 0.0
        self.head_peak = None
        self.max_lateral = 0.0
        self.down_frames = self.up_frames = self.extended_frames = self.arm_phase_frames = 0
        self.cooldown_frames = 5
        self.fast_arm_candidate = False
        self.last_raw_angle = None
        if self.phase == "UNKNOWN":
            self.smoothed_angle = self.smoothed_movement = None

    def _update_arm(self, angle, movement, head, lateral, timestamp_ms=None):
        pullup = self.exercise == "pullup"
        if angle is None or (movement is None and (not pullup or head is None)):
            self.missing_frames += 1
            if self.missing_frames > 3:
                self.down_frames = self.up_frames = 0
            if self.missing_frames >= self.MAX_MISSING_FRAMES:
                if self.phase == "DOWN":
                    self._reject_arm("landmarks missing too long", None, None, None, None)
                else:
                    self.phase = "UNKNOWN"
                    self.movement_baseline = self.head_baseline = None
                    self.last_raw_position = self.last_head_position = None
                    self.smoothed_angle = self.smoothed_movement = None
                    self.fast_arm_candidate = False
                    self.last_raw_angle = None
                    self.last_reason = "landmarks missing; recalibrating start"
                return False
            self.last_reason = f"brief landmark loss {self.missing_frames}/{self.MAX_MISSING_FRAMES}; preserving {self.phase}"
            return False
        if (movement is not None and self.last_raw_position is not None
                and abs(movement - self.last_raw_position) > 0.15 + 0.03 * self.missing_frames):
            self._update_arm(None, None, None, None)
            self.last_reason = f"implausible shoulder tracking jump; preserving {self.phase}"
            return False
        if (head is not None and self.last_head_position is not None
                and abs(head - self.last_head_position) > 0.15 + 0.03 * self.missing_frames):
            head = None
            self.head_baseline = self.head_peak = None
        fast_sample = (not pullup and timestamp_ms is not None and self.last_sample_ms is not None
                       and 0 < timestamp_ms - self.last_sample_ms <= FAST_FRAME_GAP_MS
                       and self.last_raw_angle is not None and self.last_raw_position is not None
                       and movement is not None and 40 <= abs(angle - self.last_raw_angle) <= 100
                       and 0.03 <= abs(movement - self.last_raw_position) <= 0.15)
        self.missing_frames = 0
        self.last_raw_position = movement
        self.last_raw_angle = angle
        self.last_sample_ms = timestamp_ms
        if head is not None:
            self.last_head_position = head
        # Push-ups can reverse direction in only a few camera frames. Follow
        # their measured motion faster while retaining two-frame confirmation.
        smoothing = 0.45 if pullup else 0.80 if fast_sample else 0.65
        required_frames = STABLE_FRAMES if pullup else 2
        self.smoothed_angle = angle if self.smoothed_angle is None else smoothing * angle + (1 - smoothing) * self.smoothed_angle
        if movement is not None:
            self.smoothed_movement = (movement if self.smoothed_movement is None else
                                      smoothing * movement + (1 - smoothing) * self.smoothed_movement)
        position = self.smoothed_movement
        down_sign = -1.0 if pullup else 1.0
        delta = (down_sign * (position - self.movement_baseline)
                 if position is not None and self.movement_baseline is not None else None)
        head_delta = (self.head_baseline - head if head is not None and self.head_baseline is not None else None)
        up = self.ARM_UP[self.exercise]
        if self.phase == "UNKNOWN":
            if self.smoothed_angle >= up and position is not None:
                self.phase = "UP"
                self.movement_baseline = position
                self.head_baseline = head
                self.lateral_baseline = lateral
                self.arm_start_angle = self.smoothed_angle
                self.last_reason = "extended starting pose calibrated"
            else:
                self.last_reason = f"waiting for extended angle>={up:.0f} and shoulder position"
            return False
        if self.cooldown_frames:
            self.cooldown_frames -= 1
            self.last_reason = "cooldown"
            return False
        if self.phase == "UP":
            if fast_sample:
                self.fast_arm_candidate = True
            elif self.smoothed_angle >= up and self.down_frames == 0:
                self.fast_arm_candidate = False
            down = min(self.ARM_DOWN["pullup"], self.arm_start_angle - 35.0) if pullup else self.ARM_DOWN["pushup"]
            angle_down = self.smoothed_angle <= down
            # Shallow elbow flexion needs stronger whole-body travel than a
            # clearly flexed elbow; this admits camera-angle variation without
            # making an arm gesture alone look like a pull-up.
            shoulder_threshold = (0.070 if self.smoothed_angle > 100 else 0.040) if pullup else 0.035
            head_threshold = 0.080 if self.smoothed_angle > 100 else 0.050
            shoulder_down = delta is not None and delta >= shoulder_threshold
            head_down = pullup and head_delta is not None and head_delta >= head_threshold
            accepted = angle_down and (shoulder_down or head_down)
            self.down_frames = self.down_frames + 1 if accepted else 0
            if self.down_frames >= required_frames:
                self.phase = "DOWN"
                self.down_frames = self.up_frames = self.extended_frames = self.arm_phase_frames = 0
                self.arm_peak = max(0.0, delta or 0.0)
                self.head_peak = max(0.0, head_delta or 0.0) if pullup else None
                self.max_lateral = 0.0
                self.last_reason = "flexion and vertical travel accepted; waiting for return"
            else:
                self.last_reason = (f"waiting for flexion<={down:.0f} and vertical travel "
                                    f"(shoulder={delta if delta is not None else 0:.3f}, "
                                    f"head={head_delta if head_delta is not None else 0:.3f}; "
                                    f"{self.down_frames}/{required_frames})")
                if self.smoothed_angle >= up and position is not None:
                    # Calibrate only while fully extended, never during descent.
                    self.movement_baseline = 0.95 * self.movement_baseline + 0.05 * position
                    self.arm_start_angle = 0.9 * self.arm_start_angle + 0.1 * self.smoothed_angle
                    if head is not None:
                        self.head_baseline = head if self.head_baseline is None else 0.95 * self.head_baseline + 0.05 * head
                    if lateral is not None:
                        self.lateral_baseline = lateral if self.lateral_baseline is None else 0.95 * self.lateral_baseline + 0.05 * lateral
            return False

        self.arm_phase_frames += 1
        self.arm_peak = max(self.arm_peak, delta or 0.0)
        if pullup and head_delta is not None:
            self.head_peak = max(self.head_peak or 0.0, head_delta)
        if pullup and lateral is not None and self.lateral_baseline is not None:
            self.max_lateral = max(self.max_lateral, abs(lateral - self.lateral_baseline))
        shoulder_return = (delta is not None and self.arm_peak >= (0.030 if pullup else 0.035)
                           and self.arm_peak - delta >= max(0.020, 0.65 * self.arm_peak))
        head_return = (pullup and head_delta is not None and (self.head_peak or 0) >= 0.040
                       and self.head_peak - head_delta >= max(0.025, 0.65 * self.head_peak))
        returned = shoulder_return or head_return
        angle_up = self.smoothed_angle >= up
        if not pullup and not angle_up:
            # In the recorded fast set the shoulder returned to its start
            # height repeatedly, but MediaPipe held the elbow near 140-150°.
            # Accept that alternate top only after substantial descent and
            # near-complete torso return, never from elbow angle alone.
            angle_up = (self.smoothed_angle >= 135.0 and self.arm_peak >= 0.08
                        and delta is not None and abs(delta) <= 0.035)
        self.extended_frames = self.extended_frames + 1 if angle_up else 0
        self.up_frames = self.up_frames + 1 if angle_up and returned else 0
        if self.up_frames >= required_frames:
            if pullup and self.max_lateral >= 0.10:
                self._reject_arm(f"lateral swing {self.max_lateral:.3f} >= 0.100", self.smoothed_angle, position, head, lateral)
                return False
            self.reps += 1
            self.phase = "UP"
            # A fresh DOWN state still has to be earned before another rep.
            # Ten ignored frames cost about a third of a second at 30 fps.
            self.cooldown_frames = self.COOLDOWN_FRAMES if pullup else 0 if self.fast_arm_candidate else 2
            self.fast_arm_candidate = False
            self.movement_baseline = position
            self.head_baseline = head
            self.lateral_baseline = lateral
            self.arm_start_angle = self.smoothed_angle
            self.arm_peak = 0.0
            self.head_peak = None
            self.up_frames = self.extended_frames = self.arm_phase_frames = 0
            self.last_reason = "complete flexion, vertical travel, and stable return"
            return True
        if self.extended_frames >= 20 or self.arm_phase_frames >= 300:
            reason = (f"extended elbows but insufficient torso return; peak={self.arm_peak:.3f}, "
                      f"current={delta if delta is not None else 0:.3f}"
                      if self.extended_frames >= 20 else "candidate timed out before return")
            self._reject_arm(reason, self.smoothed_angle, position, head, lateral)
            return False
        self.last_reason = (f"waiting for extended angle>={up:.0f} and rise from candidate peak "
                            f"(angle={self.smoothed_angle:.0f}, peak={self.arm_peak:.3f}, "
                            f"current={delta if delta is not None else 0:.3f}; "
                            f"{self.up_frames}/{required_frames})")
        return False


class CindyWorkout:
    EXERCISES = ("pullup", "pushup", "squat")
    TARGETS = {"pullup": 5, "pushup": 10, "squat": 15}

    def __init__(self, duration_seconds=1200, clock=time.monotonic):
        self.duration_seconds = duration_seconds
        self.clock = clock
        self.started = clock()
        self.progress = {name: 0 for name in self.EXERCISES}
        self.completed = set()
        self.rounds = 0
        self.stage = 0

    @property
    def elapsed(self):
        return min(self.duration_seconds, max(0.0, self.clock() - self.started))

    @property
    def remaining(self):
        return max(0.0, self.duration_seconds - self.elapsed)

    @property
    def finished(self):
        return self.remaining <= 0

    @property
    def current_exercise(self):
        return self.EXERCISES[self.stage]

    def record_rep(self, exercise):
        if self.finished:
            return self.rounds
        self.progress[exercise] += 1
        if self.progress[exercise] >= self.TARGETS[exercise]:
            self.progress[exercise] = 0
            self.completed.add(exercise)
        while self.current_exercise in self.completed:
            self.completed.remove(self.current_exercise)
            self.stage = (self.stage + 1) % len(self.EXERCISES)
            if self.stage == 0:
                self.rounds += 1
        return self.rounds

    def select(self, exercise):
        if exercise in self.EXERCISES:
            self.stage = self.EXERCISES.index(exercise)


def select_exercise_key(key, workout):
    """Apply webcam key codes 1/2/3 to workout stage; return selected name or None."""
    selected = {ord("1"): "pullup", ord("2"): "pushup", ord("3"): "squat"}.get(key)
    if selected is not None and workout is not None:
        workout.select(selected)
    return selected

