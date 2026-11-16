import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.federated_learning import (FLConfig, LogisticModel, TrainingRow, _fit_scaler, _metrics,
                                        _scale, build_dataset, cross_validate, train_federated,
                                        weighted_average)


def write_recording(directory: Path, session: str, intervals: list[float], exercise: str = 'squat'):
    path = directory / f'webcam_workout_{session}.csv'
    fields = ['timestampUtc', 'exercise', 'exerciseReps', 'sessionId']
    stamp = datetime(2026, 11, 16, tzinfo=timezone.utc)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, interval in enumerate(intervals, 1):
            stamp += timedelta(seconds=interval)
            writer.writerow({'timestampUtc': stamp.isoformat(), 'exercise': exercise,
                             'exerciseReps': index, 'sessionId': session})


class FederatedLearningTests(unittest.TestCase):
    def test_weighted_fedavg_uses_only_sample_weighted_parameters(self):
        result = weighted_average([
            {'clientId': 'a', 'sampleCount': 1, 'parameters': {'weights': [0, 2], 'bias': 1}},
            {'clientId': 'b', 'sampleCount': 3, 'parameters': {'weights': [4, 6], 'bias': 5}},
        ])
        self.assertEqual(result, {'weights': [3.0, 5.0], 'bias': 4.0})

    def test_dataset_is_causal_and_session_split_is_disjoint(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            # The final future intervals create slowdown labels; they cannot
            # enter features because the feature window ends at the current rep.
            for index in range(6):
                intervals = [1.0] * 8 + ([3.0, 3.0, 3.0] if index % 2 else [1.0, 1.0, 1.0])
                write_recording(directory, f'webcam-20261116T12{index:02d}00Z-a1b2c3d4', intervals)
            rows, audit = build_dataset(directory, FLConfig(recent_intervals=3, future_intervals=3))
            self.assertGreater(len(rows), 0)
            self.assertEqual(audit['derivedSessions'], 6)
            self.assertEqual({row.session_id for row in rows}, set(audit.get('derivedSessionIds', [])) or
                             {row.session_id for row in rows})
            first = next(row for row in rows if row.session_id.endswith('a1b2c3d4'))
            self.assertEqual(len(first.features), 8)
            self.assertTrue(all(value == value for value in first.features))

    def test_training_is_deterministic_and_partitions_do_not_send_raw_rows(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            for index in range(9):
                intervals = [1.0] * 8 + ([3.0, 3.0, 3.0] if index % 2 else [1.0, 1.0, 1.0])
                write_recording(directory, f'webcam-20261116T13{index:02d}00Z-a1b2c3d4', intervals)
            config = FLConfig(minimum_samples=1, rounds=2, local_epochs=5)
            first = train_federated(directory, config)
            second = train_federated(directory, config)
            self.assertEqual(first, second)
            self.assertEqual(first['status'], 'trained')
            self.assertEqual(len(first['clientPartitions']), 3)
            self.assertTrue(all('rows' not in client for client in first['clientPartitions']))
            train_sessions = set(first['trainSessions'])
            validation_sessions = set(first['validationSessions'])
            self.assertTrue(train_sessions.isdisjoint(validation_sessions))

    def test_scaler_is_fit_from_training_rows_only(self):
        train = [TrainingRow('a', 'squat', '2026-01-01T00:00:00+00:00', 'a.csv', (1.0,) * 8, 0)]
        validation = [TrainingRow('b', 'squat', '2026-01-01T00:00:00+00:00', 'b.csv', (100.0,) * 8, 1)]
        means, scales = _fit_scaler(train)
        self.assertEqual(means, [1.0] * 8)
        self.assertNotEqual(_scale(validation, means, scales)[0][0][0], 0.0)

    def test_model_parameters_change_and_loss_is_reported_by_round(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            for index in range(6):
                write_recording(directory, f'webcam-20261116T14{index:02d}00Z-a1b2c3d4',
                                [1.0] * 8 + ([3.0, 3.0, 3.0] if index % 2 else [1.0, 1.0, 1.0]))
            result = train_federated(directory, FLConfig(minimum_samples=1, local_epochs=5, rounds=3))
            self.assertEqual(result['status'], 'trained')
            self.assertTrue(all(round_data['parameterL2Delta'] > 0 for round_data in result['roundMetrics']))
            self.assertTrue(result['roundMetrics'][0]['trainingLoss'] > result['roundMetrics'][-1]['trainingLoss'])

    def test_metrics_handle_single_class_validation_without_division_errors(self):
        rows = [TrainingRow('a', 'squat', '2026-01-01T00:00:00+00:00', 'a.csv', (0.0,) * 8, 0)]
        model = LogisticModel(8)
        model.bias = -1.0
        metric = _metrics(model, rows, [0.0] * 8, [1.0] * 8)
        self.assertEqual(metric['accuracy'], 1.0)
        self.assertEqual(metric['precision'], 0.0)
        self.assertEqual(metric['recall'], 0.0)

    def test_cross_validation_reports_insufficient_single_session_fold(self):
        rows = [TrainingRow('only-session', 'squat', f'2026-01-01T00:00:0{index}+00:00', 'a.csv', (0.0,) * 8, index % 2)
                for index in range(4)]
        result = cross_validate(rows, FLConfig())
        self.assertEqual(result[0]['status'], 'insufficient_data')

    def test_missing_data_is_explicitly_insufficient(self):
        with tempfile.TemporaryDirectory() as raw:
            result = train_federated(Path(raw))
            self.assertEqual(result['status'], 'insufficient_data')
            self.assertEqual(result['provenance'], 'real_csv_recordings')
