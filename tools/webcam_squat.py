"""CindySense webcam exercise counter (MediaPipe Pose + OpenCV)."""

import argparse
import os
import csv
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import mediapipe as mp

from webcam_exercises import (CindyWorkout, ExerciseCounter, body_movement_measurement,
                              exercise_measurement, pullup_auxiliary_measurement,
                              select_exercise_key, visibility)
from webcam_tinyiot import (PosePublisher, TinyIoTClient, TinyIoTError, DEFAULT_BASE,
                            DEFAULT_ORIGIN, EVENT_CONTAINER, SUMMARY_CONTAINER)
from workout_summary import SummaryPublisher, completed_summary
from workout_timeseries import WorkoutTimeSeries
from local_voice_events import LocalVoiceEvents

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = PROJECT_ROOT / "models" / "pose_landmarker_lite.task"
OUTPUT_DIR = PROJECT_ROOT / "data" / "recordings"
CONNECTIONS = [
    (11, 12), (11, 23), (12, 24), (23, 24), (23, 25), (25, 27),
    (24, 26), (26, 28), (11, 13), (13, 15), (12, 14), (14, 16),
]
TARGETS = {"pullup": 5, "pushup": 10, "squat": 15}


def draw_pose(frame, landmarks):
    h, w = frame.shape[:2]
    for start, end in CONNECTIONS:
        a, b = landmarks[start], landmarks[end]
        if min(visibility(a), visibility(b)) >= 0.50:
            cv2.line(frame, (int(a.x*w), int(a.y*h)), (int(b.x*w), int(b.y*h)), (50, 220, 50), 2)
    for index in {i for pair in CONNECTIONS for i in pair}:
        p = landmarks[index]
        if visibility(p) >= 0.50:
            cv2.circle(frame, (int(p.x*w), int(p.y*h)), 4, (10, 60, 255), -1)


def main():
    parser = argparse.ArgumentParser(description="CindySense webcam workout counter")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--exercise", choices=("cindy", "squat", "pushup", "pullup"), default="cindy",
                        help="Start in 20-minute Cindy AMRAP or a single exercise mode")
    parser.add_argument("--duration-minutes", type=float, default=20.0, help="Cindy AMRAP duration")
    parser.add_argument("--tinyiot", action="store_true", help="Publish webcam poses and repetition events")
    parser.add_argument("--tinyiot-base", default=DEFAULT_BASE)
    parser.add_argument("--tinyiot-origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--tinyiot-event-origin", default=os.environ.get("CINDYSENSE_EVENT_ORIGIN"),
                        help="Originator with create access to CindySense_AE/RepetitionCount")
    parser.add_argument("--event-container", default=EVENT_CONTAINER,
                        help="Existing oneM2M container path, e.g. CindySense_AE/RepetitionCount")
    parser.add_argument("--tinyiot-summary-origin",
                        help="Originator with create and retrieve access to WorkoutSummary; defaults to event origin")
    parser.add_argument("--summary-container", default=SUMMARY_CONTAINER,
                        help="Existing oneM2M workout summary container path")
    parser.add_argument("--workout-ts-origin",
                        help="Optional dedicated originator for WorkoutPerformance_TS; defaults to summary origin")
    parser.add_argument("--no-workout-timeseries", action="store_true",
                        help="Keep WorkoutSummary publishing but skip optional TimeSeries mirroring")
    parser.add_argument("--voice-events-port", type=int, default=8765,
                        help="Local browser voice event stream port; 0 disables it")
    args = parser.parse_args()
    if args.tinyiot and (not args.tinyiot_event_origin or args.tinyiot_event_origin == 'CAdmin'
                         or (args.tinyiot_summary_origin or args.tinyiot_event_origin) == 'CAdmin'):
        parser.error("Set verified non-admin event and summary publisher origins before writing workouts.")
    if not args.model.is_file():
        sys.exit(f"Model missing: {args.model}\nDownload pose_landmarker_lite.task into models/ first.")
    camera = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW) if sys.platform.startswith("win") else cv2.VideoCapture(args.camera)
    if not camera.isOpened():
        camera.release()
        if sys.platform.startswith("win"):
            camera = cv2.VideoCapture(args.camera)
    if not camera.isOpened():
        sys.exit("Cannot open webcam. Check camera privacy settings or try --camera 1.")

    workout = CindyWorkout(max(1, int(args.duration_minutes * 60))) if args.exercise == "cindy" else None
    active = workout.current_exercise if workout else args.exercise
    counters = {name: ExerciseCounter(name) for name in TARGETS}
    start = time.monotonic()
    session_id = "webcam-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    event_sequence = 0
    last_ts_ms = -1
    last_diagnostic = 0.0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / f"webcam_workout_{stamp}.csv"
    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO, num_poses=1,
        min_pose_detection_confidence=0.5, min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    publisher = (PosePublisher(args.tinyiot_base, args.tinyiot_origin,
                               event_container=args.event_container,
                               event_origin=args.tinyiot_event_origin) if args.tinyiot else None)
    summary_client = (TinyIoTClient(args.tinyiot_base,
                                   args.tinyiot_summary_origin or args.tinyiot_event_origin)
                      if args.tinyiot else None)
    summary_publisher = (SummaryPublisher(summary_client, args.summary_container)
                         if summary_client else None)
    ts_client = (TinyIoTClient(args.tinyiot_base,
                              args.workout_ts_origin or args.tinyiot_summary_origin or args.tinyiot_event_origin,
                              timeout=1.5)
                 if summary_client and not args.no_workout_timeseries else None)
    ts_publisher = (WorkoutTimeSeries(ts_client)
                    if summary_client and not args.no_workout_timeseries else None)
    voice_events = None
    if args.voice_events_port:
        try:
            voice_events = LocalVoiceEvents(args.voice_events_port)
        except OSError as exc:
            print(f"[VOICE] Local event stream unavailable: {exc}")
    def announce(kind, **details):
        if voice_events and workout:
            return voice_events.emit({"type": kind, "sessionId": session_id, **details})
        return None
    print(f"Webcam session {session_id}; mode={args.exercise}; TinyIoT={'on' if publisher else 'off'}")
    print("Click the webcam window before pressing keys.")
    print("Manual modes: 1 pull-up | 2 push-up | 3 squat | C Cindy | S stop and save | R reset | Q quit without saving")
    print("Pull-ups need upper body and hands visible; bar/chin are not model landmarks.")
    print(f"Saving measurements to: {csv_path}")
    reps_this_mode = {name: 0 for name in TARGETS}
    total_reps = {name: 0 for name in TARGETS}
    total_rounds = 0
    session_baseline = dict(total_reps)
    end_reason = None
    timer_announced = set()
    try:
        announce("start", round=1)
        with open(csv_path, "w", newline="", encoding="utf-8") as f, \
                mp.tasks.vision.PoseLandmarker.create_from_options(options) as landmarker:
            fields = ["timestampUtc", "elapsedMs", "exercise", "kneeAngleDeg", "trackedLeg",
                      "visibility", "phase", "repCount", "angleDeg", "trackedSide",
                      "confidence", "bodyMovement", "exerciseReps", "rounds", "sessionId",
                      "headPosition", "lateralPosition", "detectorReason", "lastRejection",
                      "eventId", "eventQueueDepth", "frameGapMs", "poseMs"]
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            while True:
                if workout and workout.finished:
                    print(f"[SESSION] 20-minute Cindy complete; rounds={workout.rounds}")
                    end_reason = "timer"
                    announce("complete")
                    break
                ok, frame = camera.read()
                if not ok:
                    print("Camera frame read failed.")
                    break
                frame_exercise = active
                frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                ts_ms = max(int((time.monotonic() - start) * 1000), last_ts_ms + 1)
                frame_gap_ms = ts_ms - last_ts_ms if last_ts_ms >= 0 else 0
                last_ts_ms = ts_ms
                pose_started = time.monotonic()
                result = landmarker.detect_for_video(image, ts_ms)
                pose_ms = round((time.monotonic() - pose_started) * 1000, 1)
                angle, confidence, side = None, 0.0, "NONE"
                body_movement, body_confidence = None, 0.0
                head_position = lateral_position = None
                measurement_reason = "no pose landmarks"
                movement_reason = "no pose landmarks"
                if result.pose_landmarks:
                    landmarks = result.pose_landmarks[0]
                    draw_pose(frame, landmarks)
                    angle, confidence, side, measurement_reason = exercise_measurement(
                        frame_exercise, landmarks, w, h, include_reason=True
                    )
                    body_movement, body_confidence, movement_reason = body_movement_measurement(
                        frame_exercise, landmarks, side
                    )
                    if frame_exercise == "pullup":
                        head_position, lateral_position = pullup_auxiliary_measurement(landmarks)
                counter = counters[frame_exercise]
                rejected_before = counter.rejected_candidates
                phase_before = counter.phase
                counted = counter.update(angle, body_movement, head_position, lateral_position,
                                         timestamp_ms=ts_ms)
                confirmed_at_ms = time.time_ns() // 1_000_000 if counted else None
                counted = counted and not (workout and workout.finished)
                if frame_exercise == "pushup" and (counter.phase != phase_before or counter.rejected_candidates != rejected_before):
                    print(f"[PUSHUP] t={ts_ms}ms {phase_before}->{counter.phase} "
                          f"angle={angle if angle is not None else 'missing'} "
                          f"shoulder={body_movement if body_movement is not None else 'missing'} "
                          f"visibility={confidence:.2f} gap={frame_gap_ms}ms pose={pose_ms}ms "
                          f"reason={counter.last_reason}")
                if counter.rejected_candidates != rejected_before:
                    print(f"[REJECT] exercise={frame_exercise} reason={counter.last_rejection}")
                event = None
                if counted:
                    event_round = workout.rounds + 1 if workout else 1
                    spoken_count = workout.progress[frame_exercise] + 1 if workout else reps_this_mode[frame_exercise] + 1
                    reps_this_mode[frame_exercise] += 1
                    total_reps[frame_exercise] += 1
                    if workout:
                        total_rounds = workout.record_rep(frame_exercise)
                        reps_this_mode[frame_exercise] = workout.progress[frame_exercise]
                        active = workout.current_exercise
                        if active != frame_exercise:
                            counters[active].reset_phase()
                    else:
                        total_rounds = 0
                    event_sequence += 1
                    event = {
                        "eventId": str(uuid.uuid4()), "eventSequence": event_sequence,
                        "sessionId": session_id, "exercise": counter.exercise,
                        "repetitions": total_reps[counter.exercise] - session_baseline[counter.exercise],
                        "round": event_round,
                        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                        "source": "webcam",
                    }
                    print(f"[REP] detectedAt={event['timestamp']} id={event['eventId']} "
                          f"{counter.exercise} total={total_reps[counter.exercise]} "
                          f"sessionSeq={event_sequence} rounds={total_rounds}")
                    published_at_ms = announce("rep", eventId=event["eventId"], eventSequence=event_sequence,
                             confirmedAtEpochMs=confirmed_at_ms,
                             exercise=frame_exercise, count=spoken_count,
                             nextExercise=active if active != frame_exercise else None,
                             roundComplete=bool(workout and active != frame_exercise and active == "pullup"),
                             round=workout.rounds + 1 if workout else 1)
                    if published_at_ms is not None:
                        print(f"[VOICE] id={event['eventId']} confirmToLocalPublishMs={published_at_ms - confirmed_at_ms}")
                    if publisher:
                        publisher.offer_event(event)

                elapsed_s = (time.monotonic() - start)
                display_exercise = frame_exercise
                display_counter = counters[display_exercise]
                if workout:
                    remaining = workout.remaining
                    for threshold, label in ((600, "10"), (300, "5"), (60, "1")):
                        if workout.duration_seconds >= threshold and remaining <= threshold and label not in timer_announced:
                            timer_announced.add(label)
                            announce("timer", minutes=label)
                    clock_text = f"{int(remaining//60):02d}:{int(remaining%60):02d}"
                    rounds_text = workout.rounds
                    progress_text = f"{workout.progress[display_exercise]}/{TARGETS[display_exercise]}"
                else:
                    clock_text = f"{int(elapsed_s//60):02d}:{int(elapsed_s%60):02d}"
                    rounds_text = total_rounds
                    progress_text = str(total_reps[display_exercise])
                angle_text = f"{angle:.0f} deg" if angle is not None else "untracked"
                position_text = f"bodyΔ={body_movement:.3f}" if body_movement is not None else "bodyΔ=none"
                tracking_confidence = max(confidence, body_confidence)
                reason = "counted" if counted else (
                    f"{measurement_reason}; {display_counter.last_reason}" if angle is None
                    else display_counter.last_reason
                )
                lines = [f"{display_exercise.upper()}  reps {progress_text}  phase {display_counter.phase}",
                         f"Confidence {tracking_confidence:.2f}  {angle_text}  {position_text}",
                         f"Cindy rounds {rounds_text}  {'remaining' if workout else 'elapsed'} {clock_text}",
                         f"Reason: {reason[:72]}",
                         "1 pull-up | 2 push-up | 3 squat | C Cindy | R reset | Q quit"]
                if workout and workout.finished:
                    lines[2] = f"AMRAP complete | rounds {workout.rounds}"
                for i, line in enumerate(lines):
                    cv2.putText(frame, line, (15, 32 + 30*i), cv2.FONT_HERSHEY_SIMPLEX,
                                0.62, (255, 255, 255), 2)
                if time.monotonic() - last_diagnostic >= 1.0:
                    print(f"[DETECT] exercise={display_exercise} phase={display_counter.phase} "
                          f"visibility={tracking_confidence:.2f} angle={angle if angle is not None else 'none'} "
                          f"bodyMovement={body_movement if body_movement is not None else 'none'} "
                          f"reason={reason}")
                    last_diagnostic = time.monotonic()
                timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                if publisher:
                    # Keep the existing Webcam_AE/PoseData shape and exercise/phase fields.
                    publisher.offer({
                        "deviceId": "CindySense_Webcam_01", "source": "webcam_live", "synthetic": False,
                        "timestampUtc": timestamp, "elapsedMs": ts_ms, "exercise": display_exercise,
                        "kneeAngleDeg": round(angle, 2) if display_exercise == "squat" and angle is not None else None,
                        "trackedLeg": side if display_exercise == "squat" else "NONE",
                        "jointAngleDeg": round(angle, 2) if angle is not None else None,
                        "bodyMovement": round(body_movement, 4) if body_movement is not None else None,
                        "visibility": round(tracking_confidence, 3), "phase": counters[display_exercise].phase,
                        "repCount": total_reps[display_exercise] - session_baseline[display_exercise],
                        "repEvent": counted,
                        "rounds": rounds_text, "sessionId": session_id,
                        **publisher.diagnostics(),
                    })
                writer.writerow({"timestampUtc": timestamp, "elapsedMs": ts_ms, "exercise": display_exercise,
                                 "kneeAngleDeg": f"{angle:.2f}" if display_exercise == "squat" and angle is not None else "",
                                 "trackedLeg": side if display_exercise == "squat" else "NONE",
                                 "visibility": f"{tracking_confidence:.3f}", "repCount": total_reps[display_exercise],
                                 "angleDeg": f"{angle:.2f}" if angle is not None else "",
                                 "bodyMovement": f"{body_movement:.4f}" if body_movement is not None else "",
                                 "trackedSide": side, "confidence": f"{confidence:.3f}",
                                 "phase": counters[display_exercise].phase, "exerciseReps": total_reps[display_exercise],
                                 "rounds": rounds_text, "sessionId": session_id,
                                 "headPosition": f"{head_position:.4f}" if head_position is not None else "",
                                 "lateralPosition": f"{lateral_position:.4f}" if lateral_position is not None else "",
                                 "detectorReason": reason, "lastRejection": display_counter.last_rejection or "",
                                 "eventId": event["eventId"] if event is not None else "",
                                 "eventQueueDepth": publisher.diagnostics()["eventQueueDepth"] if publisher else 0,
                                 "frameGapMs": frame_gap_ms, "poseMs": pose_ms})
                cv2.imshow("CindySense Webcam Workout", frame)
                key = cv2.waitKey(1) & 0xFF
                if workout and workout.finished:
                    end_reason = "timer"
                    announce("complete")
                    break
                if key in (ord("s"), ord("S")) and workout:
                    end_reason = "explicit_stop"
                    announce("stop")
                    print(f"[SESSION] Cindy stopped explicitly; rounds={workout.rounds}")
                    break
                if key in (ord("q"), 27):
                    announce("reset")
                    break
                selected = select_exercise_key(key, workout)
                if selected is not None:
                    if active != selected:
                        active = selected
                        counters[selected].reset_phase()
                    print(f"[MODE] active exercise changed to {selected}; detector phase={counters[selected].phase}")
                elif key in (ord("c"), ord("C")) and workout is None:
                    workout = CindyWorkout(max(1, int(args.duration_minutes * 60)))
                    session_id = "webcam-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    session_baseline = dict(total_reps)
                    event_sequence = 0
                    active = workout.current_exercise
                    print(f"Started Cindy AMRAP timer; session {session_id}.")
                    timer_announced = set()
                    announce("start", round=1)
                elif key in (ord("r"), ord("R")):
                    announce("reset")
                    counters = {name: ExerciseCounter(name) for name in TARGETS}
                    reps_this_mode = {name: 0 for name in TARGETS}
                    total_reps = {name: 0 for name in TARGETS}
                    session_baseline = dict(total_reps)
                    total_rounds = 0
                    event_sequence = 0
                    session_id = "webcam-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    if workout:
                        workout = CindyWorkout(max(1, int(args.duration_minutes * 60)))
                        active = workout.current_exercise
                        timer_announced = set()
                        announce("start", round=1)
                    print(f"Workout counts reset; new session {session_id}.")
    finally:
        if voice_events:
            voice_events.close()
        if publisher:
            publisher.close()
        if end_reason and workout:
            final_totals = {name: total_reps[name] - session_baseline[name] for name in TARGETS}
            summary = completed_summary(session_id, workout, final_totals, end_reason)
            if summary_publisher:
                try:
                    result = summary_publisher.publish(summary)
                    print(f"[TinyIoT] Workout summary PERSISTED session={session_id} result={result}")
                    if ts_publisher:
                        try:
                            source_path = f"{args.summary_container.strip('/')}/webcam-summary-{session_id}"
                            ts_result, _ = ts_publisher.mirror_source(source_path)
                            print(f"[TinyIoT] Workout TimeSeries {ts_result} session={session_id}")
                        except TinyIoTError as exc:
                            # The confirmed WorkoutSummary remains authoritative; repair
                            # the optional TS through the explicit reconcile command.
                            print(f"[TinyIoT] Optional TimeSeries mirror failed for session={session_id}: {exc}")
                except TinyIoTError as exc:
                    print(f"[TinyIoT] Workout summary PERSISTENCE UNCONFIRMED session={session_id}: {exc}")
            else:
                print(f"[SESSION] Completed locally; TinyIoT summary not enabled for session={session_id}")
        elif workout:
            print(f"[SESSION] Interrupted session={session_id}; no completed summary published")
        if summary_client:
            summary_client.close()
        if ts_client:
            ts_client.close()
        camera.release()
        cv2.destroyAllWindows()
    print(f"Completed. Local repetitions: {total_reps}; rounds={workout.rounds if workout else total_rounds}. CSV: {csv_path}")


if __name__ == "__main__":
    main()
