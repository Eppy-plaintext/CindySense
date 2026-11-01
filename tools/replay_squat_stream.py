"""Replay collector CSV rows through the causal detector without loading the CSV."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.streaming_squat_detector import (  # noqa: E402
    StreamingConfig, StreamingSquatDetector,
)


REQUIRED = ("sample_time_seconds", "ax", "ay", "az", "gx", "gy", "gz")


def replay(path: Path, *, batch_size: int = 50, orientation: str = "auto",
           max_gap_ms: float = 40.0, calibration_mode: str = "adaptive",
           standing_calibration_seconds: float = 2.0) -> dict:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    detector: StreamingSquatDetector | None = None
    batch: list[dict[str, str]] = []
    events = []
    delivery_waits = []
    first_time: float | None = None
    sample_count = batch_number = 0

    def flush() -> None:
        nonlocal batch_number
        if not batch:
            return
        batch_number += 1
        emitted = detector.process_batch(batch, batch_id=f"replay-{batch_number}")
        last_time = float(batch[-1]["sample_time_seconds"])
        events.extend(emitted)
        delivery_waits.extend(max(0.0, last_time - event.completed_time_seconds)
                              for event in emitted)
        batch.clear()

    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = [key for key in REQUIRED if key not in (reader.fieldnames or ())]
        if missing:
            raise ValueError(f"Missing CSV columns: {', '.join(missing)}")
        for row in reader:
            if detector is None:
                session_id = row.get("session_id") or path.stem
                detector = StreamingSquatDetector(
                    session_id, StreamingConfig(orientation=orientation, max_gap_ms=max_gap_ms,
                                                calibration_mode=calibration_mode,
                                                standing_calibration_seconds=
                                                standing_calibration_seconds))
                first_time = float(row["sample_time_seconds"])
            batch.append(row)
            sample_count += 1
            if len(batch) >= batch_size:
                flush()
    if detector is None:
        raise ValueError("Recording is empty")
    flush()
    state = detector.get_state()
    return {
        "samples": sample_count, "batches": batch_number, "state": state,
        "events": events, "first_time": first_time,
        "mean_batch_wait_seconds": statistics.mean(delivery_waits) if delivery_waits else 0.0,
        "max_batch_wait_seconds": max(delivery_waits, default=0.0),
        "mean_bottom_to_confirmation_seconds": (
            statistics.mean(event.bottom_to_confirmation_seconds for event in events)
            if events else 0.0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--orientation", choices=("auto", "positive", "negative"),
                        default="auto")
    parser.add_argument("--max-gap-ms", type=float, default=40.0)
    parser.add_argument("--calibration-mode", choices=("adaptive", "initial_standing"),
                        default="adaptive")
    parser.add_argument("--standing-calibration-seconds", type=float, default=2.0,
                        help="stable standing window required by initial_standing mode")
    args = parser.parse_args()
    try:
        result = replay(args.csv_path, batch_size=args.batch_size,
                        orientation=args.orientation, max_gap_ms=args.max_gap_ms,
                        calibration_mode=args.calibration_mode,
                        standing_calibration_seconds=args.standing_calibration_seconds)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    state = result["state"]
    print(f"Samples: {result['samples']}; batches: {result['batches']}; "
          f"calibration: {state['calibration']}; orientation: {state['orientation']}")
    print(f"Detected repetitions: {state['repetitions']}")
    print(f"Calibration quality: {state['calibration_quality']}")
    print(f"Standing reference: {state['standing_reference_deg']}")
    for event in result["events"]:
        elapsed = event.completed_time_seconds - result["first_time"]
        print(f"  {event.repetition:2d}. {elapsed:.2f}s, "
              f"{event.orientation}, {event.gyro_axis.upper()}, peak "
              f"{event.peak_tilt_deg:.1f} deg, raw return {event.raw_return_deg:.1f} deg, "
              f"gyro descent/ascent {event.descent_gyro_deg_s:.1f}/"
              f"{event.ascent_gyro_deg_s:.1f} deg/s, cycle "
              f"{event.cycle_duration_seconds:.2f}s")
    print(f"Mean bottom-to-confirmation: "
          f"{result['mean_bottom_to_confirmation_seconds']:.3f}s; "
          f"mean/max batch delivery wait: "
          f"{result['mean_batch_wait_seconds']:.3f}/"
          f"{result['max_batch_wait_seconds']:.3f}s")
    print(f"Diagnostics: {state['diagnostics']}")
    for rejection in state["recent_rejections"]:
        print(f"Rejected: {rejection}")
    for candidate in state["uncertain_candidates"]:
        elapsed = candidate["completed_time_seconds"] - result["first_time"]
        print(f"Uncertain movement candidate: {elapsed:.2f}s, "
              f"peak {candidate['peak_tilt_deg']:.1f} deg, raw return "
              f"{candidate['raw_return_deg']:.1f} deg, gyro descent/ascent "
              f"{candidate['descent_gyro_deg_s']:.1f}/"
              f"{candidate['ascent_gyro_deg_s']:.1f} deg/s; {candidate['reason']}")
    if state["calibration"] != "ready":
        print(f"Calibration note: {state['calibration_note']}")


if __name__ == "__main__":
    main()
