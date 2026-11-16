# CindySense Federated Learning Phase 1

This is an offline proof of concept. Three sequential virtual clients run on
the same Windows laptop and do not represent three real users. The module is
not started by FastAPI startup, the webcam worker, TinyIoT, or CindyPT.

## Dataset audit

The current audit scans `data/recordings/webcam_workout_*.csv` read-only. The
recordings contain 34 workout sessions and 141,811 rows, with pull-up, push-up,
and squat frame data. There is one real user, so session grouping prevents
row/session leakage but cannot establish cross-user validity. Fourteen sessions
contain enough repetition timestamps for causal future-window examples.

Repetition timestamps are taken from increasing `exerciseReps` values. A row's
features use only the current and previous three intervals. The label is one
when the following three-interval median is more than 20% slower than the
current median. These are proxy pace labels, not medical fatigue labels.

The current run produced 478 examples: 353 negative and 125 positive. The
session-grouped split contains 449 training and 29 validation examples.

## Model and FedAvg

`backend/federated_learning.py` implements a deterministic logistic classifier
with exercise one-hot features, elapsed fraction, recent pace, interval
variation, relative pace change, and last interval. Three clients own disjoint
whole-session partitions and return only parameter arrays and sample counts.
FedAvg weights each update by its local sample count. A deterministic positive
class weight is used because slowdown labels are imbalanced.

The current real-recording demonstration used four rounds and produced:

- Validation accuracy: 0.586
- Precision: 0.111
- Recall: 0.200
- F1: 0.143
- Confusion matrix: `[[16, 8], [4, 1]]`
- Majority baseline accuracy: 0.828

The model does not outperform the baseline on this small, single-user split.
This is an engineering demonstration only; real-world predictive validity is
unverified.

## Running the demonstration

```powershell
.\\.venv\\Scripts\\python.exe -m backend.federated_learning audit --recordings-dir data/recordings
.\\.venv\\Scripts\\python.exe -m backend.federated_learning train --recordings-dir data/recordings --output data/fl/latest_results.json
```

After the explicit command, restart or reload FastAPI if needed and read:

```text
GET /api/fl/status
GET /api/fl/results
```

These endpoints only read the result JSON. They never train automatically and
never write TinyIoT data. The dashboard labels the result as a single-machine
simulation and keeps it separate from CindyPT's rule-based warnings.
