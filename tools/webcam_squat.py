"""CindySense webcam-only squat detector prototype.

Run from Windows after placing this file at tools/webcam_squat.py.
Uses OpenCV + MediaPipe Tasks (Pose Landmarker), and saves measurements
as CSV. Only accesses TinyIoT with --tinyiot; never saves or uploads raw video.
"""

import argparse
import csv
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import mediapipe as mp

from webcam_tinyiot import PosePublisher, DEFAULT_BASE, DEFAULT_ORIGIN

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = PROJECT_ROOT / "models" / "pose_landmarker_lite.task"
OUTPUT_DIR = PROJECT_ROOT / "data" / "recordings"

# Baseline only: tune these against labelled recordings and camera angle.
DOWN_THRESHOLD_DEG = 115.0
UP_THRESHOLD_DEG = 155.0
STABLE_FRAMES = 3
MIN_VISIBILITY = 0.50

CONNECTIONS = [
    (11, 12), (11, 23), (12, 24), (23, 24),
    (23, 25), (25, 27), (24, 26), (26, 28),
    (11, 13), (13, 15), (12, 14), (14, 16),
]


def angle_degrees(a, b, c, width, height):
    """Joint angle at b, using pixel-scaled 2D landmark coordinates."""
    ax, ay = (a.x - b.x) * width, (a.y - b.y) * height
    cx, cy = (c.x - b.x) * width, (c.y - b.y) * height
    denom = math.hypot(ax, ay) * math.hypot(cx, cy)
    if denom < 1e-6:
        return None
    cosine = max(-1.0, min(1.0, (ax * cx + ay * cy) / denom))
    return math.degrees(math.acos(cosine))


def visibility(point):
    score = getattr(point, "visibility", None)
    return 1.0 if score is None else float(score)


def choose_knee_angle(landmarks, width, height):
    """Choose the more visible leg. Returns (angle, leg, confidence)."""
    candidates = []
    for hip, knee, ankle, leg in [(23, 25, 27, "LEFT"), (24, 26, 28, "RIGHT")]:
        points = [landmarks[hip], landmarks[knee], landmarks[ankle]]
        confidence = min(visibility(p) for p in points)
        if confidence < MIN_VISIBILITY:
            continue
        angle = angle_degrees(*points, width, height)
        if angle is not None:
            candidates.append((confidence, angle, leg))
    if not candidates:
        return None, "NONE", 0.0
    confidence, angle, leg = max(candidates, key=lambda p: p[0])
    return angle, leg, confidence


def draw_pose(frame, landmarks):
    h, w = frame.shape[:2]
    for start, end in CONNECTIONS:
        a, b = landmarks[start], landmarks[end]
        if min(visibility(a), visibility(b)) >= MIN_VISIBILITY:
            p1 = (int(a.x * w), int(a.y * h))
            p2 = (int(b.x * w), int(b.y * h))
            cv2.line(frame, p1, p2, (50, 220, 50), 2)
    for index in {i for pair in CONNECTIONS for i in pair}:
        p = landmarks[index]
        if visibility(p) >= MIN_VISIBILITY:
            cv2.circle(frame, (int(p.x * w), int(p.y * h)), 4, (10, 60, 255), -1)


class SquatCounter:
    def __init__(self):
        self.reps = 0
        self.phase = "UNKNOWN"
        self.down_frames = 0
        self.up_frames = 0
        self.smoothed_angle = None

    def update(self, angle):
        if angle is None:
            self.down_frames = self.up_frames = 0
            self.smoothed_angle = None
            self.phase = "UNKNOWN"  # Require standing calibration after tracking loss.
            return False
        self.smoothed_angle = (
            angle if self.smoothed_angle is None
            else 0.4 * angle + 0.6 * self.smoothed_angle
        )
        a = self.smoothed_angle
        if self.phase == "UNKNOWN":
            if a >= UP_THRESHOLD_DEG:
                self.phase = "UP"
            return False
        if self.phase == "UP":
            self.down_frames = self.down_frames + 1 if a <= DOWN_THRESHOLD_DEG else 0
            if self.down_frames >= STABLE_FRAMES:
                self.phase = "DOWN"
                self.down_frames = 0
        elif self.phase == "DOWN":
            self.up_frames = self.up_frames + 1 if a >= UP_THRESHOLD_DEG else 0
            if self.up_frames >= STABLE_FRAMES:
                self.phase = "UP"
                self.up_frames = 0
                self.reps += 1
                return True
        return False


def main():
    parser = argparse.ArgumentParser(description="CindySense webcam squat prototype")
    parser.add_argument("--camera", type=int, default=0, help="Camera index (default 0)")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--tinyiot", action="store_true", help="Send pose measurements to TinyIoT")
    parser.add_argument("--tinyiot-base", default=DEFAULT_BASE, help="Full CSEBase URL")
    parser.add_argument("--tinyiot-origin", default=DEFAULT_ORIGIN)
    args = parser.parse_args()
    if not args.model.is_file():
        sys.exit(f"Model missing: {args.model}\nDownload pose_landmarker_lite.task into models/ first.")

    if sys.platform.startswith("win"):
        camera = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
        if not camera.isOpened():
            camera.release()
            camera = cv2.VideoCapture(args.camera)
    else:
        camera = cv2.VideoCapture(args.camera)
    if not camera.isOpened():
        sys.exit("Cannot open webcam. Check camera privacy settings or try --camera 1.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_path = OUTPUT_DIR / f"webcam_squat_{stamp}.csv"
    counter = SquatCounter()
    start = time.monotonic()
    last_ts_ms = -1
    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    publisher = (PosePublisher(args.tinyiot_base, args.tinyiot_origin)
                 if args.tinyiot else None)
    print("Webcam starting. Stand sideways with hips, knees and ankles visible.")
    print("Stand upright first, then squat. Press R to reset or Q to quit.")
    print(f"Saving measurements to: {csv_path}")
    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f, \
                mp.tasks.vision.PoseLandmarker.create_from_options(options) as landmarker:
            writer = csv.DictWriter(f, fieldnames=[
                "timestampUtc", "elapsedMs", "exercise", "kneeAngleDeg",
                "trackedLeg", "visibility", "phase", "repCount",
            ])
            writer.writeheader()
            while True:
                ok, frame = camera.read()
                if not ok:
                    print("Camera frame read failed.")
                    break
                frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                ts_ms = max(int((time.monotonic() - start) * 1000), last_ts_ms + 1)
                last_ts_ms = ts_ms
                result = landmarker.detect_for_video(mp_image, ts_ms)
                angle, leg, confidence = None, "NONE", 0.0
                if result.pose_landmarks:
                    landmarks = result.pose_landmarks[0]
                    draw_pose(frame, landmarks)
                    angle, leg, confidence = choose_knee_angle(landmarks, w, h)
                counted = counter.update(angle)
                if counted:
                    print(f"Rep {counter.reps} counted at {ts_ms / 1000:.2f}s")
                angle_text = f"{counter.smoothed_angle:.0f} deg" if counter.smoothed_angle is not None else "No reliable leg"
                for line_no, message in enumerate([
                    f"SQUATS: {counter.reps}",
                    f"Knee angle: {angle_text}",
                    f"State: {counter.phase}   Leg: {leg}",
                    "Side view, full body visible | R reset | Q quit",
                ]):
                    cv2.putText(frame, message, (15, 32 + 30 * line_no),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
                timestamp_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                if publisher is not None:
                    publisher.offer({
                        "deviceId": "CindySense_Webcam_01",
                        "source": "webcam_live",
                        "synthetic": False,
                        "timestampUtc": timestamp_utc,
                        "elapsedMs": ts_ms,
                        "exercise": "squat",
                        "kneeAngleDeg": round(angle, 2) if angle is not None else None,
                        "trackedLeg": leg,
                        "visibility": round(confidence, 3),
                        "phase": counter.phase,
                        "repCount": counter.reps,
                        "repEvent": counted,
                    }, important=counted)
                writer.writerow({
                    "timestampUtc": timestamp_utc,
                    "elapsedMs": ts_ms, "exercise": "squat",
                    "kneeAngleDeg": f"{angle:.2f}" if angle is not None else "",
                    "trackedLeg": leg, "visibility": f"{confidence:.3f}",
                    "phase": counter.phase, "repCount": counter.reps,
                })
                cv2.imshow("CindySense Webcam Squat Detector", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q") or key == 27:
                    break
                if key == ord("r"):
                    counter = SquatCounter()
                    print("Counter reset.")
    finally:
        if publisher is not None:
            publisher.close()
        camera.release()
        cv2.destroyAllWindows()
    print(f"Completed. Recorded {counter.reps} reps. CSV: {csv_path}")


if __name__ == "__main__":
    main()
