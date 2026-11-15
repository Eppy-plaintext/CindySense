"""CindySense webcam -> TinyIoT bridge (oneM2M HTTP, RVI 4).

Copy beside tools/webcam_squat.py. Use Python standard library only.

Run from repository root:
  python tools/webcam_tinyiot.py --origin CWebcam setup
  python tools/webcam_tinyiot.py --origin CWebcam test
  python tools/webcam_tinyiot.py --origin CWebcam latest

Then run webcam_squat.py --tinyiot to publish rate-limited pose readings
and completed repetition events on a separate priority worker.
"""

import argparse
import http.client
import json
import os
from pathlib import Path
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

DEFAULT_BASE = os.environ.get("CINDYSENSE_TINYIOT_URL", "http://127.0.0.1:3001/TinyIoT")
DEFAULT_ORIGIN = os.environ.get("CINDYSENSE_TINYIOT_ORIGIN", "CWebcam")
AE_NAME = "Webcam_AE"
CNT_NAME = "PoseData"
EVENT_CONTAINER = "CindySense_AE/RepetitionCount"
SUMMARY_CONTAINER = "CindySense_AE/WorkoutSummary"


class TinyIoTError(RuntimeError):
    pass


class TinyIoTConnectionError(TinyIoTError):
    pass


class RetryableSummaryError(TinyIoTError):
    pass


class TinyIoTClient:
    def __init__(self, base=DEFAULT_BASE, origin=DEFAULT_ORIGIN, timeout=3.0):
        self.base = base.rstrip("/")
        self.origin = origin
        self.timeout = timeout
        parsed = urlsplit(self.base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise TinyIoTError(f"Invalid TinyIoT URL: {self.base}")
        self._url = parsed
        self._connection = None

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _request(self, method, suffix="", body=None, ty=None):
        headers = {
            "Accept": "application/json",
            "X-M2M-Origin": self.origin,
            "X-M2M-RI": "webcam-" + uuid.uuid4().hex[:14],
            "X-M2M-RVI": "4",
        }
        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json" + (f";ty={ty}" if ty else "")
        try:
            if self._connection is None:
                connection_type = (http.client.HTTPSConnection if self._url.scheme == "https"
                                   else http.client.HTTPConnection)
                # On Windows, localhost can attempt an unavailable IPv6 listener
                # for ~2 seconds on every oneM2M request. TinyIoT is local IPv4.
                host = "127.0.0.1" if self._url.hostname == "localhost" else self._url.hostname
                self._connection = connection_type(host, self._url.port,
                                                   timeout=self.timeout)
            path = self._url.path.rstrip("/") + suffix
            self._connection.request(method, path, body=payload, headers=headers)
            response = self._connection.getresponse()
            raw = response.read()
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                data = {"error": raw.decode("utf-8", errors="replace")[:450]}
            return response.status, response.getheader("X-M2M-RSC"), data
        except (OSError, http.client.HTTPException) as exc:
            self.close()
            raise TinyIoTConnectionError(f"Cannot reach TinyIoT at {self.base}: {exc}") from exc

    @staticmethod
    def _check(status, rsc, data, expected, action):
        if status != expected:
            raise TinyIoTError(f"{action} failed: HTTP {status}, oneM2M RSC={rsc}, body={data}")

    def check_cse(self):
        status, rsc, data = self._request("GET")
        self._check(status, rsc, data, 200, "GET CSEBase")
        return data

    def ensure(self, path, parent, key, ty, content):
        status, rsc, data = self._request("GET", path)
        if status == 200:
            if key not in data:
                raise TinyIoTError(f"Unexpected existing resource at {path}: {data}")
            print(f"[EXISTS] {path}")
            return
        if status != 404:
            raise TinyIoTError(f"Checking {path} failed: HTTP {status}, oneM2M RSC={rsc}, body={data}")
        status, rsc, data = self._request("POST", parent, {key: content}, ty=ty)
        if status == 409:  # Race with another creator, verify existence.
            check_status, _, check_body = self._request("GET", path)
            if check_status == 200 and key in check_body:
                print(f"[EXISTS] {path}")
                return
        self._check(status, rsc, data, 201, f"CREATE {path}")
        print(f"[CREATED] {path}")

    def setup(self, admin_origin="CAdmin"):
        # Register the webcam AE with its unique originator, not with CAdmin.
        # Use an existing privileged originator only to inspect existing resources.
        admin = TinyIoTClient(self.base, admin_origin, self.timeout)
        admin.check_cse()
        path = f"/{AE_NAME}"
        status, rsc, data = admin._request("GET", path)
        if status == 200:
            ae = data.get("m2m:ae")
            if not isinstance(ae, dict):
                raise TinyIoTError(f"Unexpected existing resource at {path}: {data}")
            actual_aei = ae.get("aei")
            if actual_aei != self.origin:
                raise TinyIoTError(
                    f"{path} already exists with AE-ID {actual_aei!r}, but your "
                    f"requested origin is {self.origin!r}. Do not overwrite it; "
                    "check the stored AE-ID before proceeding."
                )
            print(f"[EXISTS] {path} (AE-ID: {actual_aei})")
        elif status == 404:
            body = {"m2m:ae": {
                "rn": AE_NAME, "api": "NCindySenseWebcam", "rr": False, "srv": ["4"]
            }}
            status, rsc, data = self._request("POST", "", body, ty=2)
            self._check(status, rsc, data, 201, f"REGISTER {path}")
            registered_aei = (data.get("m2m:ae") or {}).get("aei")
            if registered_aei and registered_aei != self.origin:
                raise TinyIoTError(
                    f"AE created, but server assigned AE-ID {registered_aei!r}; "
                    f"use --origin {registered_aei} for subsequent requests."
                )
            print(f"[CREATED] {path} (origin: {self.origin})")
        else:
            raise TinyIoTError(
                f"Checking {path} as {admin_origin} failed: HTTP {status}, "
                f"oneM2M RSC={rsc}, body={data}"
            )
        self.ensure(
            f"/{AE_NAME}/{CNT_NAME}", f"/{AE_NAME}", "m2m:cnt", 3,
            {"rn": CNT_NAME, "mni": 300},
        )
        print("Webcam resources ready. Existing CindySense_AE was not modified.")

    def publish_pose(self, pose):
        content = json.dumps(pose, separators=(",", ":"), allow_nan=False)
        payload = {"m2m:cin": {"cnf": "application/json:0", "con": content}}
        status, rsc, data = self._request(
            "POST", f"/{AE_NAME}/{CNT_NAME}", payload, ty=4,
        )
        self._check(status, rsc, data, 201, "POST PoseData CIN")
        return rsc

    def ensure_event_retention(self, admin_origin="CAdmin", minimum=1000):
        admin = TinyIoTClient(self.base, admin_origin, self.timeout)
        path = "/" + EVENT_CONTAINER
        status, _, data = admin._request("GET", path)
        admin._check(status, None, data, 200, "GET repetition container")
        container = data.get("m2m:cnt", {})
        current = container.get("mni")
        if not isinstance(current, int):
            raise TinyIoTError(f"Repetition container has no valid mni: {container}")
        if current < minimum:
            status, rsc, data = admin._request("PUT", path, {"m2m:cnt": {"mni": minimum}})
            admin._check(status, rsc, data, 200, "UPDATE repetition retention")
        status, _, data = admin._request("GET", path)
        admin._check(status, None, data, 200, "VERIFY repetition retention")
        container = data.get("m2m:cnt", {})
        print(f"[TinyIoT] RepetitionCount mni={container.get('mni')} mbs={container.get('mbs')} "
              f"cni={container.get('cni')}; previously evicted events cannot be recovered")

    def publish_event(self, event, container=EVENT_CONTAINER):
        """Create an idempotent event CIN; verify an existing CIN on retry."""
        path = "/" + container.strip("/")
        resource_name = "webcam-" + event["eventId"].replace("-", "")
        item_path = f"{path}/{resource_name}"
        status, _, data = self._request("GET", item_path)
        if status == 200:
            self._verify_event(data, event, item_path)
            return "verified"
        if status != 404:
            raise TinyIoTError(f"Checking event {item_path} failed: HTTP {status}, body={data}")
        payload = {"m2m:cin": {"rn": resource_name, "cnf": "application/json:0",
                                "con": json.dumps(event, separators=(",", ":"))}}
        status, rsc, data = self._request("POST", path, payload, ty=4)
        if status == 201:
            return rsc
        if status in (409, 500, 502, 503, 504):
            # A timeout/server error can mean POST committed but its reply was lost.
            check_status, _, check_body = self._request("GET", item_path)
            if check_status == 200:
                self._verify_event(check_body, event, item_path)
                return "verified"
        self._check(status, rsc, data, 201, "POST repetition event CIN")

    def publish_summary(self, summary, container=SUMMARY_CONTAINER):
        """Store one named CIN per session, with exact readback after ambiguous writes."""
        path = "/" + container.strip("/")
        name = "webcam-summary-" + summary["sessionId"]
        item_path = f"{path}/{name}"
        status, rsc, data = self._request("GET", path)
        self._check_summary(status, rsc, data, 200, "GET workout summary container")
        if "m2m:cnt" not in data:
            raise TinyIoTError("Workout summary resource is not a container")
        status, rsc, data = self._request("GET", item_path)
        if status == 200:
            self._check_summary(status, rsc, data, 200, "GET workout summary CIN")
            self._verify_summary(data, summary, name)
            return "verified"
        if status != 404:
            self._check_summary(status, rsc, data, 404, "GET workout summary CIN")
        body = {"m2m:cin": {"rn": name, "cnf": "application/json:0",
                            "con": json.dumps(summary, separators=(",", ":"), allow_nan=False)}}
        status, rsc, data = self._request("POST", path, body, ty=4)
        if status not in (201, 409, 500, 502, 503, 504):
            self._check_summary(status, rsc, data, 201, "POST workout summary CIN")
        # A successful or ambiguous POST is only confirmed by the named resource.
        check_status, check_rsc, check_data = self._request("GET", item_path)
        if check_status == 200:
            self._check_summary(check_status, check_rsc, check_data, 200,
                                "VERIFY workout summary CIN")
            self._verify_summary(check_data, summary, name)
            return "created" if status == 201 else "verified"
        if check_status != 404:
            self._check_summary(check_status, check_rsc, check_data, 200, "VERIFY workout summary CIN")
        if status == 201:
            raise RetryableSummaryError("Workout summary POST succeeded but readback was not found")
        raise RetryableSummaryError(f"Workout summary POST returned HTTP {status} without readable CIN")

    @staticmethod
    def _check_summary(status, rsc, data, expected, action):
        if status in (500, 502, 503, 504):
            raise RetryableSummaryError(f"{action} returned HTTP {status}, oneM2M RSC={rsc}")
        TinyIoTClient._check(status, rsc, data, expected, action)
        expected_rsc = {200: "2000", 201: "2001"}.get(expected)
        if expected_rsc and rsc and rsc != expected_rsc:
            raise TinyIoTError(f"{action} returned unexpected oneM2M RSC={rsc}")

    @staticmethod
    def _verify_summary(data, summary, name):
        cin = data.get("m2m:cin", {})
        content = cin.get("con")
        try:
            content = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            pass
        if cin.get("rn", name) != name or content != summary:
            raise TinyIoTError(f"Workout summary readback mismatch for session {summary['sessionId']}")

    @staticmethod
    def _verify_event(data, event, path):
        cin = data.get("m2m:cin", {})
        content = cin.get("con")
        try:
            content = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            pass
        if cin.get("rn", path.rsplit("/", 1)[-1]) != path.rsplit("/", 1)[-1] or content != event:
            raise TinyIoTError(f"Event readback mismatch at {path}; refusing to confirm delivery")

    def latest(self):
        status, rsc, data = self._request("GET", f"/{AE_NAME}/{CNT_NAME}/la")
        self._check(status, rsc, data, 200, "GET latest PoseData CIN")
        if "m2m:cin" not in data:
            raise TinyIoTError(f"Unexpected latest response: {data}")
        cin = data["m2m:cin"]
        con = cin.get("con")
        try:
            value = json.loads(con) if isinstance(con, str) else con
        except json.JSONDecodeError:
            value = con
        return {"resourceName": cin.get("rn"), "content": value}


class PosePublisher:
    """Send pose and repetition events off the camera thread."""

    def __init__(self, base=DEFAULT_BASE, origin=DEFAULT_ORIGIN, interval=1.0,
                 event_container=EVENT_CONTAINER, event_origin=None, event_spool=None,
                 shutdown_grace=10.0):
        self.client = TinyIoTClient(base, origin)
        self.event_client = TinyIoTClient(base, event_origin or origin)
        self.interval = interval
        self.event_container = event_container
        self.shutdown_grace = shutdown_grace
        self.last_queued = 0.0
        self.pending = queue.Queue(maxsize=5)
        # A full 20-minute offline Cindy session must fit while retries continue.
        self.events = queue.Queue(maxsize=20000)
        self.event_spool = Path(event_spool) if event_spool else Path(__file__).resolve().parent.parent / "data" / "recordings" / "webcam_pending_events.json"
        self.event_overflow = 0
        self.event_confirmed = 0
        self.event_retries = 0
        self.event_queued_at = {}
        self.event_last_latency_ms = None
        self.event_max_latency_ms = 0
        self.stopping = threading.Event()
        self.shutdown_deadline = None
        self.inflight_event = None
        if self.event_spool.is_file():
            try:
                pending = json.loads(self.event_spool.read_text(encoding="utf-8"))
                if not isinstance(pending, list) or len(pending) > 20001:
                    raise ValueError("spool is not a bounded event list")
                self.events.maxsize = max(self.events.maxsize, len(pending))
                for event in pending:
                    if not isinstance(event, dict) or not event.get("eventId"):
                        raise ValueError("spool contains an invalid event")
                    self.events.put_nowait(event)
                print(f"[TinyIoT] Restored {len(pending)} pending repetition events")
            except (OSError, ValueError, queue.Full) as exc:
                raise TinyIoTError(f"Cannot restore pending event spool {self.event_spool}: {exc}") from exc
        self.worker = threading.Thread(target=self._run, name="tinyiot-webcam", daemon=True)
        self.event_worker = threading.Thread(target=self._run_events, name="tinyiot-events", daemon=True)
        self.worker.start()
        self.event_worker.start()

    def offer(self, data, important=False):
        now = time.monotonic()
        if not important and now - self.last_queued < self.interval:
            return
        self.last_queued = now
        try:
            self.pending.put_nowait((now, data))
        except queue.Full:
            try:
                self.pending.get_nowait()  # Drop stale measurements, never block camera.
            except queue.Empty:
                pass
            try:
                self.pending.put_nowait((now, data))
            except queue.Full:
                pass

    def _run(self):
        while not self.stopping.is_set() or not self.pending.empty():
            try:
                queued_at, pose = self.pending.get(timeout=0.2)
            except queue.Empty:
                continue
            if time.monotonic() - queued_at > 4.0:
                print("[TinyIoT] Dropped stale webcam reading")
                continue
            try:
                self.client.publish_pose(pose)
                print(f"[TinyIoT] HTTP 201 | phase={pose.get('phase')} reps={pose.get('repCount')}")
            except TinyIoTError as exc:
                print(f"[TinyIoT] Upload failed: {exc}")

    def offer_event(self, event):
        try:
            self.events.put_nowait(event)
            getattr(self, "event_queued_at", {})[event["eventId"]] = time.monotonic()
            print(f"[TinyIoT] Event queued at={datetime.now(timezone.utc).isoformat(timespec='milliseconds')} "
                  f"id={event.get('eventId')} seq={event.get('eventSequence')} "
                  f"{event.get('exercise')} rep={event.get('repetitions')} depth={self.events.qsize()}")
            return True
        except queue.Full:
            self.event_overflow += 1
            print(f"[TinyIoT] EVENT QUEUE FULL; overflow={self.event_overflow}; repetition counted locally but event not queued")
            return False

    def diagnostics(self):
        return {"eventQueueDepth": self.events.qsize() + int(self.inflight_event is not None),
                "eventConfirmed": self.event_confirmed, "eventRetries": self.event_retries,
                "eventOverflow": self.event_overflow,
                "eventLastLatencyMs": getattr(self, "event_last_latency_ms", None),
                "eventMaxLatencyMs": getattr(self, "event_max_latency_ms", 0)}

    def _run_events(self):
        delay = 1.0
        while not self.stopping.is_set() or not self.events.empty():
            if self.shutdown_deadline is not None and time.monotonic() >= self.shutdown_deadline:
                return
            try:
                event = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            self.inflight_event = event
            while True:
                if self.shutdown_deadline is not None and time.monotonic() >= self.shutdown_deadline:
                    self.events.put_nowait(event)
                    self.inflight_event = None
                    return
                try:
                    result = self.event_client.publish_event(event, self.event_container)
                    self.event_confirmed += 1
                    self.inflight_event = None
                    queued_at = getattr(self, "event_queued_at", {}).pop(event.get("eventId"), None)
                    if queued_at is not None:
                        self.event_last_latency_ms = round((time.monotonic() - queued_at) * 1000, 1)
                        self.event_max_latency_ms = max(getattr(self, "event_max_latency_ms", 0), self.event_last_latency_ms)
                    detected_at = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
                    age_ms = round((datetime.now(timezone.utc) - detected_at).total_seconds() * 1000)
                    print(f"[TinyIoT] Event confirmed at={datetime.now(timezone.utc).isoformat(timespec='milliseconds')} "
                          f"id={event.get('eventId')} seq={event.get('eventSequence')} "
                          f"ageMs={age_ms} depth={self.events.qsize()} ({result})")
                    delay = 1.0
                    break
                except TinyIoTError as exc:
                    self.event_retries += 1
                    print(f"[TinyIoT] Event retry seq={event.get('eventSequence')} in {delay:.0f}s: {exc}")
                    if self.shutdown_deadline is not None:
                        remaining = self.shutdown_deadline - time.monotonic()
                        if remaining <= 0:
                            self.events.put_nowait(event)
                            self.inflight_event = None
                            return
                        time.sleep(min(delay, remaining))
                    else:
                        self.stopping.wait(delay)
                    delay = min(delay * 2, 30.0)

    def close(self):
        self.shutdown_deadline = time.monotonic() + self.shutdown_grace
        self.stopping.set()
        self.worker.join(timeout=6.0)
        self.event_worker.join(timeout=20.0)
        if not self.worker.is_alive():
            self.client.close()
        if not self.event_worker.is_alive():
            self.event_client.close()
        if self.worker.is_alive():
            print("[TinyIoT] Publisher stopped with some requests still in progress")
        if self.event_worker.is_alive():
            print("[TinyIoT] Event worker still in request; spooling its in-flight event for idempotent retry")
        with self.events.mutex:
            pending = list(self.events.queue)
        if self.inflight_event is not None:
            pending.append(self.inflight_event)
        pending = list({event["eventId"]: event for event in pending}.values())
        spool_status = "none"
        try:
            if pending:
                self.event_spool.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.event_spool.with_suffix(".tmp")
                temporary.write_text(json.dumps(pending, separators=(",", ":")), encoding="utf-8")
                os.replace(temporary, self.event_spool)
                spool_status = f"saved:{len(pending)}"
                print(f"[TinyIoT] Saved {len(pending)} undelivered events for next run")
            elif self.event_spool.exists():
                self.event_spool.unlink()
        except OSError as exc:
            spool_status = "FAILED"
            print(f"[TinyIoT] ERROR saving undelivered events: {exc}")
        print(f"[TinyIoT] Event summary confirmed={self.event_confirmed} "
              f"queued={self.events.qsize()} retries={self.event_retries} "
              f"overflow={self.event_overflow} spool={spool_status}; "
              "a crash before shutdown can lose RAM-only events")


def main():
    parser = argparse.ArgumentParser(description="Set up or check CindySense webcam TinyIoT resources")
    parser.add_argument("command", choices=("setup", "test", "latest", "retention"))
    parser.add_argument("--base", default=DEFAULT_BASE, help="CSEBase URL")
    parser.add_argument("--origin", default="CWebcam", help="Registered webcam AE originator (default: CWebcam)")
    parser.add_argument("--admin-origin", default="CAdmin", help="Existing originator allowed to check the CSEBase (setup only)")
    parser.add_argument("--min-events", type=int, default=1000, help="Minimum retained repetition events")
    args = parser.parse_args()
    client = TinyIoTClient(args.base, args.origin)
    try:
        if args.command == "setup":
            client.setup(args.admin_origin)
        elif args.command == "test":
            client.publish_pose({
                "deviceId": "CindySense_Webcam_01",
                "source": "webcam_test_only",
                "synthetic": True,
                "timestampUtc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "exercise": "squat", "kneeAngleDeg": 170.0,
                "trackedLeg": "LEFT", "visibility": 0.90,
                "phase": "UP", "repCount": 0,
            })
            print("Test CIN created (HTTP 201). It is marked synthetic=true.")
        elif args.command == "latest":
            print(json.dumps(client.latest(), indent=2))
        elif args.command == "retention":
            if args.min_events < 100 or args.min_events > 2000:
                raise TinyIoTError("--min-events must be between 100 and 2000")
            client.ensure_event_retention(args.admin_origin, args.min_events)
    except TinyIoTError as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
