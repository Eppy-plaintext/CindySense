"""Offline regression checks for the disposable advanced oneM2M probe."""

import unittest
from unittest.mock import patch

from tools import onem2m_advanced_probe as probe


class FakeResponse:
    status = 200
    headers = {"X-M2M-RSC": "2000"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"m2m:uril":["/TinyIoT/CindySense_AE/probe/labeled"]}'


class AdvancedProbeTests(unittest.TestCase):
    def test_uri_names_extract_only_discovery_list(self):
        self.assertEqual(probe.uri_names({"m2m:uril": ["/a/labeled", "/a/plain/"]}),
                         {"labeled", "plain"})
        self.assertEqual(probe.uri_names({"m2m:uril": []}), set())
        with self.assertRaises(RuntimeError):
            probe.uri_names({"m2m:uril": "bad"})

    @patch.object(probe, "urlopen", return_value=FakeResponse())
    def test_discovery_request_encodes_filters_and_required_headers(self, mocked):
        status, rsc, data = probe.request("GET", "CindySense_AE/probe",
                                          query={"fu": 1, "rcn": 11, "lbl": "a b", "ty": 3})
        self.assertEqual((status, rsc), (200, "2000"))
        self.assertEqual(probe.uri_names(data), {"labeled"})
        req = mocked.call_args.args[0]
        self.assertIn("lbl=a+b", req.full_url)
        self.assertIn("ty=3", req.full_url)
        self.assertEqual(req.get_header("X-m2m-rvi"), "4")
        self.assertEqual(req.get_method(), "GET")

    @patch.object(probe, "request", return_value=(503, "5000", {}))
    def test_health_guard_stops_on_server_error(self, _mocked):
        with self.assertRaisesRegex(RuntimeError, "health changed"):
            probe.checked_health()

    @patch.object(probe, "request", return_value=(200, "2000", {"m2m:ae": {}}))
    def test_health_guard_accepts_live_one_m2m_success(self, _mocked):
        probe.checked_health()


if __name__ == "__main__":
    unittest.main()
