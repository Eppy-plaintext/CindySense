"""Optional, idempotent mirror of confirmed webcam summaries into a oneM2M TS.

Provisioning and reconciliation are explicit CLI actions. Normal webcam use
never creates or modifies the TS resource and never changes WorkoutSummary.
"""

import argparse
import json
import os

if __package__:
    from .webcam_tinyiot import (DEFAULT_BASE, SUMMARY_CONTAINER, TinyIoTClient,
                                 TinyIoTConnectionError, TinyIoTError)
    from .workout_ts_integrity import tsi_body, tsi_name, verified_workout, verify_tsi_readback
else:
    from webcam_tinyiot import (DEFAULT_BASE, SUMMARY_CONTAINER, TinyIoTClient,
                                TinyIoTConnectionError, TinyIoTError)
    from workout_ts_integrity import tsi_body, tsi_name, verified_workout, verify_tsi_readback


TS_PATH = "CindySense_AE/WorkoutPerformance_TS"
TS_MNI = 100
TS_MBS = 262144


def checked(response, expected, rsc, label):
    status, actual_rsc, data = response
    if (status, actual_rsc) != (expected, rsc):
        raise TinyIoTError(f"{label}: HTTP {status}, RSC {actual_rsc}; expected {expected}/{rsc}")
    return data


class WorkoutTimeSeries:
    def __init__(self, client, ts_path=TS_PATH):
        self.client = client
        self.ts_path = ts_path.strip("/")

    def provision(self):
        """Create exactly one permanent TS; never update an existing resource."""
        path = "/" + self.ts_path
        status, rsc, data = self.client._request("GET", path)
        if status == 200:
            ts = checked((status, rsc, data), 200, "2000", "GET existing TS").get("m2m:ts")
            if not isinstance(ts, dict) or ts.get("ty") != 29:
                raise TinyIoTError("Existing TS path has another resource type")
            return "reused", ts
        if (status, rsc) != (404, "4004"):
            raise TinyIoTError(f"GET TS: HTTP {status}, RSC {rsc}")
        parent, name = self.ts_path.rsplit("/", 1)
        body = {"m2m:ts": {"rn": name, "mni": TS_MNI, "mbs": TS_MBS}}
        try:
            status, rsc, _ = self.client._request("POST", "/" + parent, body, ty=29)
        except TinyIoTConnectionError:
            status, rsc = None, None  # a committed POST may have lost its reply
        if (status, rsc) not in ((201, "2001"), (409, "4105"), (None, None)):
            raise TinyIoTError(f"POST TS: HTTP {status}, RSC {rsc}")
        ts = checked(self.client._request("GET", path), 200, "2000", "VERIFY TS").get("m2m:ts")
        if not isinstance(ts, dict) or ts.get("ty") != 29 or ts.get("rn") != name:
            raise TinyIoTError("TS readback has wrong identity or type")
        if ts.get("mni") != TS_MNI or ts.get("mbs") != TS_MBS:
            raise TinyIoTError("TS readback has unexpected retention settings")
        return "created" if status == 201 else "verified", ts

    def mirror_source(self, source_path):
        """GET the confirmed summary CIN; create/verify one deterministic TSI."""
        source = "/" + source_path.strip("/")
        if not source.startswith("/" + SUMMARY_CONTAINER + "/"):
            raise TinyIoTError("Source must be a WorkoutSummary CIN")
        source_name = source.rsplit("/", 1)[-1]
        stored = checked(self.client._request("GET", source), 200, "2000", "GET source CIN")
        cin = stored.get("m2m:cin")
        if not isinstance(cin, dict) or cin.get("rn") != source_name:
            raise TinyIoTError("Confirmed summary identity mismatch")
        try:
            workout = verified_workout(stored)
        except ValueError as exc:
            raise TinyIoTError("Stored webcam summary is not a valid completed workout") from exc
        ts = checked(self.client._request("GET", "/" + self.ts_path),
                     200, "2000", "GET WorkoutPerformance TS").get("m2m:ts")
        if not isinstance(ts, dict) or ts.get("ty") != 29:
            raise TinyIoTError("WorkoutPerformance is not a TS")
        name = tsi_name(workout["workoutId"])
        path = "/" + self.ts_path + "/" + name
        status, rsc, data = self.client._request("GET", path)
        if status == 200:
            checked((status, rsc, data), 200, "2000", "GET existing TSI")
            self._verify(data, workout, name)
            return "verified", workout["workoutId"]
        if (status, rsc) != (404, "4004"):
            raise TinyIoTError(f"GET TSI: HTTP {status}, RSC {rsc}")
        try:
            status, rsc, _ = self.client._request(
                "POST", "/" + self.ts_path, tsi_body(workout, name), ty=30)
        except TinyIoTConnectionError:
            status, rsc = None, None
        if (status, rsc) not in ((201, "2001"), (409, "4105"), (None, None)):
            raise TinyIoTError(f"POST TSI: HTTP {status}, RSC {rsc}")
        data = checked(self.client._request("GET", path), 200, "2000", "VERIFY TSI")
        self._verify(data, workout, name)
        return ("created" if status == 201 else "verified"), workout["workoutId"]

    @staticmethod
    def _verify(data, workout, name):
        try:
            verify_tsi_readback(data, workout, name)
        except ValueError as exc:
            raise TinyIoTError("Stored TSI conflicts with confirmed workout summary") from exc

    def reconcile(self, max_items=100):
        """Explicit bounded repair of completed webcam summaries after TS failures."""
        path = "/" + SUMMARY_CONTAINER
        result = checked(self.client._request("GET", path + "?fu=1&rcn=11&ty=4"),
                         200, "2000", "DISCOVER WorkoutSummary CINs")
        refs = result.get("m2m:uril")
        if not isinstance(refs, list) or len(refs) > max_items:
            raise TinyIoTError("Summary discovery exceeds the bounded reconciliation limit")
        prefix = "TinyIoT/" + SUMMARY_CONTAINER + "/"
        results = []
        for ref in refs:
            if not isinstance(ref, str) or not ref.startswith(prefix):
                raise TinyIoTError("Summary discovery returned an unexpected path")
            try:
                outcome, workout_id = self.mirror_source(ref[len("TinyIoT/"):])
            except TinyIoTConnectionError:
                raise  # stop on CSE connectivity failure
            except TinyIoTError as exc:
                results.append({"source": ref.rsplit("/", 1)[-1], "outcome": "failed",
                                "reason": str(exc)})
            else:
                results.append({"source": ref.rsplit("/", 1)[-1],
                                "outcome": outcome, "workoutId": workout_id})
        return results


def main():
    parser = argparse.ArgumentParser(description="Provision or reconcile CindySense workout TS")
    parser.add_argument("action", choices=("provision", "reconcile", "mirror"))
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--origin", default=os.getenv("CINDYSENSE_TS_ORIGIN"))
    parser.add_argument("--source-cin", help="WorkoutSummary CIN path for mirror")
    args = parser.parse_args()
    if not args.origin:
        parser.error("--origin or CINDYSENSE_TS_ORIGIN is required")
    if args.action == "mirror" and not args.source_cin:
        parser.error("mirror requires --source-cin")
    client = TinyIoTClient(args.base, args.origin)
    try:
        series = WorkoutTimeSeries(client)
        if args.action == "provision":
            outcome, ts = series.provision()
            print(json.dumps({"outcome": outcome, "path": TS_PATH, "ty": ts.get("ty"),
                              "mni": ts.get("mni"), "mbs": ts.get("mbs"),
                              "cni": ts.get("cni")}))
        elif args.action == "mirror":
            outcome, workout_id = series.mirror_source(args.source_cin)
            print(json.dumps({"outcome": outcome, "workoutId": workout_id,
                              "tsi": TS_PATH + "/" + tsi_name(workout_id)}))
        else:
            print(json.dumps({"results": series.reconcile()}, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
