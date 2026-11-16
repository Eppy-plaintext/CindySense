"""Offline, single-machine federated-learning proof of concept.

This module deliberately has no startup side effects and never writes to
TinyIoT. Three virtual clients hold disjoint workout-session partitions and
exchange only logistic-model parameters with the FedAvg coordinator.
"""

from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Iterable


EXERCISES = ('pullup', 'pushup', 'squat')
FEATURE_NAMES = (
    'exercise_pullup', 'exercise_pushup', 'exercise_squat',
    'elapsed_fraction', 'recent_interval_seconds', 'recent_interval_std',
    'relative_pace_change', 'last_interval_seconds',
)


@dataclass(frozen=True)
class FLConfig:
    recent_intervals: int = 3
    future_intervals: int = 3
    slowdown_ratio: float = 0.20
    minimum_samples: int = 12
    learning_rate: float = 0.08
    local_epochs: int = 60
    rounds: int = 4
    seed: int = 17


@dataclass(frozen=True)
class RepetitionPoint:
    session_id: str
    exercise: str
    timestamp: datetime
    repetition: int
    source_file: str


@dataclass(frozen=True)
class TrainingRow:
    session_id: str
    exercise: str
    timestamp: str
    source_file: str
    features: tuple[float, ...]
    label: int


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _number(row: dict[str, str], *names: str) -> float | None:
    for name in names:
        try:
            value = float(row.get(name, ''))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def _timestamp(row: dict[str, str]) -> datetime | None:
    value = row.get('timestampUtc') or row.get('timestamp')
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def audit_recordings(recordings_dir: str | Path) -> dict:
    """Return a read-only data-quality audit for webcam CSV recordings."""
    directory = Path(recordings_dir)
    files = sorted(directory.glob('webcam_workout_*.csv'))
    sessions: set[str] = set()
    exercises: set[str] = set()
    rows = 0
    usable_points = 0
    missing = {'sessionId': 0, 'timestampUtc': 0, 'exercise': 0, 'exerciseReps': 0}
    for path in files:
        with path.open(newline='', encoding='utf-8-sig') as handle:
            for item in csv.DictReader(handle):
                rows += 1
                session = item.get('sessionId', '').strip()
                exercise = item.get('exercise', '').strip()
                stamp = _timestamp(item)
                reps = _number(item, 'exerciseReps', 'repCount')
                if not session:
                    missing['sessionId'] += 1
                else:
                    sessions.add(session)
                if exercise not in EXERCISES:
                    missing['exercise'] += 1
                else:
                    exercises.add(exercise)
                if stamp is None:
                    missing['timestampUtc'] += 1
                if reps is None:
                    missing['exerciseReps'] += 1
                if session and exercise in EXERCISES and stamp is not None and reps is not None and reps > 0:
                    usable_points += 1
    return {
        'source': 'real_csv_recordings',
        'recordingPattern': 'data/recordings/webcam_workout_*.csv',
        'fileCount': len(files), 'rowCount': rows, 'uniqueWorkoutSessions': len(sessions),
        'exerciseTypes': sorted(exercises), 'usableFramePoints': usable_points,
        'missingValues': missing,
        'independentRealUsers': 1,
        'evaluationCaveat': 'Sessions are grouped for leakage control, but all recordings come from one real user.',
    }


def _load_points(recordings_dir: str | Path) -> list[RepetitionPoint]:
    grouped: dict[tuple[str, str], list[tuple[datetime, int, str]]] = {}
    for path in sorted(Path(recordings_dir).glob('webcam_workout_*.csv')):
        with path.open(newline='', encoding='utf-8-sig') as handle:
            for item in csv.DictReader(handle):
                session = item.get('sessionId', '').strip()
                exercise = item.get('exercise', '').strip()
                stamp = _timestamp(item)
                reps = _number(item, 'exerciseReps', 'repCount')
                if not session or exercise not in EXERCISES or stamp is None or reps is None or reps <= 0:
                    continue
                grouped.setdefault((session, exercise), []).append((stamp, int(reps), path.name))
    points: list[RepetitionPoint] = []
    for (session, exercise), values in grouped.items():
        previous = 0
        for stamp, reps, source_file in sorted(values):
            if reps > previous:
                points.append(RepetitionPoint(session, exercise, stamp, reps, source_file))
                previous = reps
    return sorted(points, key=lambda item: (item.session_id, item.exercise, item.timestamp, item.repetition))


def build_dataset(recordings_dir: str | Path, config: FLConfig = FLConfig()) -> tuple[list[TrainingRow], dict]:
    """Create causal features and future-window slowdown labels."""
    points = _load_points(recordings_dir)
    grouped: dict[tuple[str, str], list[RepetitionPoint]] = {}
    for point in points:
        grouped.setdefault((point.session_id, point.exercise), []).append(point)
    rows: list[TrainingRow] = []
    for (session, exercise), sequence in grouped.items():
        sequence.sort(key=lambda item: item.timestamp)
        if len(sequence) < config.recent_intervals + config.future_intervals + 1:
            continue
        start = sequence[0].timestamp
        for index in range(config.recent_intervals, len(sequence) - config.future_intervals):
            previous_times = [item.timestamp for item in sequence[:index + 1]]
            all_intervals = [(b - a).total_seconds() for a, b in zip(previous_times, previous_times[1:])]
            recent = all_intervals[-config.recent_intervals:]
            future = [(sequence[j].timestamp - sequence[j - 1].timestamp).total_seconds()
                      for j in range(index + 1, index + config.future_intervals + 1)]
            if any(value <= 0 or not math.isfinite(value) for value in recent + future):
                continue
            baseline = median(recent)
            future_pace = median(future)
            if baseline <= 0:
                continue
            mean = sum(recent) / len(recent)
            std = math.sqrt(sum((value - mean) ** 2 for value in recent) / len(recent))
            features = tuple([
                float(exercise == name) for name in EXERCISES
            ] + [
                max(0.0, (sequence[index].timestamp - start).total_seconds()) / 1200.0,
                baseline, std, recent[-1] / baseline - 1.0, recent[-1],
            ])
            rows.append(TrainingRow(session, exercise, sequence[index].timestamp.isoformat(),
                                    sequence[index].source_file, features,
                                    int(future_pace > baseline * (1.0 + config.slowdown_ratio))))
    audit = audit_recordings(recordings_dir)
    audit.update({
        'derivedExamples': len(rows),
        'derivedSessions': len({row.session_id for row in rows}),
        'labelDistribution': {'0': sum(row.label == 0 for row in rows), '1': sum(row.label == 1 for row in rows)},
        'labelDefinition': f'future median of {config.future_intervals} intervals > current median of '
                           f'{config.recent_intervals} intervals by {config.slowdown_ratio:.0%}',
        'featureCausality': 'Features use timestamps through the current repetition; future intervals are labels only.',
    })
    return rows, audit


def _split_sessions(rows: list[TrainingRow], seed: int) -> tuple[list[str], list[str]]:
    sessions = sorted({row.session_id for row in rows})
    random.Random(seed).shuffle(sessions)
    validation_count = max(1, round(len(sessions) * 0.2))
    return sessions[validation_count:], sessions[:validation_count]


def _scale(rows: Iterable[TrainingRow], means: list[float], scales: list[float]) -> list[tuple[list[float], int]]:
    return [([(value - means[index]) / scales[index] for index, value in enumerate(row.features)], row.label)
            for row in rows]


class LogisticModel:
    def __init__(self, feature_count: int):
        self.weights = [0.0] * feature_count
        self.bias = 0.0

    def clone(self) -> 'LogisticModel':
        result = LogisticModel(len(self.weights))
        result.weights, result.bias = self.weights[:], self.bias
        return result

    def parameters(self) -> dict:
        return {'weights': self.weights[:], 'bias': self.bias}

    def load(self, parameters: dict) -> None:
        self.weights = [float(value) for value in parameters['weights']]
        self.bias = float(parameters['bias'])

    def probability(self, features: list[float]) -> float:
        value = self.bias + sum(weight * feature for weight, feature in zip(self.weights, features))
        value = max(-40.0, min(40.0, value))
        return 1.0 / (1.0 + math.exp(-value))

    def predict(self, features: list[float]) -> int:
        return int(self.probability(features) >= 0.5)


class VirtualClient:
    def __init__(self, client_id: str, rows: list[TrainingRow], means: list[float], scales: list[float],
                 config: FLConfig, positive_weight: float = 1.0):
        self.client_id, self.rows, self.means, self.scales, self.config = client_id, rows, means, scales, config
        self.positive_weight = positive_weight

    def train(self, global_parameters: dict) -> dict:
        model = LogisticModel(len(FEATURE_NAMES))
        model.load(global_parameters)
        samples = _scale(self.rows, self.means, self.scales)
        for _ in range(self.config.local_epochs):
            for features, label in samples:
                error = (model.probability(features) - label) * (self.positive_weight if label else 1.0)
                for index, value in enumerate(features):
                    model.weights[index] -= self.config.learning_rate * error * value / max(1, len(samples))
                model.bias -= self.config.learning_rate * error / max(1, len(samples))
        return {'clientId': self.client_id, 'sampleCount': len(samples),
                'classDistribution': {'0': sum(label == 0 for _, label in samples),
                                      '1': sum(label == 1 for _, label in samples)},
                'parameters': model.parameters()}


def weighted_average(updates: list[dict]) -> dict:
    """Aggregate parameter-only client updates weighted by sample count."""
    total = sum(int(update.get('sampleCount', 0)) for update in updates)
    if total <= 0:
        raise ValueError('cannot aggregate empty client updates')
    width = len(updates[0]['parameters']['weights'])
    weights = [0.0] * width
    bias = 0.0
    for update in updates:
        factor = int(update['sampleCount']) / total
        bias += float(update['parameters']['bias']) * factor
        for index, value in enumerate(update['parameters']['weights']):
            weights[index] += float(value) * factor
    return {'weights': weights, 'bias': bias}


def _class_distribution(rows: Iterable[TrainingRow]) -> dict[str, int]:
    result = {'0': 0, '1': 0}
    for row in rows:
        result[str(row.label)] += 1
    return result


def _fit_scaler(rows: list[TrainingRow]) -> tuple[list[float], list[float]]:
    if not rows:
        return [0.0] * len(FEATURE_NAMES), [1.0] * len(FEATURE_NAMES)
    means = [sum(row.features[index] for row in rows) / len(rows) for index in range(len(FEATURE_NAMES))]
    scales = []
    for index in range(len(FEATURE_NAMES)):
        variance = sum((row.features[index] - means[index]) ** 2 for row in rows) / len(rows)
        scales.append(math.sqrt(variance) or 1.0)
    return means, scales


def _loss(model: LogisticModel, rows: list[TrainingRow], means: list[float], scales: list[float]) -> float:
    values = _scale(rows, means, scales)
    if not values:
        return 0.0
    total = 0.0
    for features, label in values:
        probability = min(1.0 - 1e-12, max(1e-12, model.probability(features)))
        total -= label * math.log(probability) + (1 - label) * math.log(1 - probability)
    return total / len(values)


def _metrics(model: LogisticModel, rows: list[TrainingRow], means: list[float], scales: list[float],
             threshold: float = 0.5) -> dict:
    values = _scale(rows, means, scales)
    matrix = [[0, 0], [0, 0]]
    for features, label in values:
        matrix[label][int(model.probability(features) >= threshold)] += 1
    tn, fp, fn, tp = matrix[0][0], matrix[0][1], matrix[1][0], matrix[1][1]
    total = sum(sum(row) for row in matrix)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {'sampleCount': total, 'accuracy': (tp + tn) / total if total else 0.0,
            'precision': precision, 'recall': recall, 'f1': f1,
            'confusionMatrix': matrix}


def _parameter_delta(previous: dict, current: dict) -> float:
    return math.sqrt((previous['bias'] - current['bias']) ** 2 + sum(
        (before - after) ** 2 for before, after in zip(previous['weights'], current['weights'])))


def _partition_clients(rows: list[TrainingRow], sessions: list[str], means: list[float], scales: list[float],
                       config: FLConfig, positive_weight: float) -> list[VirtualClient]:
    session_client = {session: index % 3 for index, session in enumerate(sessions)}
    return [VirtualClient(f'virtual-client-{index + 1}',
                          [row for row in rows if session_client.get(row.session_id) == index],
                          means, scales, config, positive_weight)
            for index in range(3)]


def _calibrate_threshold(rows: list[TrainingRow], config: FLConfig, seed: int) -> float:
    """Choose a threshold using an inner session split, never an outer fold."""
    sessions = sorted({row.session_id for row in rows})
    if len(sessions) < 3 or len({row.label for row in rows}) < 2:
        return 0.5
    random.Random(seed + 101).shuffle(sessions)
    cut = max(1, round(len(sessions) * 0.25))
    inner_validation = {session for session in sessions[:cut]}
    inner_train = [row for row in rows if row.session_id not in inner_validation]
    inner_rows = [row for row in rows if row.session_id in inner_validation]
    if not inner_train or not inner_rows or len({row.label for row in inner_train}) < 2:
        return 0.5
    means, scales = _fit_scaler(inner_train)
    positive = sum(row.label for row in inner_train)
    weight = (len(inner_train) - positive) / positive if positive else 1.0
    model = LogisticModel(len(FEATURE_NAMES))
    update = VirtualClient('threshold-calibration', inner_train, means, scales, config, weight).train(model.parameters())
    model.load(update['parameters'])
    best = (0.0, 0.5)
    for threshold in (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70):
        metric = _metrics(model, inner_rows, means, scales, threshold)
        score = (metric['f1'], -abs(threshold - 0.5))
        if score > best:
            best = score
            best_threshold = threshold
    return locals().get('best_threshold', 0.5)


def _run_fedavg(train_rows: list[TrainingRow], validation_rows: list[TrainingRow], train_sessions: list[str],
                config: FLConfig, threshold: float) -> tuple[LogisticModel, list[dict], list[dict]]:
    means, scales = _fit_scaler(train_rows)
    positive = sum(row.label for row in train_rows)
    positive_weight = (len(train_rows) - positive) / positive if positive else 1.0
    clients = _partition_clients(train_rows, train_sessions, means, scales, config, positive_weight)
    model = LogisticModel(len(FEATURE_NAMES))
    round_metrics = []
    for round_number in range(1, config.rounds + 1):
        before = model.parameters()
        updates = [client.train(before) for client in clients if client.rows]
        if not updates:
            break
        model.load(weighted_average(updates))
        parameters = model.parameters()
        round_metrics.append({
            'round': round_number,
            'clientSampleCounts': {update['clientId']: update['sampleCount'] for update in updates},
            'clientClassDistributions': {update['clientId']: update['classDistribution'] for update in updates},
            'parameterL2Delta': _parameter_delta(before, parameters),
            'trainingLoss': _loss(model, train_rows, means, scales),
            'globalValidation': _metrics(model, validation_rows, means, scales, threshold),
        })
    clients_report = [{'clientId': client.client_id,
                       'sessionIds': sorted({row.session_id for row in client.rows}),
                       'sampleCount': len(client.rows), 'classDistribution': _class_distribution(client.rows)}
                      for client in clients]
    return model, round_metrics, (means, scales, clients_report)


def train_federated(recordings_dir: str | Path, config: FLConfig = FLConfig()) -> dict:
    rows, audit = build_dataset(recordings_dir, config)
    train_sessions, validation_sessions = _split_sessions(rows, config.seed) if rows else ([], [])
    train_rows = [row for row in rows if row.session_id in train_sessions]
    validation_rows = [row for row in rows if row.session_id in validation_sessions]
    if len(train_rows) < config.minimum_samples or len(validation_rows) < 1 or len({row.label for row in train_rows}) < 2:
        return {'status': 'insufficient_data', 'modelVersion': None, 'provenance': 'real_csv_recordings',
                'audit': audit, 'trainSampleCount': len(train_rows), 'validationSampleCount': len(validation_rows),
                'trainSessions': train_sessions, 'validationSessions': validation_sessions,
                'message': 'Need enough grouped examples and both slowdown classes for evaluation.'}
    threshold = _calibrate_threshold(train_rows, config, config.seed)
    model, round_metrics, (means, scales, clients_report) = _run_fedavg(
        train_rows, validation_rows, train_sessions, config, threshold)
    majority = int(sum(row.label for row in train_rows) * 2 >= len(train_rows))
    baseline_matrix = [[0, 0], [0, 0]]
    for row in validation_rows:
        baseline_matrix[row.label][majority] += 1
    baseline_total = len(validation_rows)
    baseline_accuracy = sum(baseline_matrix[i][i] for i in range(2)) / baseline_total if baseline_total else 0.0
    # Centralized comparison uses the same outer-fold training data and scaler,
    # but is a single local optimization rather than federated aggregation.
    positive = sum(row.label for row in train_rows)
    positive_weight = (len(train_rows) - positive) / positive if positive else 1.0
    central_update = VirtualClient('centralized-baseline', train_rows, means, scales, config, positive_weight).train(
        LogisticModel(len(FEATURE_NAMES)).parameters())
    central_model = LogisticModel(len(FEATURE_NAMES))
    central_model.load(central_update['parameters'])
    cv_folds = cross_validate(rows, config)
    trained_folds = [fold for fold in cv_folds if fold.get('status') == 'trained']
    cv_summary = {'foldCount': len(trained_folds), 'accuracy': 0.0, 'precision': 0.0,
                  'recall': 0.0, 'f1': 0.0}
    if trained_folds:
        for metric_name in ('accuracy', 'precision', 'recall', 'f1'):
            cv_summary[metric_name] = sum(fold['fedavg'][metric_name] for fold in trained_folds) / len(trained_folds)
    return {'status': 'trained', 'modelVersion': 'fedavg-logistic-v2', 'provenance': 'real_csv_recordings_single_user',
            'simulation': 'Three sequential virtual clients on one machine; not three real users.',
            'config': {**config.__dict__, 'positiveClassWeight': positive_weight, 'decisionThreshold': threshold},
            'featureNames': FEATURE_NAMES, 'audit': audit,
            'trainSampleCount': len(train_rows), 'validationSampleCount': len(validation_rows),
            'trainClassDistribution': _class_distribution(train_rows),
            'validationClassDistribution': _class_distribution(validation_rows),
            'trainSessions': train_sessions, 'validationSessions': validation_sessions,
            'clientPartitions': clients_report, 'roundMetrics': round_metrics,
            'validation': _metrics(model, validation_rows, means, scales, threshold),
            'baselines': {
                'majority': {'name': 'training-majority', 'label': majority, 'confusionMatrix': baseline_matrix,
                             'accuracy': baseline_accuracy, 'classDistribution': _class_distribution(validation_rows)},
                'centralizedLogistic': {'metrics': _metrics(central_model, validation_rows, means, scales, threshold),
                                        'trainingLoss': _loss(central_model, train_rows, means, scales)},
            },
            # Keep the old key for clients of the Phase 1 response contract.
            'baseline': {'name': 'training-majority', 'label': majority, 'confusionMatrix': baseline_matrix,
                         'accuracy': baseline_accuracy},
            'crossValidation': cv_folds, 'crossValidationSummary': cv_summary,
            'limitations': ['All real recordings are from one user.', 'CSV-derived labels are proxy future-pace labels, not clinical fatigue labels.',
                            'This proof of concept does not provide complete privacy guarantees.',
                            'Validation performance is not evidence of real-world fatigue prediction.']}


def cross_validate(rows: list[TrainingRow], config: FLConfig = FLConfig()) -> list[dict]:
    """Evaluate deterministic session-grouped folds with fold-local scaling."""
    sessions = sorted({row.session_id for row in rows})
    if len(sessions) < 2:
        return [{'fold': 1, 'status': 'insufficient_data',
                 'trainSessions': [], 'validationSessions': sessions,
                 'trainClassDistribution': {'0': 0, '1': 0},
                 'validationClassDistribution': _class_distribution(rows)}]
    shuffled = sessions[:]
    random.Random(config.seed).shuffle(shuffled)
    fold_count = min(5, len(shuffled))
    folds = [shuffled[index::fold_count] for index in range(fold_count)]
    results = []
    for index, validation_session_list in enumerate(folds, 1):
        validation_sessions = set(validation_session_list)
        train_rows = [row for row in rows if row.session_id not in validation_sessions]
        validation_rows = [row for row in rows if row.session_id in validation_sessions]
        if not train_rows or not validation_rows or len({row.label for row in train_rows}) < 2:
            results.append({'fold': index, 'status': 'insufficient_data',
                            'trainSessions': sorted({row.session_id for row in train_rows}),
                            'validationSessions': sorted(validation_sessions),
                            'trainClassDistribution': _class_distribution(train_rows),
                            'validationClassDistribution': _class_distribution(validation_rows)})
            continue
        threshold = _calibrate_threshold(train_rows, config, config.seed + index)
        model, rounds, (_, _, clients) = _run_fedavg(
            train_rows, validation_rows, sorted({row.session_id for row in train_rows}), config, threshold)
        means, scales = _fit_scaler(train_rows)
        positive = sum(row.label for row in train_rows)
        weight = (len(train_rows) - positive) / positive if positive else 1.0
        central = LogisticModel(len(FEATURE_NAMES))
        central.load(VirtualClient('centralized-baseline', train_rows, means, scales, config, weight)
                     .train(central.parameters())['parameters'])
        majority = int(sum(row.label for row in train_rows) * 2 >= len(train_rows))
        majority_matrix = [[0, 0], [0, 0]]
        for row in validation_rows:
            majority_matrix[row.label][majority] += 1
        results.append({'fold': index, 'status': 'trained', 'threshold': threshold,
                        'trainSessions': sorted({row.session_id for row in train_rows}),
                        'validationSessions': sorted(validation_sessions),
                        'trainSampleCount': len(train_rows), 'validationSampleCount': len(validation_rows),
                        'trainClassDistribution': _class_distribution(train_rows),
                        'validationClassDistribution': _class_distribution(validation_rows),
                        'fedavg': _metrics(model, validation_rows, means, scales, threshold),
                        'centralizedLogistic': _metrics(central, validation_rows, means, scales, threshold),
                        'majority': {'accuracy': sum(row[majority] for row in majority_matrix) / len(validation_rows),
                                     'confusionMatrix': majority_matrix},
                        'roundMetrics': rounds, 'clients': clients})
    return results


def result_path(project_root: str | Path) -> Path:
    return Path(project_root) / 'data' / 'fl' / 'latest_results.json'


def read_result(project_root: str | Path) -> dict | None:
    path = result_path(project_root)
    try:
        with path.open(encoding='utf-8') as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Run the offline CindySense FL demonstration.')
    parser.add_argument('command', choices=('train', 'audit'))
    parser.add_argument('--recordings-dir', default='data/recordings')
    parser.add_argument('--output', default='data/fl/latest_results.json')
    args = parser.parse_args()
    if args.command == 'audit':
        value = audit_recordings(args.recordings_dir)
    else:
        value = train_federated(args.recordings_dir)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(value, indent=2), encoding='utf-8')
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
