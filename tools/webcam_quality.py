"""Session-local capture/tracking diagnostics, not a rep-accuracy certificate."""

from collections import defaultdict


class TrackingQuality:
    def __init__(self, gap_threshold_ms=100.0, minimum_frames=30):
        self.gap_threshold_ms = gap_threshold_ms
        self.minimum_frames = minimum_frames
        self._by_exercise = defaultdict(lambda: dict(frames=0, missingPoseFrames=0,
            unusableMeasurementFrames=0, longDetectorGaps=0, droppedFrames=0,
            cameraInterruptions=0, maxDetectorGapMs=0.0))

    def record(self, exercise, *, has_pose, usable_measurement, detector_gap_ms, dropped_frames=0):
        item = self._by_exercise[exercise]
        item['frames'] += 1
        item['missingPoseFrames'] += int(not has_pose)
        item['unusableMeasurementFrames'] += int(not usable_measurement)
        item['droppedFrames'] += max(0, dropped_frames)
        if detector_gap_ms is not None:
            item['maxDetectorGapMs'] = round(max(item['maxDetectorGapMs'], detector_gap_ms), 1)
            item['longDetectorGaps'] += int(detector_gap_ms > self.gap_threshold_ms)

    def interruption(self, exercise):
        self._by_exercise[exercise]['cameraInterruptions'] += 1

    def snapshot(self):
        result = {}
        for exercise, item in self._by_exercise.items():
            if item['frames'] < self.minimum_frames:
                state = 'insufficient_evidence'
            elif (item['cameraInterruptions'] or item['droppedFrames'] or
                  item['longDetectorGaps'] or item['unusableMeasurementFrames']):
                state = 'degraded'
            else:
                state = 'stable'
            result[exercise] = {**item, 'state': state}
        return result
