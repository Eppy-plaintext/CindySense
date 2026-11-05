import unittest
from types import SimpleNamespace

from tools.webcam_exercises import (CindyWorkout, ExerciseCounter, body_movement_measurement,
                                    exercise_measurement,
                                    select_exercise_key)


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


def arm_pose(elbow_angle, alignment_visible=True):
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
        points[index].x, points[index].y = x / 640.0, y / 480.0
        points[index].visibility = 0.95 if index not in (23, 27) or alignment_visible else 0.1
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
            if measured is not None:
                detections += counter.update(measured)
            else:
                counter.update(None)
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

    def test_brief_occlusion_does_not_erase_active_repetition_phase(self):
        counter = ExerciseCounter("pushup")
        counter.update(170)
        for _ in range(12):
            counter.update(80)
        self.assertEqual(counter.phase, "DOWN")
        for _ in range(30):
            counter.update(None)
        self.assertEqual(counter.phase, "DOWN")
        counted = False
        for _ in range(12):
            counted = counter.update(170) or counted
        self.assertTrue(counted)

    def test_three_pushups_and_squats_from_synthetic_landmark_cycles(self):
        pushups, pushup_count = replay_three_cycles(
            "pushup", lambda a: arm_pose(a, alignment_visible=(a >= 100.0))
        )
        squats, squat_count = replay_three_cycles("squat", squat_pose)
        self.assertEqual((pushups.reps, pushup_count), (3, 3))
        self.assertEqual((squats.reps, squat_count), (3, 3))

    def test_pushup_falls_back_to_elbow_when_body_alignment_is_occluded(self):
        angle, confidence, side, reason = exercise_measurement(
            "pushup", arm_pose(170, alignment_visible=False), 640, 480, include_reason=True
        )
        self.assertIsNotNone(angle)
        self.assertGreater(confidence, 0.9)
        self.assertIn("using elbow", reason)

    def test_full_body_position_alone_counts_three_cycles_for_each_exercise(self):
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
                self.assertEqual(counted, 3)
                self.assertEqual(counter.reps, 3)

    def test_stale_body_position_cannot_complete_an_angle_only_down_transition(self):
        counter = ExerciseCounter("pushup")
        counter.update(170.0, .40)
        for _ in range(12):
            counter.update(80.0, .40)
        self.assertEqual(counter.phase, "DOWN")
        # The elbow signal becomes occluded while the shoulder position remains
        # unchanged. That is not evidence that the body returned to the top.
        for _ in range(20):
            counter.update(None, .40)
        self.assertEqual(counter.reps, 0)
        self.assertEqual(counter.phase, "DOWN")

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
