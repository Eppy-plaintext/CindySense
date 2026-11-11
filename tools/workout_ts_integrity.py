"""Offline integrity helpers for workout TimeSeries mirroring and probes.

This module never contacts TinyIoT or creates resources. A TSI represents one
verified workout snapshot; its data-generation time is the source CIN's CSE
creation time, not a claimed physical exercise timestamp.
"""

import json
import re
import hashlib


_CSE_TIMESTAMP = re.compile(r"^\d{8}T\d{6}(?:,\d{1,6})?$")
EXPECTED_EXERCISES = ("pullup", "pushup", "squat")


def verified_workout(cin, workout_id=None, expected_counts=None, rounds=None, duration_seconds=None):
    """Normalize a completed webcam CIN, optionally checking exact expected values."""
    if not isinstance(cin, dict) or not isinstance(cin.get("m2m:cin"), dict):
        raise ValueError("Workout summary is not a CIN")
    instance = cin["m2m:cin"]
    raw_content = instance.get("con")
    try:
        content = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
    except ValueError as exc:
        raise ValueError("Workout summary content is not JSON") from exc
    if not isinstance(content, dict):
        raise ValueError("Workout summary content is not an object")
    identities = [content[key] for key in ("workoutId", "sessionId") if key in content]
    if (not identities or not isinstance(identities[0], str) or not identities[0]
            or any(identity != identities[0] for identity in identities)
            or (workout_id is not None and identities[0] != workout_id)
            or content.get("source") != "webcam"):
        raise ValueError("Workout identity or source mismatch")
    status = content.get("status")
    if not isinstance(status, str) or status.lower() not in ("complete", "completed"):
        raise ValueError("Workout is not complete")
    counts = content.get("reps")
    top_level = {name: content.get(field) for name, field in (
        ("pullup", "pullups"), ("pushup", "pushups"), ("squat", "squats"))
        if field in content}
    if not isinstance(counts, dict):
        counts = top_level
    if any(counts.get(name) != value for name, value in top_level.items()):
        raise ValueError("Top-level counts conflict with reps")
    if set(counts) != set(EXPECTED_EXERCISES) or any(
        type(counts[name]) is not int or counts[name] < 0 or
        (expected_counts is not None and counts[name] != expected_counts[name])
        for name in EXPECTED_EXERCISES
    ):
        raise ValueError("Workout exercise counts mismatch")
    if (type(content.get("rounds")) is not int or content["rounds"] < 0
            or (rounds is not None and content["rounds"] != rounds)):
        raise ValueError("Completed rounds mismatch")
    if (type(content.get("durationSeconds")) is not int
            or content["durationSeconds"] <= 0
            or (duration_seconds is not None and content["durationSeconds"] != duration_seconds)):
        raise ValueError("Duration mismatch")
    created = instance.get("ct")
    if not isinstance(created, str) or not _CSE_TIMESTAMP.fullmatch(created):
        raise ValueError("CSE creation timestamp missing or invalid")
    return {"workoutId": identities[0], "source": "webcam", "reps": counts,
            "rounds": content["rounds"], "durationSeconds": content["durationSeconds"],
            "dataGenerationTime": created}


def tsi_name(workout_id):
    """Stable, URI-safe resource identity across retries and process restarts."""
    return "workout_" + hashlib.sha256(workout_id.encode("utf-8")).hexdigest()[:32]


def tsi_body(verified, name="workout_measurement"):
    """Create a TSI request containing all measurements for one workout."""
    measurements = {key: verified[key] for key in (
        "workoutId", "source", "reps", "rounds", "durationSeconds")}
    measurements["sourceCinCreatedAt"] = verified["dataGenerationTime"]
    return {"m2m:tsi": {"rn": name, "dgt": verified["dataGenerationTime"],
                        "con": json.dumps(measurements, separators=(",", ":"),
                                          sort_keys=True)}}


def verify_tsi_readback(response, verified, name="workout_measurement"):
    """Reject any stored TSI whose timestamp or measurements changed."""
    instance = response.get("m2m:tsi") if isinstance(response, dict) else None
    if not isinstance(instance, dict) or instance.get("rn") != name:
        raise ValueError("TSI identity mismatch")
    expected = tsi_body(verified, name)["m2m:tsi"]
    if instance.get("dgt") != expected["dgt"]:
        raise ValueError("TSI data generation time mismatch")
    try:
        raw_content = instance["con"]
        actual_content = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("TSI content is not JSON") from exc
    if actual_content != json.loads(expected["con"]):
        raise ValueError("TSI workout measurements mismatch")
