
import json
import uuid
import urllib.request

BASE = "http://127.0.0.1:3000/TinyIoT"

def request(method, path, origin, data=None, ty=None):
    headers = {
        "X-M2M-Origin": origin,
        "X-M2M-RI": str(uuid.uuid4()),
        "X-M2M-RVI": "4",
        "Accept": "application/json",
    }

    body = None

    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = (
            f"application/json;ty={ty}"
            if ty else "application/json"
        )

    req = urllib.request.Request(
        BASE + path,
        data=body,
        headers=headers,
        method=method,
    )

    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode())

# Read latest CindySense workout
result = request(
    "GET",
    "/CindySense_AE/WorkoutSummary/la",
    "CCindyPT",
)

workout = json.loads(result["m2m:cin"]["con"])

rounds = workout["rounds"]
duration = workout["durationSeconds"]

# Basic rule-based coaching
if rounds < 3:
    advice = "Focus on consistent form and build endurance gradually."
elif rounds < 7:
    advice = "Good progress. Aim for one additional round over time."
else:
    advice = "Strong performance. Maintain technique and prioritize recovery."

recommendation = {
    "roundsCompleted": rounds,
    "durationSeconds": duration,
    "recommendation": advice,
    "source": "rule-based",
}

# Send recommendation to TinyIoT
response = request(
    "POST",
    "/CindyPT_AE/AIRecommendations",
    "CCindyPT",
    {
        "m2m:cin": {
            "con": json.dumps(recommendation)
        }
    },
    ty=4,
)

print("CindyPT recommendation created!")
print(json.dumps(response, indent=2))
