"""Behavior checks for the offline squat state machine and CSV validation."""

from __future__ import annotations

import csv
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from detect_squats import (  # noqa: E402
    Repetition, Thresholds, adaptive_tilt, choose_orientation, detect_repetitions,
    filtered_signals, gyro_direction, keep_consistent_bouts, quiet_reference,
    main, read_csv,
)


class SquatDetectorTests(unittest.TestCase):
    def assert_zero_rep_recording(self, angles: np.ndarray) -> None:
        time = np.arange(len(angles)) * 0.02
        radians = np.deg2rad(angles)
        acceleration = np.column_stack((9.81 * np.sin(radians),
                                        -9.81 * np.cos(radians),
                                        np.zeros_like(time)))
        gyro_z = -np.gradient(angles, time)
        gyroscope = np.column_stack((np.zeros_like(time), np.zeros_like(time), gyro_z))
        csv_path = Path("quiet.csv")
        output_dir = Path("plots")
        output = io.StringIO()
        with (patch.object(sys, "argv", ["detect_squats.py", str(csv_path),
                                        "--output-dir", str(output_dir)]),
              patch("detect_squats.read_csv", return_value=(time, acceleration, gyroscope, 50)),
              patch("detect_squats.plot_detection") as graph,
              patch.object(Path, "mkdir"), redirect_stdout(output)):
            main()
        self.assertIn("Orientation: undetermined; no qualifying motion bout", output.getvalue())
        self.assertIn("Detected repetitions: 0", output.getvalue())
        self.assertEqual(graph.call_args.args[0], output_dir / "quiet_squat_detection.png")
        self.assertEqual(graph.call_args.args[4], [])
        self.assertEqual(graph.call_args.args[6], "undetermined")

    def test_stationary_recording_has_zero_repetitions(self) -> None:
        self.assert_zero_rep_recording(np.full(1000, 12.0))

    def test_isolated_upper_body_movements_have_zero_repetitions(self) -> None:
        time = np.arange(0, 24, 0.02)
        angles = np.interp(time, [0, 4, 5, 6, 12, 13, 14, 24],
                           [12, 12, 34, 12, 12, -9, 12, 12])
        self.assert_zero_rep_recording(angles)

    def test_auto_orientation_for_positive_and_negative_cycles(self) -> None:
        time = np.arange(0, 28, 0.02)
        pulse_times = [0, 1, 2, 2.25, 2.8, 4, 5, 5.25, 5.8,
                       7, 8, 8.25, 8.8, 10, 11, 11.25, 11.8,
                       14, 14.5, 15, 16, 16.5, 26.5, 27.5, 28]
        pulse_angles = [0, 0, 17, 17, 0, 0, 18, 18, 0,
                        0, 16, 16, 0, 0, 19, 19, 0,
                        0, 7, 0, 0, 20, 20, 0, 0]
        pulses = np.interp(time, pulse_times, pulse_angles)
        for expected_sign, orientation in ((1, "positive"), (-1, "negative")):
            # Slow mounting drift and an unsettled initial posture are present.
            raw_angle = 12 + 0.15 * time + expected_sign * pulses
            radians = np.deg2rad(raw_angle)
            acceleration = np.column_stack((9.81 * np.sin(radians),
                                            -9.81 * np.cos(radians),
                                            np.zeros_like(time)))
            gyro = np.column_stack((np.zeros_like(time), np.zeros_like(time),
                                    -np.gradient(raw_angle, time)))
            config = Thresholds()
            raw_tilt, smooth_gyro = filtered_signals(acceleration, gyro, 50, config)
            reference = quiet_reference(time, raw_tilt, smooth_gyro, config)
            chosen_sign, bouts = choose_orientation(time, raw_tilt, reference, 50, config)
            axis, gyro_sign, _ = gyro_direction(time, raw_tilt, smooth_gyro, config)
            tilt, _ = adaptive_tilt(time, raw_tilt, chosen_sign, 50, config)
            candidates, _ = detect_repetitions(
                time, tilt, chosen_sign * gyro_sign * smooth_gyro[:, axis], config)
            repetitions, _ = keep_consistent_bouts(candidates, bouts, time)

            with self.subTest(orientation=orientation):
                self.assertEqual(chosen_sign, expected_sign)
                self.assertEqual(axis, 2)
                self.assertEqual(gyro_sign, -1)
                self.assertEqual(len(repetitions), 4)

    def test_complete_cycles_only_and_no_double_count(self) -> None:
        time = np.arange(0, 18, 0.02)
        # Two complete cycles, a shallow lean, and a long held lean.
        anchors_t = [0, 1, 1.5, 1.7, 2.3, 3.3, 3.8, 4.0, 4.6,
                     6, 6.4, 7, 8, 8.5, 15.5, 16, 18]
        anchors_angle = [0, 0, 17, 17, 0, 0, 16, 16, 0,
                         0, 7, 0, 0, 22, 22, 0, 0]
        tilt = np.interp(time, anchors_t, anchors_angle)
        gyro = np.gradient(tilt, time)

        reps, rejected = detect_repetitions(time, tilt, gyro, Thresholds())

        self.assertEqual(len(reps), 2)
        self.assertAlmostEqual(reps[0].completed, 2.2, delta=0.2)
        self.assertAlmostEqual(reps[1].completed, 4.5, delta=0.2)
        self.assertTrue(any("exceeded" in reason for reason in rejected))

    def test_weak_final_cycle_inside_bout_but_not_isolated_motion(self) -> None:
        time = np.arange(0, 28, 0.02)
        anchors_t = [0, 1, 2, 2.2, 2.8, 4, 5, 5.2, 5.8,
                     7, 8, 8.2, 8.8, 10, 11, 11.2, 11.8,
                     14, 15, 15.2, 15.8, 17, 18, 25, 26, 28]
        anchors_angle = [0, 0, 18, 18, 0, 0, 18, 18, 0,
                         0, 18, 18, 0, 0, 18, 18, 0,
                         0, 18, 18, 0, 0, 22, 22, 0, 0]
        tilt = np.interp(time, anchors_t, anchors_angle)
        gyro = np.gradient(tilt, time)
        gyro[(time >= 10) & (time < 12)] *= 0.2  # Both directions remain present.
        config = Thresholds()
        peak_times = (2.1, 5.1, 8.1, 11.1)

        candidates, rejected = detect_repetitions(
            time, tilt, gyro, config, bout_peak_times=peak_times,
            established_peak_times=peak_times[2:], oriented_raw_tilt=tilt)
        self.assertEqual(len(candidates), 5)  # Four in-bout cycles plus an isolated cycle.
        self.assertEqual(candidates[3].evidence, "weak-gyro bout fallback")
        self.assertTrue(any("exceeded" in reason for reason in rejected))

        peak_indices = [int(np.searchsorted(time, peak)) for peak in peak_times]
        kept, isolated = keep_consistent_bouts(candidates, [peak_indices], time)
        self.assertEqual(len(kept), 4)
        self.assertTrue(any("isolated" in reason for reason in isolated))

        no_bout, _ = detect_repetitions(
            time, tilt, gyro, config, oriented_raw_tilt=tilt)
        self.assertEqual(len(no_bout), 4)  # Weak cycle is not granted a global exception.

    def test_weak_gyro_fallback_requires_raw_tilt_return(self) -> None:
        time = np.arange(0, 5, 0.02)
        tilt = np.interp(time, [0, 1, 2, 2.2, 3, 5], [0, 0, 18, 18, 0, 0])
        gyro = np.gradient(tilt, time) * 0.2
        raw_tilt = np.interp(time, [0, 1, 2, 2.2, 3, 5], [0, 0, 10, 12, 14, 14])

        repetitions, rejected = detect_repetitions(
            time, tilt, gyro, Thresholds(), bout_peak_times=(2.1,),
            established_peak_times=(2.1,), oriented_raw_tilt=raw_tilt)

        self.assertEqual(repetitions, [])
        self.assertTrue(any("no raw tilt reversal" in reason for reason in rejected))

    def test_bout_peak_after_early_turn_matches_same_complete_cycle(self) -> None:
        time = np.arange(0, 38, 0.02)
        peak_times = (3.28, 7.84, 12.76, 15.56, 19.08)
        peak_indices = [int(round(peak / 0.02)) for peak in peak_times]
        cycles = [
            Repetition(2.08, 3.28, 4.86, 25.64),
            Repetition(6.60, 7.84, 9.68, 27.37),
            # The first turn occurs at 11.72 s; a stronger peak follows before standing.
            Repetition(11.26, 11.72, 13.66, 15.65),
            Repetition(14.84, 15.54, 16.10, 10.95),
            Repetition(18.26, 19.12, 21.12, 18.77),
            Repetition(28.66, 29.18, 30.12, 25.02),
        ]

        kept, rejected = keep_consistent_bouts(cycles, [peak_indices], time)

        self.assertEqual(kept, cycles[:5])
        self.assertEqual(len(rejected), 1)
        self.assertIn("28.66s: isolated", rejected[0])

        short_window, _ = keep_consistent_bouts(
            cycles, [peak_indices], time,
            Thresholds(bout_peak_rebound_seconds=1.0))
        self.assertNotIn(cycles[2], short_window)

    def test_bout_peak_after_cycle_completion_does_not_match(self) -> None:
        time = np.arange(0, 15, 0.02)
        cycle = Repetition(10.0, 11.0, 12.0, 15.0)
        peak_index = int(round(12.2 / 0.02))

        kept, rejected = keep_consistent_bouts([cycle], [[peak_index]], time)

        self.assertEqual(kept, [])
        self.assertEqual(len(rejected), 1)

    def test_timestamp_gap_is_rejected(self) -> None:
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(("sample_time_seconds", "ax", "ay", "az", "gx", "gy", "gz"))
        for index in range(12):
            timestamp = index * 0.02 + (0.10 if index >= 6 else 0)
            writer.writerow((timestamp, 0, -9.8, 0, 0, 0, 0))
        with patch.object(Path, "open", return_value=io.StringIO(stream.getvalue())):
            with self.assertRaisesRegex(ValueError, "Timestamp gap"):
                read_csv(Path("recording.csv"), max_gap_ms=40)

    def test_missing_columns_are_rejected(self) -> None:
        content = "sample_time_seconds,ax,ay,az,gx,gy\n" + "0,0,-9.8,0,0,0\n" * 12
        with patch.object(Path, "open", return_value=io.StringIO(content)):
            with self.assertRaisesRegex(ValueError, "Missing CSV columns: gz"):
                read_csv(Path("recording.csv"), max_gap_ms=40)

    def test_nonincreasing_timestamps_are_rejected(self) -> None:
        content = "sample_time_seconds,ax,ay,az,gx,gy,gz\n"
        content += "".join(f"{index * 0.02},0,-9.8,0,0,0,0\n" for index in range(11))
        content += "0.20,0,-9.8,0,0,0,0\n"
        with patch.object(Path, "open", return_value=io.StringIO(content)):
            with self.assertRaisesRegex(ValueError, "Timestamps are not strictly increasing"):
                read_csv(Path("recording.csv"), max_gap_ms=40)

    def test_nonfinite_values_are_rejected(self) -> None:
        content = "sample_time_seconds,ax,ay,az,gx,gy,gz\n"
        content += "0.00,nan,-9.8,0,0,0,0\n"
        content += "0.02,0,-9.8,0,0,0,0\n" * 11
        with patch.object(Path, "open", return_value=io.StringIO(content)):
            with self.assertRaisesRegex(ValueError, "Non-finite number"):
                read_csv(Path("recording.csv"), max_gap_ms=40)

    def test_unusable_accelerometer_signal_is_rejected(self) -> None:
        content = "sample_time_seconds,ax,ay,az,gx,gy,gz\n"
        content += "".join(f"{index * 0.02},0,0,0,0,0,0\n" for index in range(20))
        with patch.object(Path, "open", return_value=io.StringIO(content)):
            with self.assertRaisesRegex(ValueError, "Accelerometer X/Y signal is unusable"):
                read_csv(Path("recording.csv"), max_gap_ms=40)


if __name__ == "__main__":
    unittest.main()
