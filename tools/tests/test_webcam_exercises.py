import unittest
from types import SimpleNamespace

from tools.webcam_exercises import (CindyWorkout, ExerciseCounter, body_movement_measurement,
                                    exercise_measurement,
                                    pullup_auxiliary_measurement, select_exercise_key)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def pose_points():
    """Landmark stubs with a straight, side-on body and visible joints."""
    points = [SimpleNamespace(x=0.5, y=0.5, visibility=0.95) for _ in range(33)]
    # Straight body, arms extended; synthetic tests only exercise coordinate geometry.
    for i, p in {11: (0.40, .30), 13: (.40, .40), 15: (.40, .50),
                 23: (.45, .55), 27: (.50, .90), 12: (.60, .30),
                 14: (.60, .40), 16: (.60, .50), 24: (.55, .55), 28: (.50, .90)}.items():
        points[i].x, points[i].y = p
    return points


def arm_pose(elbow_angle, alignment_visible=True, vertical_shift=0.0):
    points = [SimpleNamespace(x=0.0, y=0.0, visibility=0.0) for _ in range(33)]
    # Pixel-space side view: shoulder/hip/ankle are aligned for a push-up.
    sx, sy, ex, ey = 220.0, 120.0, 270.0, 120.0
    radians = __import__("math").radians(180.0 - elbow_angle)
    values = {
        11: (sx, sy), 13: (ex, ey),
        15: (ex + 50.0 * __import__("math").cos(radians),
             ey + 50.0 * __import__("math").sin(radians)),
        23: (320.0, 170.0), 27: (520.0, 270.0),
    }
    for index, (x, y) in values.items():
        points[index].x, points[index].y = x / 640.0, (y + vertical_shift) / 480.0
        points[index].visibility = 0.95 if index not in (23, 27) or alignment_visible else 0.1
    return points


def pullup_pose(elbow_angle, visible=True, head_visible=True, wrist_overhead=True):
    import math
    points = [SimpleNamespace(x=0.0, y=0.0, visibility=0.0) for _ in range(33)]
    rise = 60.0 if elbow_angle < 120 else 0.0
    shoulder = (220.0, 220.0 - rise)
    elbow = (220.0, 160.0 - rise)
    angle = math.radians(elbow_angle)
    wrist = (220.0 + 60.0 * math.sin(angle), 160.0 - rise + 60.0 * math.cos(angle))
    if not wrist_overhead:
        wrist = (wrist[0], shoulder[1] + 40.0)
    values = {0: (240.0, 190.0 - rise), 11: shoulder, 13: elbow,
              15: wrist, 23: (230.0, 350.0 - rise)}
    for index, (x, y) in values.items():
        points[index].x, points[index].y = x / 640.0, y / 480.0
        points[index].visibility = .95 if visible and (index != 0 or head_visible) else 0.1
    return points


def squat_pose(knee_angle):
    points = [SimpleNamespace(x=0.0, y=0.0, visibility=0.0) for _ in range(33)]
    import math
    hip_x, hip_y, knee_x, knee_y, length = 250.0, 120.0, 250.0, 220.0, 100.0
    radians = math.radians(knee_angle)
    ankle_x = knee_x + length * math.sin(radians)
    ankle_y = knee_y - length * math.cos(radians)
    for index, (x, y) in {23: (hip_x, hip_y), 25: (knee_x, knee_y),
                          27: (ankle_x, ankle_y)}.items():
        points[index].x, points[index].y = x / 640.0, y / 480.0
        points[index].visibility = 0.95
    return points


def body_only_pose(exercise, vertical_position):
    """Visible torso landmarks only; joint-angle landmarks are deliberately absent."""
    points = [SimpleNamespace(x=.5, y=.5, visibility=0.0) for _ in range(33)]
    indices = {"squat": (23, 24), "pushup": (11, 12),
               "pullup": (11, 12, 23, 24)}[exercise]
    for offset, index in enumerate(indices):
        points[index].x = .4 + .03 * offset
        points[index].y = vertical_position
        points[index].visibility = .95
    return points


def replay_three_cycles(exercise, pose_factory):
    counter = ExerciseCounter(exercise)
    detections = 0
    for _ in range(3):
        for angle in (170.0,) * 8 + (80.0,) * 12 + (170.0,) * 12:
            points = pose_factory(angle)
            measured, confidence, side = exercise_measurement(exercise, points, 640, 480)
            movement, _, _ = body_movement_measurement(exercise, points)
            detections += counter.update(measured, movement)
    return counter, detections


class ExerciseCounterTests(unittest.TestCase):
    def test_requires_full_cycle_and_stable_transitions(self):
        counter = ExerciseCounter("squat")
        self.assertFalse(counter.update(170))  # standing calibration
        for _ in range(6):
            counter.update(100)
        self.assertEqual(counter.phase, "DOWN")
        counted = False
        for _ in range(6):
            counted = counter.update(170) or counted
        self.assertTrue(counted)
        self.assertEqual(counter.reps, 1)
        for _ in range(8):
            self.assertFalse(counter.update(170))
        self.assertEqual(counter.reps, 1)

    def test_tracking_loss_requires_recalibration(self):
        counter = ExerciseCounter("pushup")
        counter.update(170)
        for _ in range(3):
            counter.update(80)
        for _ in range(ExerciseCounter.MAX_MISSING_FRAMES):
            counter.update(None)
        self.assertEqual(counter.phase, "UNKNOWN")
        self.assertFalse(counter.update(80))
        self.assertEqual(counter.reps, 0)

    def test_occlusion_aborts_incomplete_repetition(self):
        counter = ExerciseCounter("pushup")
        counter.update(170, .40)
        for _ in range(12):
            counter.update(80, .50)
        self.assertEqual(counter.phase, "DOWN")
        for _ in range(ExerciseCounter.MAX_MISSING_FRAMES):
            counter.update(None, .50)
        self.assertEqual(counter.phase, "UNKNOWN")
        for _ in range(12):
            self.assertFalse(counter.update(170, .40))
        self.assertEqual(counter.reps, 0)

    def test_three_cycles_for_each_exercise_from_synthetic_landmarks(self):
        pushups, pushup_count = replay_three_cycles(
            "pushup", lambda a: arm_pose(a, vertical_shift=60.0 if a < 100 else 0.0)
        )
        pullups, pullup_count = replay_three_cycles("pullup", pullup_pose)
        squats, squat_count = replay_three_cycles("squat", squat_pose)
        self.assertEqual((pullups.reps, pullup_count), (3, 3))
        self.assertEqual((pushups.reps, pushup_count), (3, 3))
        self.assertEqual((squats.reps, squat_count), (3, 3))

    def test_pushup_rejects_occluded_body_alignment(self):
        angle, confidence, side, reason = exercise_measurement(
            "pushup", arm_pose(170, alignment_visible=False), 640, 480, include_reason=True
        )
        self.assertIsNone(angle)
        self.assertIn("visibility", reason)

    def test_body_position_alone_never_counts(self):
        for exercise in ("pullup", "pushup", "squat"):
            with self.subTest(exercise=exercise):
                counter = ExerciseCounter(exercise)
                counted = 0
                baseline, movement = ((.56, .4) if exercise == "pullup" else (.4, .56))
                sequence = [baseline] * 5 + [movement] * 10 + [baseline] * 10
                sequence += ([movement] * 10 + [baseline] * 10) * 2
                for position in sequence:
                    points = body_only_pose(exercise, position)
                    angle, _, _ = exercise_measurement(exercise, points, 640, 480)
                    movement, visibility, _ = body_movement_measurement(exercise, points)
                    self.assertIsNone(angle)
                    self.assertGreaterEqual(visibility, .9)
                    counted += counter.update(angle, movement)
                self.assertEqual(counted, 0)
                self.assertEqual(counter.reps, 0)

    def test_stale_body_position_cannot_complete_an_angle_only_down_transition(self):
        counter = ExerciseCounter("pushup")
        counter.update(170.0, .40)
        for _ in range(12):
            counter.update(80.0, .40)
        self.assertEqual(counter.phase, "UP")
        # The elbow signal becomes occluded while the shoulder position remains
        # unchanged. That is not evidence that the body returned to the top.
        for _ in range(20):
            counter.update(None, .40)
        self.assertEqual(counter.reps, 0)
        self.assertEqual(counter.phase, "UNKNOWN")

    def test_bent_joint_cannot_be_calibrated_as_body_only_starting_position(self):
        counter = ExerciseCounter("pushup")
        for _ in range(10):
            counter.update(25.0, .72)
        self.assertEqual(counter.phase, "UNKNOWN")
        self.assertEqual(counter.reps, 0)
        for _ in range(10):
            counter.update(170.0, .72)
        self.assertEqual(counter.phase, "UP")

    def test_pushup_uses_elbow_and_alignment(self):
        angle, confidence, side = exercise_measurement("pushup", pose_points(), 640, 480)
        self.assertAlmostEqual(angle, 180.0)
        self.assertGreaterEqual(confidence, 0.9)
        self.assertIn(side, ("LEFT", "RIGHT"))

    def test_pullup_head_and_wrist_image_height_are_not_hard_gates(self):
        for points in (pullup_pose(80, head_visible=False), pullup_pose(80, wrist_overhead=False)):
            with self.subTest(head_visible=points[0].visibility):
                angle, _, _, reason = exercise_measurement("pullup", points, 640, 480, include_reason=True)
                self.assertIsNotNone(angle)
                self.assertTrue(reason)
        head, lateral = pullup_auxiliary_measurement(pullup_pose(80, head_visible=False))
        self.assertIsNone(head)
        self.assertIsNone(lateral)  # One shoulder is visible; no reliable swing midpoint.

    def test_partial_motion_and_missing_elbow_do_not_count(self):
        for exercise in ("pullup", "pushup", "squat"):
            with self.subTest(exercise=exercise):
                counter = ExerciseCounter(exercise)
                factory = {"pullup": pullup_pose,
                           "pushup": lambda a: arm_pose(a, vertical_shift=60 if a < 100 else 0),
                           "squat": squat_pose}[exercise]
                for angle in (170.0,) * 8 + (80.0,) * 10:
                    points = factory(angle)
                    measured, _, _ = exercise_measurement(exercise, points, 640, 480)
                    movement, _, _ = body_movement_measurement(exercise, points)
                    self.assertFalse(counter.update(measured, movement))
                for _ in range(ExerciseCounter.MAX_MISSING_FRAMES):
                    counter.update(None, None)
                for _ in range(10):
                    self.assertFalse(counter.update(170, .4))
                self.assertEqual(counter.reps, 0)

    def test_mode_rearm_keeps_accumulated_count(self):
        counter = ExerciseCounter("squat")
        for angle in (170,) * 8 + (80,) * 10 + (170,) * 12:
            counter.update(angle)
        self.assertEqual(counter.reps, 1)
        counter.reset_phase()
        self.assertEqual(counter.phase, "UNKNOWN")
        self.assertEqual(counter.reps, 1)

    def test_arm_flex_without_torso_rise_is_not_pullup(self):
        counter = ExerciseCounter("pullup")
        for angle in (170,) * 8 + (80,) * 12 + (170,) * 12:
            counter.update(angle, .55)
        self.assertEqual(counter.reps, 0)

    def test_pullup_variation_and_brief_occlusion_count_once(self):
        counter = ExerciseCounter("pullup")
        detections = 0
        for _ in range(8):
            detections += counter.update(160, .55)
        for frame in range(12):
            if frame in (5, 6):
                detections += counter.update(None, None)
            else:
                detections += counter.update(112, .47)
        for _ in range(16):
            detections += counter.update(150, .55)
        for _ in range(20):
            detections += counter.update(150, .55)
        self.assertEqual((counter.reps, detections), (1, 1))

    def test_five_controlled_pullups_count_exactly_five(self):
        counter = ExerciseCounter("pullup")
        detections = 0
        for _ in range(5):
            for angle, position, frames in ((170, .55, 8), (85, .42, 12), (170, .55, 14)):
                for _ in range(frames):
                    detections += counter.update(angle, position)
        self.assertEqual((counter.reps, detections), (5, 5))

    def test_pullup_partial_and_lateral_swing_are_rejected_with_diagnostics(self):
        partial = ExerciseCounter("pullup")
        for angle, position in [(170, .55)] * 8 + [(132, .47)] * 12 + [(170, .55)] * 12:
            partial.update(angle, position)
        self.assertEqual(partial.reps, 0)
        swing = ExerciseCounter("pullup")
        for _ in range(8):
            swing.update(170, .55, .50, .40)
        for _ in range(12):
            swing.update(85, .42, .37, .55)
        for _ in range(12):
            swing.update(170, .55, .50, .40)
        self.assertEqual(swing.reps, 0)
        self.assertGreaterEqual(swing.rejected_candidates, 1)
        self.assertIn("swing", swing.last_rejection)

    def test_pullup_missing_head_and_hip_can_still_count_from_shoulder_motion(self):
        counter = ExerciseCounter("pullup")
        detected = 0
        for angle, position, frames in ((170, .55, 8), (85, .42, 12), (170, .55, 12)):
            for _ in range(frames):
                detected += counter.update(angle, position, head_position=None)
        self.assertEqual(detected, 1)

    def test_pullup_can_use_head_rise_when_shoulder_travel_is_small(self):
        counter = ExerciseCounter("pullup")
        detections = 0
        for angle, shoulder, head, frames in ((170, .55, .50, 8),
                                              (85, .54, .40, 12),
                                              (170, .55, .50, 12)):
            for _ in range(frames):
                detections += counter.update(angle, shoulder, head)
        self.assertEqual(detections, 1)

    def test_pushup_sixty_cycles_over_20min_frame_equivalent_with_baseline_drift(self):
        counter = ExerciseCounter("pushup")
        detected = 0
        for repetition in range(60):
            top = .35 + repetition * .003
            for angle, position, frames in ((170, top, 570), (80, top + .12, 10),
                                            (170, top + .025, 14)):
                for _ in range(frames):
                    detected += counter.update(angle, position)
        self.assertEqual((counter.reps, detected), (60, 60))
        self.assertEqual(counter.phase, "UP")

    def test_pushup_stalled_down_candidate_rearms_without_false_count(self):
        counter = ExerciseCounter("pushup")
        for angle, position, frames in ((170, .40, 8), (80, .52, 12), (170, .52, 30)):
            for _ in range(frames):
                counter.update(angle, position)
        self.assertEqual(counter.reps, 0)
        self.assertEqual(counter.phase, "UP")
        self.assertIn("insufficient torso return", counter.last_rejection)
        detections = 0
        for angle, position, frames in ((80, .64, 12), (170, .54, 14)):
            for _ in range(frames):
                detections += counter.update(angle, position)
        self.assertEqual((counter.reps, detections), (1, 1))

    def test_pushup_tracking_jump_does_not_complete_a_cycle(self):
        counter = ExerciseCounter("pushup")
        for _ in range(8):
            counter.update(170, .40)
        for _ in range(10):
            counter.update(80, .50)
        for _ in range(5):
            self.assertFalse(counter.update(170, .10))
        self.assertEqual(counter.reps, 0)
        self.assertIn("tracking jump", counter.last_reason)


class CindyWorkoutTests(unittest.TestCase):
    def test_targets_advance_round_and_timer(self):
        clock = FakeClock()
        workout = CindyWorkout(1200, clock)
        self.assertEqual(workout.current_exercise, "pullup")
        for _ in range(5):
            workout.record_rep("pullup")
        self.assertEqual(workout.current_exercise, "pushup")
        for _ in range(10):
            workout.record_rep("pushup")
        self.assertEqual(workout.current_exercise, "squat")
        for _ in range(15):
            workout.record_rep("squat")
        self.assertEqual(workout.rounds, 1)
        clock.now = 1200
        self.assertTrue(workout.finished)
        self.assertEqual(workout.record_rep("pullup"), 1)

    def test_manual_switch_preserves_progress(self):
        workout = CindyWorkout()
        for _ in range(3):
            workout.record_rep("pullup")
        workout.select("squat")
        self.assertEqual(workout.current_exercise, "squat")
        self.assertEqual(workout.progress["pullup"], 3)

    def test_keyboard_switch_changes_cindy_stage_and_detector_key(self):
        workout = CindyWorkout()
        active = workout.current_exercise
        detectors = {name: ExerciseCounter(name) for name in workout.EXERCISES}
        for key, expected in ((ord("2"), "pushup"), (ord("3"), "squat"), (ord("1"), "pullup")):
            selected = select_exercise_key(key, workout)
            if selected is not None:
                active = selected
            self.assertEqual(workout.current_exercise, expected)
            self.assertEqual(detectors[active].exercise, expected)


if __name__ == "__main__":
    unittest.main()
