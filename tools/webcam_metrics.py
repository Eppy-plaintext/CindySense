"""Bounded monotonic timing summaries for local webcam diagnosis."""

from collections import defaultdict, deque


class WebcamTimings:
    def __init__(self, gap_threshold_ms=100, samples_per_stage=512):
        if gap_threshold_ms <= 0 or samples_per_stage < 1:
            raise ValueError('Timing thresholds must be positive')
        self.gap_threshold_ms = gap_threshold_ms
        self.samples_per_stage = samples_per_stage
        self.samples = defaultdict(lambda: deque(maxlen=samples_per_stage))
        self.counts = defaultdict(int)
        self.totals = defaultdict(float)
        self.maximums = defaultdict(float)
        self.long_detector_gaps = 0

    def record(self, stage, milliseconds):
        if milliseconds < 0:
            raise ValueError('Duration cannot be negative')
        self.samples[stage].append(milliseconds)
        self.counts[stage] += 1
        self.totals[stage] += milliseconds
        self.maximums[stage] = max(self.maximums[stage], milliseconds)
        if stage == 'detectorGapMs' and milliseconds > self.gap_threshold_ms:
            self.long_detector_gaps += 1

    def snapshot(self):
        result = {'detectorGapsOverThreshold': self.long_detector_gaps,
                  'detectorGapThresholdMs': self.gap_threshold_ms}
        for stage, values in self.samples.items():
            ordered = sorted(values)
            result[stage] = {
                'count': self.counts[stage],
                'average': round(self.totals[stage] / self.counts[stage], 2),
                'maximum': round(self.maximums[stage], 2),
                'recentP95': round(ordered[max(0, (len(ordered) * 95 + 99) // 100 - 1)], 2),
            }
        return result
