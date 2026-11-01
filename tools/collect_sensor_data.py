"""Read-only oneM2M CIN recorder for labelled MPU6050 batches."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


FIELDS = ("ax", "ay", "az", "gx", "gy", "gz")
CSV_FIELDS = ("session_id", "label", "device_id", "sample_t", "sample_time_seconds",
              "cin_created_at", "cin_ri", *FIELDS)
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class CollectorError(Exception):
    """Configuration, protocol, or collection failure."""


@dataclass(frozen=True)
class Config:
    base_url: str
    origin: str
    container: str
    output_dir: Path
    poll_seconds: float
    settle_seconds: float
    page_size: int
    max_pages: int
    expected_samples_per_batch: int
    timestamp_unit: str
    device_id: str

    @classmethod
    def load(cls, path: Path) -> Config:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CollectorError(f"Cannot read config {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise CollectorError("Config must be a JSON object")
        base = str(raw.get("base_url", "")).rstrip("/")
        origin = str(raw.get("origin", ""))
        container = str(raw.get("container", "")).strip("/")
        if not base.startswith(("http://", "https://")) or not origin or not container:
            raise CollectorError("Set base_url, origin, and container in the config")
        if any(part in ("", ".", "..") for part in container.split("/")):
            raise CollectorError("Invalid container path")
        output_dir = Path(str(raw.get("output_dir", "")))
        if not str(raw.get("output_dir", "")):
            raise CollectorError("Set output_dir in the config")
        if not output_dir.is_absolute():
            output_dir = path.parent / output_dir
        try:
            poll = float(raw.get("poll_seconds", 1))
            settle = float(raw.get("settle_seconds", 3))
            page_size = int(raw.get("page_size", 150))
            max_pages = int(raw.get("max_pages", 40))
            expected = int(raw.get("expected_samples_per_batch", 50))
        except (TypeError, ValueError) as exc:
            raise CollectorError("Invalid numeric config value") from exc
        unit = str(raw.get("timestamp_unit", "ms"))
        if (not math.isfinite(poll) or poll <= 0 or not math.isfinite(settle)
                or settle < 0 or not 1 <= page_size <= 150 or max_pages < 1
                or expected < 1 or unit not in ("ms", "s")):
            raise CollectorError("Invalid poll, settle, page, batch size, or timestamp unit")
        return cls(base, origin, container, output_dir, poll, settle, page_size,
                   max_pages, expected, unit, str(raw.get("device_id", "")))


def _resource_path(path: str) -> str:
    parts = path.split("/")
    decoded = [unquote(part) for part in parts]
    if any(part in ("", ".", "..") or "/" in part or "\\" in part
           or "\x00" in part for part in decoded):
        raise CollectorError(f"Invalid resource path: {path!r}")
    return "/".join(quote(part, safe="") for part in decoded)


class TinyIoTReader:
    def __init__(self, config: Config):
        self.config = config
        base = urlsplit(config.base_url)
        if (base.scheme not in ("http", "https") or not base.netloc
                or base.query or base.fragment or not base.path.strip("/")):
            raise CollectorError("base_url must end at the CSEBase resource")
        self._base = base
        self._base_path = base.path.rstrip("/")
        self._cse_name = self._base_path.rsplit("/", 1)[-1]

    def normalize_reference(self, reference: str) -> str:
        """Return a container-relative path for a safe CIN reference."""
        if not isinstance(reference, str) or not reference:
            raise CollectorError(f"Invalid resource reference: {reference!r}")
        parsed = urlsplit(reference)
        if parsed.query or parsed.fragment:
            raise CollectorError(f"Unexpected query or fragment in resource reference: {reference!r}")
        if parsed.scheme or parsed.netloc:
            if parsed.scheme != self._base.scheme or parsed.netloc != self._base.netloc:
                raise CollectorError(f"Resource reference is outside the configured CSEBase: {reference!r}")
            if parsed.path.startswith(self._base_path + "/"):
                path = parsed.path[len(self._base_path) + 1:]
            else:
                parent = self._base_path.rsplit("/", 1)[0]
                container_path = parent + "/" + self.config.container + "/"
                if not parsed.path.startswith(container_path):
                    raise CollectorError(f"Resource reference is outside the configured CSEBase: {reference!r}")
                path = parsed.path[len(parent) + 1:]
        else:
            path = parsed.path.lstrip("/")
            if path.startswith(self._cse_name + "/"):
                path = path[len(self._cse_name) + 1:]
        path = _resource_path(path)
        container = _resource_path(self.config.container)
        if path == container or path.startswith(container + "/"):
            return path
        if "/" not in path:
            return container + "/" + path
        raise CollectorError(f"Resource reference is outside the configured container: {reference!r}")

    def resource_url(self, reference: str) -> str:
        return f"{self.config.base_url}/{self.normalize_reference(reference)}"

    def get(self, path: str, params: dict[str, int] | None = None) -> dict:
        url = self.resource_url(path)
        if params:
            url += "?" + urlencode(params)
        request = Request(url, headers={
            "X-M2M-Origin": self.config.origin,
            "X-M2M-RI": f"cindysense-collector-{uuid4().hex}",
            "X-M2M-RVI": "4",
            "Accept": "application/json",
        }, method="GET")
        for attempt in range(3):
            try:
                with urlopen(request, timeout=10) as response:
                    rsc = response.headers.get("X-M2M-RSC", "")
                    data = response.read()
                break
            except HTTPError as exc:
                failure = exc
                retryable = exc.code in (404, 408, 429, 500, 502, 503, 504)
                reason = f"HTTP {exc.code}"
            except (URLError, TimeoutError) as exc:
                failure = exc
                retryable = True
                reason = str(getattr(exc, "reason", exc))
            if not retryable or attempt == 2:
                raise CollectorError(f"GET {url}: {reason} after {attempt + 1} attempt(s)") from failure
            print(f"WARNING: GET {url}: {reason}; retrying ({attempt + 1}/2)",
                  file=sys.stderr)
            time.sleep(0.1 * (attempt + 1))
        if rsc and not rsc.startswith("2"):
            raise CollectorError(f"GET {path}: oneM2M response status {rsc}")
        try:
            payload = json.loads(data)
        except (UnicodeError, ValueError) as exc:
            raise CollectorError(f"GET {path}: malformed response JSON") from exc
        if not isinstance(payload, dict):
            raise CollectorError(f"GET {path}: expected a JSON object")
        return payload

    def list_cins(self, known: set[str] | None = None, first_page_only: bool = False,
                  stop_at: str | None = None,
                  fallback_known: set[str] | None = None) -> list[str]:
        """Read newest-first CINs, bounded by the baseline or session overlap."""
        refs: list[str] = []
        seen_in_listing: set[str] = set()
        fallback_cutoff: int | None = None
        for page in range(self.config.max_pages):
            payload = self.get(self.config.container, {
                "rcn": 6, "ty": 4, "lvl": 1,
                "lim": self.config.page_size, "ofst": page * self.config.page_size,
            })
            rrl = payload.get("m2m:rrl")
            entries = rrl.get("rrf") if isinstance(rrl, dict) else None
            if not isinstance(entries, list):
                raise CollectorError("TinyIoT did not return m2m:rrl.rrf for rcn=6")
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("typ") != 4:
                    raise CollectorError("Unexpected child reference in CIN listing")
                uri = self.normalize_reference(entry.get("val"))
                if not uri.startswith(_resource_path(self.config.container) + "/"):
                    raise CollectorError(f"Unexpected CIN reference: {entry.get('val')!r}")
                if uri in seen_in_listing:
                    continue
                seen_in_listing.add(uri)
                if uri == stop_at:
                    return refs
                if fallback_known is not None and uri in fallback_known:
                    if fallback_cutoff is None:
                        fallback_cutoff = len(refs)
                if known is None or uri not in known:
                    refs.append(uri)
            if first_page_only:
                return refs
            if len(entries) < self.config.page_size:
                if stop_at is not None:
                    if fallback_cutoff is not None:
                        return refs[:fallback_cutoff]
                    raise CollectorError("Baseline CIN disappeared; cannot distinguish new CINs from older history")
                return refs
        raise CollectorError("CIN listing exceeded max_pages; increase it before recording")


@dataclass(frozen=True)
class Sample:
    device_id: str
    sample_t: str
    sample_time_seconds: float
    cin_created_at: str
    cin_ri: str
    values: tuple[float, ...]


def _sample_time(value: object, unit: str) -> tuple[str, float]:
    if isinstance(value, bool):
        raise ValueError("sample t must be numeric or timezone-aware ISO 8601")
    if isinstance(value, (int, float)) and math.isfinite(value):
        return str(value), float(value) / (1000 if unit == "ms" else 1)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return value, parsed.timestamp()
        except ValueError:
            pass
    raise ValueError("sample t must be numeric or timezone-aware ISO 8601")


def parse_cin(payload: dict, unit: str, expected_count: int) -> tuple[list[Sample], list[str]]:
    cin = payload.get("m2m:cin")
    if not isinstance(cin, dict) or "con" not in cin:
        raise ValueError("missing m2m:cin.con")
    content = cin["con"]
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError as exc:
            raise ValueError("malformed JSON in CIN con") from exc
    if not isinstance(content, dict):
        raise ValueError("CIN con must contain a JSON object")
    device = content.get("deviceId")
    rate = content.get("sampleRateHz")
    raw_samples = content.get("samples")
    if not isinstance(device, str) or not device.strip():
        raise ValueError("missing deviceId")
    if (isinstance(rate, bool) or not isinstance(rate, (int, float))
            or not math.isfinite(rate) or rate <= 0):
        raise ValueError("invalid sampleRateHz")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ValueError("missing samples array")
    warnings: list[str] = []
    if len(raw_samples) != expected_count:
        warnings.append(f"batch has {len(raw_samples)} samples; expected {expected_count}")
    if abs(rate - 50) > 0.01:
        warnings.append(f"sampleRateHz is {rate}, expected 50 Hz")
    rows: list[Sample] = []
    for index, raw in enumerate(raw_samples):
        if not isinstance(raw, dict):
            raise ValueError(f"sample {index} is not an object")
        try:
            raw_t, seconds = _sample_time(raw.get("t"), unit)
            values = tuple(float(raw[field]) for field in FIELDS)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"sample {index} has missing or invalid fields: {exc}") from exc
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"sample {index} has a non-finite sensor value")
        rows.append(Sample(device, raw_t, seconds, str(cin.get("ct", "")),
                           str(cin.get("ri", "")), values))
    for previous, current in zip(rows, rows[1:]):
        if current.sample_time_seconds <= previous.sample_time_seconds:
            warnings.append("non-increasing sample timestamp inside batch")
            break
    return rows, warnings


def order_and_check(rows: list[Sample], rate: float = 50) -> tuple[list[Sample], list[str]]:
    ordered = sorted(rows, key=lambda row: (row.sample_time_seconds, row.cin_ri))
    unique: list[Sample] = []
    seen: set[tuple[str, float]] = set()
    warnings: list[str] = []
    previous: Sample | None = None
    for row in ordered:
        key = (row.device_id, row.sample_time_seconds)
        if key in seen:
            warnings.append(f"duplicate sample timestamp {row.sample_t}; skipped")
            continue
        seen.add(key)
        if previous and row.device_id == previous.device_id:
            gap = row.sample_time_seconds - previous.sample_time_seconds
            if gap > 1.5 / rate:
                warnings.append(f"timestamp gap of {gap:.3f}s before {row.sample_t}")
        unique.append(row)
        previous = row
    return unique, warnings


def write_csv(path: Path, rows: list[Sample], session_id: str, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(CSV_FIELDS)
        for row in rows:
            writer.writerow((session_id, label, row.device_id, row.sample_t,
                             row.sample_time_seconds, row.cin_created_at, row.cin_ri,
                             *row.values))


def record(reader: TinyIoTReader, label: str, session_id: str,
           duration: float | None) -> Path:
    config = reader.config
    output = config.output_dir / f"{session_id}.csv"
    if output.exists():
        raise CollectorError(f"Output already exists: {output}")
    baseline_refs = reader.list_cins()
    print(f"Baseline: {len(baseline_refs)} retained CINs. Recording {label} as {session_id}.")
    observed = set(baseline_refs)
    previous_window = set(baseline_refs)
    pending: set[str] = set()
    processed: set[str] = set()
    batches: list[Sample] = []
    warnings: list[str] = []
    selected_device = config.device_id
    started = time.monotonic()

    def poll() -> None:
        nonlocal selected_device, previous_window
        current_refs = reader.list_cins()
        current_window = set(current_refs)
        if previous_window and current_refs and not previous_window.intersection(current_window):
            warnings.append("Discovery windows did not overlap; CINs may have expired "
                            "between polls, so sample continuity is unverified")
        for ref in pending.difference(current_window):
            warnings.append(f"{ref}: disappeared before retrieval; batch lost")
            pending.remove(ref)
        new_refs = [ref for ref in current_refs if ref not in observed]
        pending.update(new_refs)
        observed.update(current_window)
        previous_window = current_window
        retrieved = 0
        for ref in reversed(current_refs):
            if ref not in pending or ref in processed:
                continue
            try:
                payload = reader.get(ref)
            except CollectorError as exc:
                warnings.append(f"{ref}: retrieval failed ({exc}); will retry")
                continue
            try:
                rows, batch_warnings = parse_cin(payload, config.timestamp_unit,
                                                  config.expected_samples_per_batch)
            except ValueError as exc:
                warnings.append(f"{ref}: {exc}; skipped")
                pending.remove(ref)
                processed.add(ref)
                continue
            pending.remove(ref)
            processed.add(ref)
            if not selected_device:
                selected_device = rows[0].device_id
            if rows[0].device_id != selected_device:
                warnings.append(f"{ref}: device {rows[0].device_id} differs from {selected_device}; skipped")
                continue
            batches.extend(rows)
            retrieved += 1
            warnings.extend(f"{ref}: {message}" for message in batch_warnings)
        if retrieved:
            print(f"Retrieved {retrieved} CINs; {len(batches)} raw samples.")

    try:
        while duration is None or time.monotonic() - started < duration:
            try:
                poll()
            except CollectorError as exc:
                warnings.append(f"Discovery failed ({exc}); will retry next poll")
            time.sleep(config.poll_seconds)
    except KeyboardInterrupt:
        print("Recording stopped. Waiting for the final upload...")
    finally:
        try:
            if config.settle_seconds:
                time.sleep(config.settle_seconds)
            poll()
        except CollectorError as exc:
            warnings.append(f"Final poll failed: {exc}")
        for ref in sorted(pending):
            warnings.append(f"{ref}: still unread after final poll; batch lost")
        ordered, checks = order_and_check(batches)
        warnings.extend(checks)
        if not ordered:
            warnings.append("No valid samples were recorded")
        write_csv(output, ordered, session_id, label)
        for warning in warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
        print(f"Saved {len(ordered)} samples to {output}")
    if not ordered:
        raise CollectorError("Session has no valid samples; see warnings and the empty CSV")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("probe", help="check read-only CIN listing and retrieval")
    recording = commands.add_parser("record", help="record a labelled session")
    recording.add_argument("--label", choices=("rest", "squat"), required=True)
    recording.add_argument("--session-id")
    recording.add_argument("--duration", type=float, help="seconds; omit to stop with Ctrl-C")
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        reader = TinyIoTReader(config)
        if args.command == "probe":
            refs = reader.list_cins(first_page_only=True)
            if refs:
                payload = reader.get(refs[0])
                rows, warnings = parse_cin(payload, config.timestamp_unit,
                                           config.expected_samples_per_batch)
                print(f"Read-only probe succeeded: {len(refs)} recent CIN references; "
                      f"latest CIN has {len(rows)} samples.")
                for warning in warnings:
                    print(f"WARNING: {warning}", file=sys.stderr)
            else:
                print("Read-only listing succeeded; container currently has no CINs.")
            return 0
        session_id = args.session_id or f"{args.label}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
        if not SESSION_ID.fullmatch(session_id):
            raise CollectorError("session-id must contain only letters, digits, dot, underscore, or hyphen")
        if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
            raise CollectorError("duration must be a positive number of seconds")
        record(reader, args.label, session_id, args.duration)
        return 0
    except (CollectorError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
