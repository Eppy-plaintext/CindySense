import json
import unittest

from tools.webcam_tinyiot import TinyIoTClient, TinyIoTError


class EventUploadTests(unittest.TestCase):
    def setUp(self):
        self.client = TinyIoTClient()
        self.event = {
            "eventId": "a18a9ce4-684d-45bc-944e-81dc7442828c", "eventSequence": 1,
            "sessionId": "webcam-20261105T120000Z", "exercise": "pullup",
            "repetitions": 1, "round": 1, "timestamp": "2026-11-05T12:00:00+00:00",
            "source": "webcam",
        }

    def test_posts_under_stable_idempotent_name(self):
        calls = []

        def request(method, suffix="", body=None, ty=None):
            calls.append((method, suffix, body, ty))
            return (404, None, {}) if method == "GET" else (201, "2001", {})

        self.client._request = request
        self.assertEqual(self.client.publish_event(self.event), "2001")
        self.assertEqual(calls[0][1], "/CindySense_AE/RepetitionCount/webcam-a18a9ce4684d45bc944e81dc7442828c")
        self.assertEqual(calls[1][3], 4)
        self.assertEqual(json.loads(calls[1][2]["m2m:cin"]["con"]), self.event)

    def test_ambiguous_post_is_confirmed_by_exact_readback(self):
        resource_name = "webcam-" + self.event["eventId"].replace("-", "")
        cin = {"rn": resource_name, "con": json.dumps(self.event)}
        calls = iter([(404, None, {}), (500, None, {}), (200, None, {"m2m:cin": cin})])
        self.client._request = lambda *args, **kwargs: next(calls)
        self.assertEqual(self.client.publish_event(self.event), "verified")

    def test_mismatched_readback_is_not_confirmed(self):
        self.client._request = lambda *args, **kwargs: (200, None, {"m2m:cin": {"con": "{}"}})
        with self.assertRaises(TinyIoTError):
            self.client.publish_event(self.event)


if __name__ == "__main__":
    unittest.main()
