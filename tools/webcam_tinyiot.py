"""CindySense webcam -> TinyIoT bridge (oneM2M HTTP, RVI 4).

Copy beside tools/webcam_squat.py. Use Python standard library only.

Run from repository root:
  python tools/webcam_tinyiot.py --origin CWebcam setup
  python tools/webcam_tinyiot.py --origin CWebcam test
  python tools/webcam_tinyiot.py --origin CWebcam latest

Then run webcam_squat.py --tinyiot to publish one pose reading per second,
plus an immediate reading when a squat is counted.
"""

import argparse
import json
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib import error, request

DEFAULT_BASE = os.environ.get("CINDYSENSE_TINYIOT_URL", "http://localhost:3000/TinyIoT")
DEFAULT_ORIGIN = os.environ.get("CINDYSENSE_TINYIOT_ORIGIN", "CAdmin")
AE_NAME = "Webcam_AE"
CNT_NAME = "PoseData"


class TinyIoTError(RuntimeError):
    pass


class TinyIoTClient:
    def __init__(self, base=DEFAULT_BASE, origin=DEFAULT_ORIGIN, timeout=3.0):
        self.base = base.rstrip("/")
        self.origin = origin
        self.timeout = timeout

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
        req = request.Request(self.base + suffix, data=payload, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                result = json.loads(raw) if raw else {}
                return response.status, response.headers.get("X-M2M-RSC"), result
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")[:450]
            return exc.code, exc.headers.get("X-M2M-RSC"), {"error": details}
        except (error.URLError, TimeoutError, OSError) as exc:
            raise TinyIoTError(f"Cannot reach TinyIoT at {self.base}: {exc}") from exc

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
    """Send pose readings off the camera thread; never hold up inference on HTTP."""

    def __init__(self, base=DEFAULT_BASE, origin=DEFAULT_ORIGIN, interval=1.0):
        self.client = TinyIoTClient(base, origin)
        self.interval = interval
        self.last_queued = 0.0
        self.pending = queue.Queue(maxsize=5)
        self.stopping = threading.Event()
        self.worker = threading.Thread(target=self._run, name="tinyiot-webcam", daemon=True)
        self.worker.start()

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

    def close(self):
        self.stopping.set()
        self.worker.join(timeout=6.0)
        if self.worker.is_alive():
            print("[TinyIoT] Publisher stopped with some requests still in progress")


def main():
    parser = argparse.ArgumentParser(description="Set up or check CindySense webcam TinyIoT resources")
    parser.add_argument("command", choices=("setup", "test", "latest"))
    parser.add_argument("--base", default=DEFAULT_BASE, help="CSEBase URL")
    parser.add_argument("--origin", default="CWebcam", help="Registered webcam AE originator (default: CWebcam)")
    parser.add_argument("--admin-origin", default="CAdmin", help="Existing originator allowed to check the CSEBase (setup only)")
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
    except TinyIoTError as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
