"""Plot labelled CindySense MPU6050 recordings without changing the CSV."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
import statistics

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


SENSOR_COLUMNS = ("ax", "ay", "az", "gx", "gy", "gz")
TIME_COLUMN = "sample_time_seconds"
GAP_THRESHOLD_SECONDS = 0.040
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "plots"


def load_recording(path: Path) -> tuple[list[float], dict[str, list[float]], list[str]]:
    """Return timestamps, sensor readings, and the CSV schema in file order."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        required = (TIME_COLUMN, *SENSOR_COLUMNS)
        missing = [column for column in required if column not in columns]
        if missing:
            raise ValueError(f"Missing required CSV columns: {', '.join(missing)}")

        timestamps: list[float] = []
        readings = {column: [] for column in SENSOR_COLUMNS}
        for row_number, row in enumerate(reader, start=2):
            try:
                timestamp = float(row[TIME_COLUMN])
                values = {column: float(row[column]) for column in SENSOR_COLUMNS}
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid numeric value on CSV row {row_number}: {exc}") from exc
            if not math.isfinite(timestamp) or any(not math.isfinite(value) for value in values.values()):
                raise ValueError(f"Non-finite numeric value on CSV row {row_number}")
            timestamps.append(timestamp)
            for column, value in values.items():
                readings[column].append(value)

    if not timestamps:
        raise ValueError("The CSV has no samples")
    return timestamps, readings, columns


def save_plot(
    elapsed: list[float],
    series: dict[str, list[float]],
    title: str,
    ylabel: str,
    path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(14, 5), layout="constrained")
    for name, values in series.items():
        ax.plot(elapsed, values, label=name, linewidth=1.1)
    ax.set(title=title, xlabel="Elapsed time (s)", ylabel=ylabel)
    ax.set_xlim(left=0, right=max(elapsed) if max(elapsed) > 0 else 1)
    ax.grid(alpha=0.3)
    if len(series) > 1:
        ax.legend(loc="best", ncol=len(series))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="CindySense recording CSV")
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help=f"PNG destination (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    try:
        timestamps, readings, columns = load_recording(args.csv_path)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    elapsed = [timestamp - timestamps[0] for timestamp in timestamps]
    intervals = [later - earlier for earlier, later in zip(timestamps, timestamps[1:])]
    if any(interval <= 0 for interval in intervals):
        parser.error("Sample timestamps must be strictly increasing in CSV order")
    gaps = [(index + 1, interval) for index, interval in enumerate(intervals)
            if interval > GAP_THRESHOLD_SECONDS + 1e-9]

    acceleration_magnitude = [math.sqrt(x*x + y*y + z*z)
                              for x, y, z in zip(readings["ax"], readings["ay"], readings["az"])]
    gyroscope_magnitude = [math.sqrt(x*x + y*y + z*z)
                           for x, y, z in zip(readings["gx"], readings["gy"], readings["gz"])]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.csv_path.stem
    plots = (
        ("accel_axes", {axis.upper(): readings[axis] for axis in ("ax", "ay", "az")},
         "Accelerometer axes", "Acceleration (m/s²)"),
        ("gyro_axes", {axis.upper(): readings[axis] for axis in ("gx", "gy", "gz")},
         "Gyroscope axes", "Angular velocity (deg/s)"),
        ("accel_magnitude", {"|a|": acceleration_magnitude},
         "Acceleration magnitude", "Acceleration magnitude (m/s²)"),
        ("gyro_magnitude", {"|ω|": gyroscope_magnitude},
         "Gyroscope magnitude", "Angular velocity magnitude (deg/s)"),
    )
    output_paths = []
    for suffix, series, title, ylabel in plots:
        output_path = args.output_dir / f"{stem}_{suffix}.png"
        save_plot(elapsed, series, f"{stem}: {title}", ylabel, output_path)
        output_paths.append(output_path)

    print("CSV columns:", ", ".join(columns))
    print("Parsed numeric fields: sample_time_seconds, ax, ay, az, gx, gy, gz")
    print(f"Samples: {len(timestamps)}")
    print(f"Duration: {elapsed[-1]:.3f} s")
    if intervals:
        print(f"Sampling interval: median {statistics.median(intervals)*1000:.3f} ms; "
              f"mean {statistics.mean(intervals)*1000:.3f} ms; "
              f"min {min(intervals)*1000:.3f} ms; max {max(intervals)*1000:.3f} ms")
    print(f"Timestamp gaps > {GAP_THRESHOLD_SECONDS*1000:.0f} ms: {len(gaps)}")
    for sample_index, interval in gaps[:20]:
        print(f"  before sample {sample_index + 1}: {interval*1000:.3f} ms "
              f"at elapsed {elapsed[sample_index]:.3f} s")
    if len(gaps) > 20:
        print(f"  ... {len(gaps) - 20} more gaps")
    for output_path in output_paths:
        print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
