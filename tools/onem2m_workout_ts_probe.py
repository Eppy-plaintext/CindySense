"""Opt-in live TS/TSI test from a verified stored workout; never touches production.

Run from the repository root with `python -m tools.onem2m_workout_ts_probe`.
If the CSE stops responding, the probe makes no further requests, including
cleanup; it prints the unique path for operator cleanup after recovery.
"""

import hashlib
import json
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from tools.onem2m_advanced_probe import PARENT, request
from tools.workout_ts_integrity import tsi_body, verified_workout, verify_tsi_readback


WORKOUT_ID = "final-summary-097c0172"
COUNTS = {"pullup": 5, "pushup": 10, "squat": 15}
PRODUCTION_PATHS = {
    "WorkoutSummary": f"{PARENT}/WorkoutSummary",
    "RepetitionCount": f"{PARENT}/RepetitionCount",
    "WorkoutMonitoring_GRP": f"{PARENT}/WorkoutMonitoring_GRP",
    "RepCount_Sub": f"{PARENT}/RepetitionCount/RepCount_Sub",
    "Summary_Sub": f"{PARENT}/WorkoutSummary/Summary_Sub",
}
API_PATHS = ("/api/workout/snapshot", "/api/cindypt/progress")


def fingerprint(data):
    """Compare resource representations without printing callback URIs/tokens."""
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def api_status(path):
    try:
        with urlopen("http://127.0.0.1:8000" + path, timeout=5) as response:
            response.read()
            return response.status
    except HTTPError as exc:
        return exc.code
    except (URLError, TimeoutError, OSError):
        return None


def main():
    root = f"{PARENT}/WorkoutTSProbe_{uuid.uuid4().hex[:12]}"
    ts_path = root + "/WorkoutSeries"
    tsi_path = ts_path + "/workout_measurement"
    records = []
    created = False
    healthy = True
    baseline = None
    test_started = False

    def call(label, method, path, *, body=None, ty=None, expected=(200, "2000")):
        nonlocal healthy, created
        try:
            status, rsc, data = request(method, path, body, ty)
        except (URLError, TimeoutError, OSError) as exc:
            healthy = False
            raise RuntimeError(f"TinyIoT unresponsive during {label}; stopping") from exc
        records.append({"case": label, "http": status, "rsc": rsc})
        if label == "probe root POST" and (status, rsc) == (201, "2001"):
            created = True
        if (status, rsc) != expected:
            raise RuntimeError(f"Unexpected {label}: HTTP {status}, RSC {rsc}")
        if method != "GET":
            try:
                status, rsc, _ = request("GET", PARENT)
            except (URLError, TimeoutError, OSError) as exc:
                healthy = False
                raise RuntimeError(f"TinyIoT unresponsive after {label}; stopping") from exc
            if (status, rsc) != (200, "2000"):
                healthy = False
                raise RuntimeError(f"TinyIoT unhealthy after {label}: HTTP {status}, RSC {rsc}")
        return data

    try:
        baseline = {name: fingerprint(call(name + " preflight GET", "GET", path))
                    for name, path in PRODUCTION_PATHS.items()}
        call("group fopt preflight GET", "GET",
             PRODUCTION_PATHS["WorkoutMonitoring_GRP"] + "/fopt")
        for path in API_PATHS:
            status = api_status(path)
            records.append({"case": path + " preflight GET", "http": status, "rsc": None})
            if status != 200:
                raise RuntimeError(f"FastAPI preflight failed: {path} HTTP {status}")
        summary = call("latest summary GET", "GET", PRODUCTION_PATHS["WorkoutSummary"] + "/la")
        verified = verified_workout(summary, WORKOUT_ID, COUNTS, 1, 1200)
        source_ri = summary["m2m:cin"]["ri"]
        records.append({"case": "stored workout integrity", "passed": True,
                        "sourceCinRi": source_ri,
                        "sourceCinCreatedAt": verified["dataGenerationTime"]})

        # A UUID name prevents collisions. Never create a CNT under the CSE root.
        call("probe root POST", "POST", PARENT,
             body={"m2m:cnt": {"rn": root.rsplit("/", 1)[-1]}}, ty=3,
             expected=(201, "2001"))
        test_started = True
        call("probe root GET", "GET", root)
        call("TS POST", "POST", root,
             body={"m2m:ts": {"rn": "WorkoutSeries", "mni": 3, "mbs": 4096}}, ty=29,
             expected=(201, "2001"))
        ts = call("TS initial GET", "GET", ts_path)["m2m:ts"]
        if ts.get("ty") != 29 or ts.get("cni") != 0:
            raise RuntimeError("TS initial type or count mismatch")
        records.append({"case": "TS initial metadata", "ty": ts["ty"],
                        "cni": ts["cni"], "mni": ts.get("mni"), "mbs": ts.get("mbs")})

        call("TSI POST", "POST", ts_path, body=tsi_body(verified), ty=30,
             expected=(201, "2001"))
        readback = call("TSI GET", "GET", tsi_path)
        verify_tsi_readback(readback, verified)
        records.append({"case": "TSI data integrity", "passed": True,
                        "workoutId": verified["workoutId"],
                        "sourceCinCreatedAt": verified["dataGenerationTime"]})
        ts = call("TS with TSI GET", "GET", ts_path)["m2m:ts"]
        if ts.get("cni") != 1 or not isinstance(ts.get("cbs"), int) or ts["cbs"] <= 0:
            raise RuntimeError("TS instance count or byte count mismatch")
        records.append({"case": "TS after TSI metadata", "cni": ts["cni"],
                        "cbs": ts["cbs"]})

        call("TS PUT", "PUT", ts_path,
             body={"m2m:ts": {"lbl": ["disposable-workout-test"]}},
             expected=(200, "2004"))
        ts = call("TS updated GET", "GET", ts_path)["m2m:ts"]
        if "disposable-workout-test" not in ts.get("lbl", []) or ts.get("cni") != 1:
            raise RuntimeError("TS label update or count readback mismatch")
        call("TSI DELETE", "DELETE", tsi_path, expected=(200, "2002"))
        ts = call("TS after TSI DELETE GET", "GET", ts_path)["m2m:ts"]
        if ts.get("cni") != 0 or ts.get("cbs") != 0:
            raise RuntimeError("TS counters did not reset after TSI delete")
        records.append({"case": "TS final metadata", "cni": ts["cni"], "cbs": ts["cbs"]})
        call("TS DELETE", "DELETE", ts_path, expected=(200, "2002"))
        call("TS absent GET", "GET", ts_path, expected=(404, "4004"))
    finally:
        if created and healthy:
            try:
                call("probe root DELETE", "DELETE", root, expected=(200, "2002"))
                call("probe root absent GET", "GET", root, expected=(404, "4004"))
                created = False
            except Exception as exc:
                records.append({"case": "cleanup failure", "error": str(exc)})
        if healthy and test_started and baseline is not None:
            try:
                for name, path in PRODUCTION_PATHS.items():
                    data = call(name + " final GET", "GET", path)
                    records.append({"case": name + " unchanged",
                                    "passed": fingerprint(data) == baseline[name]})
                call("group fopt final GET", "GET",
                     PRODUCTION_PATHS["WorkoutMonitoring_GRP"] + "/fopt")
                for path in API_PATHS:
                    records.append({"case": path + " final GET",
                                    "http": api_status(path), "rsc": None})
            except Exception as exc:
                records.append({"case": "final health failure", "error": str(exc)})
        print(json.dumps({"test_root": root, "leftover_test_subtree": created,
                          "cse_unresponsive": not healthy, "records": records}, indent=2))


if __name__ == "__main__":
    main()
