"""Behavior and replay checks for the causal squat detector."""

from __future__ import annotations

import math
import unittest

from backend.streaming_squat_detector import StreamingConfig, StreamingSquatDetector


ANCHORS = ((0, 0), (2, 0), (3, 18), (3.3, 18), (4.2, 0),
           (5, 0), (6, 18), (6.3, 18), (7.2, 0),
           (8, 0), (9, 18), (9.3, 18), (10.2, 0), (12, 0))


def angle_at(t: float, anchors=ANCHORS) -> float:
    for (left, a), (right, b) in zip(anchors, anchors[1:]):
        if left <= t <= right:
            return a + (b - a) * (t - left) / (right - left)
    return anchors[-1][1]


def samples(sign: int = 1, anchors=ANCHORS) -> list[dict]:
    rows = []
    for index in range(round(max(12, anchors[-1][0]) * 50)):
        t = index * 0.02
        angle = sign * angle_at(t, anchors)
        previous = sign * angle_at(max(0.0, t - 0.02), anchors)
        radians = math.radians(angle)
        rows.append({
            "sample_time_seconds": t, "sample_t": index * 20,
            "ax": 9.81 * math.sin(radians), "ay": -9.81 * math.cos(radians),
            "az": 0.0, "gx": 0.0, "gy": 0.0,
            "gz": -(angle - previous) / 0.02,
        })
    return rows


class StreamingSquatDetectorTests(unittest.TestCase):
    def test_auto_orientation_and_full_cycles(self) -> None:
        for sign, expected in ((1, "positive"), (-1, "negative")):
            detector = StreamingSquatDetector("session")
            rows = samples(sign)
            events = [event for row in rows for event in detector.process_sample(row)]
            with self.subTest(sign=sign):
                self.assertEqual(len(events), 3)
                self.assertEqual([event.repetition for event in events], [1, 2, 3])
                self.assertEqual([event.orientation for event in events], [expected] * 3)
                self.assertEqual([event.session_id for event in events], ["session"] * 3)
                self.assertTrue(all(event.completed_time_seconds >= event.bottom_time_seconds
                                    for event in events))
                self.assertEqual(detector.get_state()["calibration"], "ready")

    def test_batch_boundaries_and_duplicate_batches(self) -> None:
        rows = samples()
        baseline = StreamingSquatDetector("session")
        expected = baseline.process_batch(rows, batch_id="whole")
        for batch_size in (1, 37, 50, 83):
            detector = StreamingSquatDetector("session")
            events = []
            for start in range(0, len(rows), batch_size):
                chunk = rows[start:start + batch_size]
                identity = f"cin-{start}"
                events.extend(detector.process_batch(chunk, batch_id=identity))
                self.assertEqual(detector.process_batch(chunk, batch_id=identity), [])
            with self.subTest(batch_size=batch_size):
                self.assertEqual(events, expected)
                self.assertEqual(detector.get_state()["diagnostics"]["duplicate_batches"],
                                 math.ceil(len(rows) / batch_size))
                self.assertEqual(detector.get_state()["diagnostics"]["processed_samples"],
                                 len(rows))

    def test_duplicate_samples_and_out_of_order_are_ignored(self) -> None:
        rows = samples()
        detector = StreamingSquatDetector("session")
        detector.process_batch(rows[:120])
        detector.process_sample(rows[119])  # Exact timestamp duplicate.
        late_old = dict(rows[118], sample_time_seconds=2.35)
        detector.process_sample(late_old)
        self.assertEqual(detector.get_state()["diagnostics"]["duplicate_samples"], 1)
        self.assertEqual(detector.get_state()["diagnostics"]["out_of_order"], 1)
        events = detector.process_batch(rows[120:])
        self.assertEqual(len(events), 3)

    def test_batch_identity_conflict_and_invalid_batch_are_atomic(self) -> None:
        rows = samples()
        detector = StreamingSquatDetector("session")
        detector.process_batch(rows[:50], batch_id="cin-1")
        with self.assertRaisesRegex(ValueError, "conflicting content"):
            detector.process_batch([*rows[:49], rows[50]], batch_id="cin-1")
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            detector.process_batch([rows[50], dict(rows[51], ax=float("nan"))],
                                   batch_id="cin-2")
        self.assertEqual(detector.get_state()["diagnostics"]["processed_samples"], 50)
        self.assertEqual(detector.get_state()["diagnostics"]["invalid_samples"], 1)

    def test_gap_aborts_incomplete_cycle_without_fabricating_samples(self) -> None:
        rows = samples()
        detector = StreamingSquatDetector("session")
        events = detector.process_batch(rows[:160])
        self.assertEqual(events, [])
        # Skip 100 ms while the first cycle is in progress.
        events.extend(detector.process_batch(rows[165:]))
        self.assertEqual(len(events), 2)
        self.assertEqual(detector.get_state()["diagnostics"]["gaps"], 1)
        self.assertEqual(detector.get_state()["diagnostics"]["aborted_cycles"], 1)

    def test_sensor_clock_reset_recalibrates_and_preserves_count(self) -> None:
        detector = StreamingSquatDetector("session")
        rows = samples()
        detector.process_batch(rows[:220])
        self.assertEqual(detector.get_state()["repetitions"], 1)
        reset_row = dict(rows[0])
        detector.process_sample(reset_row)
        state = detector.get_state()
        self.assertEqual(state["diagnostics"]["sensor_resets"], 1)
        self.assertEqual(state["repetitions"], 1)
        self.assertEqual(state["calibration"], "warming_up")
        self.assertEqual(detector.process_batch(rows[1:50]), [])

    def test_partial_lean_and_prolonged_hold_do_not_count(self) -> None:
        anchors = ((0, 0), (2, 0), (3, 7), (4, 0),
                   (5, 0), (6, 18), (11, 18), (12, 0))
        detector = StreamingSquatDetector("session")
        events = detector.process_batch(samples(anchors=anchors))
        self.assertEqual(events, [])
        self.assertGreaterEqual(detector.get_state()["diagnostics"]["rejected_cycles"], 1)

    def test_rest_movements_and_short_gaps_have_no_false_reps(self) -> None:
        anchors = ((0, 0), (3, 0), (4, 6), (5, 0),
                   (10, 0), (11, -7), (12, 0),
                   (15, 0), (16, 18), (23, 18), (24, 0))
        detector = StreamingSquatDetector("rest")
        rows = samples(anchors=anchors)
        rows = rows[:300] + rows[304:]
        self.assertEqual(detector.process_batch(rows), [])
        self.assertEqual(detector.get_state()["repetitions"], 0)
        self.assertEqual(detector.get_state()["diagnostics"]["gaps"], 1)

    def test_warmup_uncertainty_and_manual_orientation(self) -> None:
        detector = StreamingSquatDetector(
            "session", StreamingConfig(orientation="positive", gyro_axis="gz",
                                       gyro_sign=-1))
        rows = samples()
        self.assertEqual(detector.process_batch(rows[:20]), [])
        self.assertEqual(detector.get_state()["calibration"], "warming_up")
        self.assertEqual(detector.get_state()["orientation"], "positive")
        self.assertEqual(len(detector.process_batch(rows[20:])), 3)
        detector.reset_session("new-session")
        self.assertEqual(detector.get_state()["repetitions"], 0)
        self.assertEqual(detector.get_state()["session_id"], "new-session")

    def test_invalid_sample_rejected(self) -> None:
        detector = StreamingSquatDetector("session")
        row = samples()[0]
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            detector.process_sample(dict(row, ax=float("nan")))
        self.assertEqual(detector.get_state()["diagnostics"]["invalid_samples"], 1)


if __name__ == "__main__":
    unittest.main()
