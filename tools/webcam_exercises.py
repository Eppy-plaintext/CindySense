"""Small, dependency-free repetition state machines and Cindy workout state."""

import math
import time

MIN_VISIBILITY = 0.50
STABLE_FRAMES = 3
BODY_DOWN_DELTA = 0.05
BODY_RETURN_DELTA = 0.02


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
        alignment_missing = False
        if exercise == "pushup":
            # Use body alignment when visible. At the bottom of a push-up the ankles
            # are often occluded; do not discard a usable elbow angle for that alone.
            alignment, align_conf = _visible_angle(
                landmarks, (shoulder, hip, ankle), width, height
            )
            if alignment is not None and alignment < 145.0:
                rejected.append(f"{side} body alignment {alignment:.0f}<145 deg")
                continue
            if alignment is not None:
                confidence = min(confidence, align_conf)
            else:
                alignment_missing = True
        elif exercise == "pullup":
            # At extension, wrists should be above shoulders (image y decreases upward).
            if elbow_angle >= 140.0 and landmarks[wrist].y > landmarks[shoulder].y + 0.12:
                rejected.append(f"{side} extended wrists not above shoulders")
                continue
        candidates.append((confidence, elbow_angle, side, alignment_missing))
    if not candidates:
        return result(None, 0.0, "NONE", "; ".join(rejected) or "no usable arm landmarks")
    confidence, angle, side, alignment_missing = max(candidates, key=lambda item: item[0])
    reason = "elbow landmarks visible"
    if exercise == "pushup" and alignment_missing:
        reason = "ankle/hip alignment occluded; using elbow angle"
    return result(angle, confidence, side, reason)


def body_movement_measurement(exercise, landmarks):
    """Return normalized vertical torso position, visibility, and signal description.

    A fixed camera is assumed. Squat/push-up descent moves hips/shoulders down in
    the image; pull-up ascent moves the torso up, so its signal is inverted later.
    """
    if exercise == "squat":
        indices = (23, 24)
        label = "hip midpoint"
    elif exercise == "pushup":
        indices = (11, 12)
        label = "shoulder midpoint"
    elif exercise == "pullup":
        indices = (11, 12, 23, 24)
        label = "torso midpoint"
    else:
        raise ValueError(f"Unknown exercise: {exercise}")
    visible = [landmarks[index] for index in indices if visibility(landmarks[index]) >= MIN_VISIBILITY]
    if not visible:
        score = min(visibility(landmarks[index]) for index in indices)
        return None, score, f"{label} landmarks below visibility {MIN_VISIBILITY:.2f}"
    position = sum(float(point.y) for point in visible) / len(visible)
    confidence = min(visibility(point) for point in visible)
    return position, confidence, label


class ExerciseCounter:
    """A complete low-angle then extended-angle cycle increments one repetition."""
    CONFIG = {
        "squat": (115.0, 155.0),
        "pushup": (95.0, 155.0),
        "pullup": (100.0, 150.0),
    }
    MAX_MISSING_FRAMES = 300  # 10 seconds at 30 fps; retain a known phase across occlusion.

    def __init__(self, exercise):
        if exercise not in self.CONFIG:
            raise ValueError(f"Unknown exercise: {exercise}")
        self.exercise = exercise
        self.reps = 0
        self.phase = "UNKNOWN"
        self.down_frames = self.up_frames = 0
        self.smoothed_angle = None
        self.smoothed_movement = None
        self.movement_baseline = None
        self.body_descent_seen = False
        self.position_calibration_frames = 0
        self.missing_frames = 0
        self.last_reason = "waiting_for_extended_pose"

    def update(self, angle, movement=None):
        if angle is None and movement is None:
            self.down_frames = self.up_frames = 0
            self.smoothed_angle = None
            self.smoothed_movement = None
            self.missing_frames += 1
            if (self.exercise == "pullup" or self.phase == "UNKNOWN"
                    or self.missing_frames >= self.MAX_MISSING_FRAMES):
                self.phase = "UNKNOWN"
                if self.missing_frames >= self.MAX_MISSING_FRAMES:
                    self.movement_baseline = None
                    self.position_calibration_frames = 0
            self.last_reason = (
                "awaiting_first_visible_extended_pose" if self.phase == "UNKNOWN"
                else f"no_measurement; preserving {self.phase} through occlusion"
            )
            return False
        self.missing_frames = 0
        if angle is not None:
            self.smoothed_angle = angle if self.smoothed_angle is None else (
                0.4 * angle + 0.6 * self.smoothed_angle
            )
        if movement is not None:
            self.smoothed_movement = movement if self.smoothed_movement is None else (
                0.4 * movement + 0.6 * self.smoothed_movement
            )
        down, up = self.CONFIG[self.exercise]
        movement_sign = -1.0 if self.exercise == "pullup" else 1.0
        if (self.phase == "UP" and self.movement_baseline is None
                and self.smoothed_movement is not None):
            self.movement_baseline = self.smoothed_movement
        movement_delta = None
        if self.smoothed_movement is not None and self.movement_baseline is not None:
            movement_delta = movement_sign * (self.smoothed_movement - self.movement_baseline)
        if movement_delta is not None and movement_delta >= BODY_DOWN_DELTA:
            self.body_descent_seen = True
        if self.phase == "UNKNOWN":
            angle_calibrated = self.smoothed_angle is not None and self.smoothed_angle >= up
            if angle_calibrated:
                self.phase = "UP"
                if self.smoothed_movement is not None:
                    self.movement_baseline = self.smoothed_movement
                self.last_reason = "extended angle calibrated"
            elif self.smoothed_angle is None and self.smoothed_movement is not None:
                if self.movement_baseline is None:
                    self.movement_baseline = self.smoothed_movement
                    self.position_calibration_frames = 1
                else:
                    self.position_calibration_frames += 1
                if self.position_calibration_frames >= STABLE_FRAMES:
                    self.phase = "UP"
                    self.last_reason = "body position baseline calibrated"
                else:
                    self.last_reason = f"calibrating body position ({self.position_calibration_frames}/{STABLE_FRAMES})"
            else:
                self.last_reason = (
                    f"waiting for extended angle>={up:.0f}; body-only calibration requires no angle"
                    if self.smoothed_angle is not None
                    else f"waiting for extended angle>={up:.0f} or visible body position"
                )
            return False
        if self.phase == "UP":
            angle_down = self.smoothed_angle is not None and self.smoothed_angle <= down
            position_down = movement_delta is not None and movement_delta >= BODY_DOWN_DELTA
            self.down_frames = self.down_frames + 1 if angle_down or position_down else 0
            if self.down_frames >= STABLE_FRAMES:
                self.phase, self.down_frames = "DOWN", 0
                self.last_reason = "down transition accepted by " + (
                    "body movement" if position_down and not angle_down else "joint angle/body movement"
                )
            else:
                signal = "body movement" if self.smoothed_angle is None else f"angle<={down:.0f} or body movement"
                self.last_reason = f"waiting for down {signal} ({self.down_frames}/{STABLE_FRAMES})"
            if self.down_frames == 0 and self.smoothed_movement is not None:
                self.movement_baseline = 0.98 * self.movement_baseline + 0.02 * self.smoothed_movement
        else:
            angle_up = self.smoothed_angle is not None and self.smoothed_angle >= up
            position_up = (self.body_descent_seen and movement_delta is not None
                           and movement_delta <= BODY_RETURN_DELTA)
            self.up_frames = self.up_frames + 1 if angle_up or position_up else 0
            if self.up_frames >= STABLE_FRAMES:
                self.phase, self.up_frames = "UP", 0
                self.reps += 1
                self.last_reason = "full cycle counted by " + (
                    "body movement" if position_up and not angle_up else "joint angle/body movement"
                )
                if self.smoothed_movement is not None:
                    self.movement_baseline = self.smoothed_movement
                self.body_descent_seen = False
                return True
            signal = "body movement" if self.smoothed_angle is None else f"angle>={up:.0f} or body return"
            self.last_reason = f"waiting for up {signal} ({self.up_frames}/{STABLE_FRAMES})"
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

