"""Read-only camera throughput probe using the workout's capture implementation."""

import argparse
import json
import time
from pathlib import Path

from webcam_camera import LatestFrameCamera
from webcam_dashboard import CameraLock
from webcam_metrics import WebcamTimings


def main():
    parser = argparse.ArgumentParser(description='Measure webcam capture without pose inference or publishing')
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--camera-width', type=int, default=640)
    parser.add_argument('--camera-height', type=int, default=480)
    parser.add_argument('--camera-fps', type=int, default=30)
    parser.add_argument('--seconds', type=float, default=30)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    root = Path(__file__).resolve().parent.parent
    (root / 'data').mkdir(exist_ok=True)
    lock = CameraLock(root / 'data' / 'webcam-camera.lock')
    camera = LatestFrameCamera(args.camera, args.camera_width, args.camera_height, args.camera_fps)
    timings = WebcamTimings()
    processed = 0
    started = None
    ended = None
    last_read_return = None
    try:
        camera.start()
        started = time.monotonic()
        while time.monotonic() - started < args.seconds:
            read_started = time.monotonic()
            ok, _frame, info = camera.read()
            timings.record('consumerWaitMs', (time.monotonic() - read_started) * 1000)
            if not ok:
                break
            processed += 1
            if last_read_return is not None:
                timings.record('deliveredIntervalMs', (info['capturedAt'] - last_read_return) * 1000)
            last_read_return = info['capturedAt']
        ended = time.monotonic()
    finally:
        camera.close()
        lock.close()
    print(json.dumps({'processedFrames': processed,
                      'processedFps': (round(processed / max(0.001, ended - started), 2)
                                       if started is not None and ended is not None else None),
                      'camera': camera.diagnostics(), 'timings': timings.snapshot()}, indent=2))


if __name__ == '__main__':
    main()
