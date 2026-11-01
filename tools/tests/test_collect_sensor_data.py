import csv
import json
from urllib.error import HTTPError
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import MagicMock, Mock, patch

from tools.collect_sensor_data import (CollectorError, Config, TinyIoTReader,
                                       order_and_check, parse_cin, record)


def cin(timestamps, con_as_string=True):
    content = {
        "deviceId": "chest-1",
        "sampleRateHz": 50,
        "samples": [
            {"t": t, "ax": 1, "ay": 2, "az": 3, "gx": 4, "gy": 5, "gz": 6}
            for t in timestamps
        ],
    }
    return {"m2m:cin": {"ri": "cin-1", "ct": "20261029T120000",
                        "con": json.dumps(content) if con_as_string else content}}


class CollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Path("tmp").mkdir(exist_ok=True)

    def config(self, output_dir):
        return Config("http://localhost:3000/TinyIoT", "read-origin",
                      "CindySense_AE/SensorData", Path(output_dir), 0.001, 0,
                      150, 3, 50, "ms", "")

    def test_parses_batch_and_detects_gap_and_duplicate(self):
        rows, warnings = parse_cin(cin([0, 20, 60]), "ms", 50)
        self.assertEqual(len(rows), 3)
        self.assertTrue(any("expected 50" in warning for warning in warnings))
        ordered, warnings = order_and_check(rows + rows[:1])
        self.assertEqual([row.sample_t for row in ordered], ["0", "20", "60"])
        self.assertTrue(any("duplicate" in warning for warning in warnings))
        self.assertTrue(any("gap" in warning for warning in warnings))

    def test_timestamp_reset_is_reported_in_original_order(self):
        before, _ = parse_cin(cin([1000, 1020]), "ms", 2)
        after_payload = cin([0, 20])
        after_payload["m2m:cin"]["ri"] = "after-reboot"
        after, _ = parse_cin(after_payload, "ms", 2)
        original = before + after
        ordered, warnings = order_and_check(original)
        self.assertEqual(ordered, original)
        self.assertEqual(len(warnings), 1)
        self.assertIn("timestamp reset detected in original sample order", warnings[0])
        self.assertIn("1020 (cin-1) -> 0 (after-reboot)", warnings[0])

    def test_boot_nonce_change_detects_reset_even_when_timestamps_overlap(self):
        payload_a = cin([1000, 1020])
        payload_a["m2m:cin"]["ri"] = "cs-012345abcdef-00000001-00000001"
        payload_b = cin([0, 20])
        payload_b["m2m:cin"]["ri"] = "cs-012345abcdef-00000002-00000001"
        before, _ = parse_cin(payload_a, "ms", 2)
        after, _ = parse_cin(payload_b, "ms", 2)
        ordered, warnings = order_and_check(before + after)
        self.assertEqual(ordered, before + after)
        self.assertTrue(any("firmware boot identity changed" in warning
                            for warning in warnings))

    def test_record_rejects_reset_session_without_writing_replay_csv(self):
        with tempfile.TemporaryDirectory(dir="tmp") as directory:
            reader = TinyIoTReader(self.config(directory))
            prefix = "CindySense_AE/SensorData/"
            names = [prefix + "cs-012345abcdef-00000002-00000001",
                     prefix + "cs-012345abcdef-00000001-00000001"]

            def fake_get(ref):
                payload = cin(range(1000, 2000, 20) if ref.endswith("-00000001-00000001")
                              else range(0, 1000, 20))
                payload["m2m:cin"]["ri"] = ref.rsplit("/", 1)[-1]
                return payload

            ticks = iter((0.0, 0.0, 1.0))
            output = Path(directory) / "reset-session.csv"
            stderr = StringIO()
            with patch.object(reader, "list_cins", side_effect=[[], names, names]), \
                 patch.object(reader, "get", side_effect=fake_get), \
                 patch("tools.collect_sensor_data.time.monotonic",
                       side_effect=lambda: next(ticks)), \
                 patch("tools.collect_sensor_data.time.sleep"), \
                 redirect_stderr(stderr), \
                 self.assertRaisesRegex(CollectorError, "normal replay CSV was not written"):
                record(reader, "squat", "reset-session", 0.5)
            self.assertFalse(output.exists())
            quarantined = Path(directory) / "reset-session.invalid.csv"
            self.assertTrue(quarantined.exists())
            with quarantined.open(newline="", encoding="utf-8") as file:
                recorded = list(csv.DictReader(file))
            self.assertEqual([row["sample_t"] for row in recorded[:2]],
                             ["1000", "1020"])
            self.assertEqual([row["sample_t"] for row in recorded[50:52]],
                             ["0", "20"])
            self.assertIn("timestamp reset detected", stderr.getvalue())

    def test_malformed_cin_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "malformed JSON"):
            parse_cin({"m2m:cin": {"con": "{"}}, "ms", 50)
        bad = cin([0], con_as_string=False)
        del bad["m2m:cin"]["con"]["samples"][0]["gx"]
        with self.assertRaisesRegex(ValueError, "sample 0"):
            parse_cin(bad, "ms", 50)

    def test_paginates_references_and_stops_at_baseline(self):
        with tempfile.TemporaryDirectory(dir="tmp") as directory:
            reader = TinyIoTReader(self.config(directory))
            prefix = "TinyIoT/CindySense_AE/SensorData/"
            calls = []

            def fake_get(path, params=None):
                calls.append(params["ofst"])
                names = [f"new-{i}" for i in range(151)] if params["ofst"] == 0 else ["new-150", "old"]
                return {"m2m:rrl": {"rrf": [
                    {"nm": name, "typ": 4, "val": prefix + name} for name in names
                ]}}

            with patch.object(reader, "get", side_effect=fake_get):
                refs = reader.list_cins(known={"CindySense_AE/SensorData/old"},
                                        stop_at="CindySense_AE/SensorData/old")
            self.assertEqual(len(refs), 151)
            self.assertEqual(refs[0], "CindySense_AE/SensorData/new-0")
            self.assertEqual(calls, [0, 150])

    def test_rejects_unsupported_listing_shape(self):
        with tempfile.TemporaryDirectory(dir="tmp") as directory:
            reader = TinyIoTReader(self.config(directory))
            with patch.object(reader, "get", return_value={"m2m:cnt": {}}):
                with self.assertRaisesRegex(CollectorError, "m2m:rrl.rrf"):
                    reader.list_cins(first_page_only=True)

    def test_seen_newer_cin_does_not_hide_unseen_older_cin_on_next_page(self):
        reader = TinyIoTReader(self.config("tmp"))
        reader.config = Config("http://localhost:3000/TinyIoT", "read-origin",
                               "CindySense_AE/SensorData", Path("tmp"), 0.001, 0,
                               2, 3, 50, "ms", "")
        prefix = "TinyIoT/CindySense_AE/SensorData/"
        pages = {
            0: ["new-3", "new-2"],
            2: ["new-2", "new-1", "baseline"],
            4: ["baseline", "historical"],
        }

        def fake_get(path, params=None):
            return {"m2m:rrl": {"rrf": [
                {"typ": 4, "val": prefix + name}
                for name in pages[params["ofst"]]
            ]}}

        with patch.object(reader, "get", side_effect=fake_get) as get:
            refs = reader.list_cins(
                known={"CindySense_AE/SensorData/new-3",
                       "CindySense_AE/SensorData/baseline"},
                stop_at="CindySense_AE/SensorData/baseline")
        self.assertEqual(refs, ["CindySense_AE/SensorData/new-2",
                                "CindySense_AE/SensorData/new-1"])
        self.assertEqual([call.args[1]["ofst"] for call in get.call_args_list], [0, 2])

    def test_does_not_include_historical_cins_after_baseline_in_same_page(self):
        reader = TinyIoTReader(self.config("tmp"))
        prefix = "TinyIoT/CindySense_AE/SensorData/"
        with patch.object(reader, "get", return_value={"m2m:rrl": {"rrf": [
                {"typ": 4, "val": prefix + name}
                for name in ("new", "baseline", "historical")
        ]}}):
            refs = reader.list_cins(known={"CindySense_AE/SensorData/baseline"},
                                    stop_at="CindySense_AE/SensorData/baseline")
        self.assertEqual(refs, ["CindySense_AE/SensorData/new"])

    def test_stops_if_baseline_disappears(self):
        reader = TinyIoTReader(self.config("tmp"))
        with patch.object(reader, "get", return_value={"m2m:rrl": {"rrf": [
            {"nm": "new", "typ": 4,
             "val": "TinyIoT/CindySense_AE/SensorData/new"}
        ]}}):
            with self.assertRaisesRegex(CollectorError, "Baseline CIN disappeared"):
                reader.list_cins(known={"CindySense_AE/SensorData/old"},
                                 stop_at="CindySense_AE/SensorData/old",
                                 fallback_known={"CindySense_AE/SensorData/newer-gone"})

    def test_uses_session_overlap_when_baseline_expires_across_pages(self):
        reader = TinyIoTReader(self.config("tmp"))
        reader.config = Config("http://localhost:3000/TinyIoT", "read-origin",
                               "CindySense_AE/SensorData", Path("tmp"), 0.001, 0,
                               2, 5, 50, "ms", "")
        prefix = "TinyIoT/CindySense_AE/SensorData/"
        pages = {0: ["new-5", "new-4"],
                 2: ["new-4", "new-3", "new-2"],
                 4: ["new-2", "new-1", "historical"],
                 6: ["historical"]}

        def fake_get(path, params=None):
            return {"m2m:rrl": {"rrf": [
                {"typ": 4, "val": prefix + name}
                for name in pages[params["ofst"]]
            ]}}

        with patch.object(reader, "get", side_effect=fake_get):
            refs = reader.list_cins(
                known={f"CindySense_AE/SensorData/{name}"
                       for name in ("baseline", "new-2", "new-1")},
                stop_at="CindySense_AE/SensorData/baseline",
                fallback_known={"CindySense_AE/SensorData/new-2",
                                "CindySense_AE/SensorData/new-1"})
        self.assertEqual(refs, [f"CindySense_AE/SensorData/new-{i}"
                                for i in (5, 4, 3)])

    def test_normalizes_relative_and_absolute_cin_references(self):
        reader = TinyIoTReader(self.config("tmp"))
        suffix = "CindySense_AE/SensorData/4-20261029T122259%2C1250176"
        expected = "http://localhost:3000/TinyIoT/" + suffix
        references = (
            "4-20261029T122259,1250176",
            suffix,
            "/" + suffix,
            "TinyIoT/" + suffix,
            "/TinyIoT/" + suffix,
            "http://localhost:3000/TinyIoT/" + suffix,
            "http://localhost:3000/" + suffix,
        )
        for reference in references:
            with self.subTest(reference=reference):
                self.assertEqual(reader.resource_url(reference), expected)
        for reference in (
            "http://evil.example/TinyIoT/" + suffix,
            "//evil.example/TinyIoT/" + suffix,
            "TinyIoT/TinyIoT/" + suffix,
            "../" + suffix,
            "TinyIoT/CindySense_AE/Other/id",
        ):
            with self.subTest(rejected=reference):
                with self.assertRaises(CollectorError):
                    reader.resource_url(reference)

    def test_http_request_is_read_only_with_onem2m_headers(self):
        reader = TinyIoTReader(self.config("tmp"))
        response = Mock()
        response.headers = {"X-M2M-RSC": "2000"}
        response.read.return_value = b'{"m2m:rrl":{"rrf":[]}}'
        with patch("tools.collect_sensor_data.urlopen") as open_url:
            open_url.return_value.__enter__.return_value = response
            reader.get("CindySense_AE/SensorData", {"rcn": 6})
        request = open_url.call_args.args[0]
        headers = dict(request.header_items())
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(headers["X-m2m-origin"], "read-origin")
        self.assertEqual(headers["X-m2m-rvi"], "4")
        self.assertTrue(headers["X-m2m-ri"].startswith("cindysense-collector-"))
        self.assertIn("rcn=6", request.full_url)

    def test_discovered_reference_builds_single_cse_prefix(self):
        reader = TinyIoTReader(self.config("tmp"))
        response = Mock()
        response.headers = {"X-M2M-RSC": "2000"}
        response.read.return_value = b'{"m2m:cin":{"con":"{}"}}'
        with patch("tools.collect_sensor_data.urlopen") as open_url:
            open_url.return_value.__enter__.return_value = response
            reader.get("TinyIoT/CindySense_AE/SensorData/4-20261029T122259,1250176")
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url,
                         "http://localhost:3000/TinyIoT/CindySense_AE/SensorData/"
                         "4-20261029T122259%2C1250176")

    def test_retries_transient_http_404_then_retrieves_cin(self):
        reader = TinyIoTReader(self.config("tmp"))
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"X-M2M-RSC": "2000"}
        response.read.return_value = b'{"m2m:cin":{"ri":"one"}}'
        missing = HTTPError("http://localhost:3000", 404, "not found", None, None)
        with patch("tools.collect_sensor_data.urlopen", side_effect=[missing, response]) as open_url, \
             patch("tools.collect_sensor_data.time.sleep") as sleep:
            payload = reader.get("CindySense_AE/SensorData/4-20261029T122259,1250176")
        self.assertEqual(payload["m2m:cin"]["ri"], "one")
        self.assertEqual(open_url.call_count, 2)
        sleep.assert_called_once_with(0.1)

    def test_http_404_retries_are_bounded(self):
        reader = TinyIoTReader(self.config("tmp"))
        with patch("tools.collect_sensor_data.urlopen",
                   side_effect=HTTPError("http://localhost:3000", 404, "not found", None, None)) as open_url, \
             patch("tools.collect_sensor_data.time.sleep"):
            with self.assertRaisesRegex(CollectorError, "HTTP 404 after 3 attempt"):
                reader.get("CindySense_AE/SensorData/missing")
        self.assertEqual(open_url.call_count, 3)

    def test_records_only_new_cin_to_csv(self):
        with tempfile.TemporaryDirectory(dir="tmp") as directory:
            reader = TinyIoTReader(self.config(directory))
            uri = "TinyIoT/CindySense_AE/SensorData/new"
            listings = [["TinyIoT/CindySense_AE/SensorData/old"], [uri], []]
            with patch.object(reader, "list_cins", side_effect=listings), \
                 patch.object(reader, "get", return_value=cin(range(0, 1000, 20))):
                output = record(reader, "squat", "squat-slow-01", 0.0001)
            with output.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 50)
            self.assertEqual(rows[0]["session_id"], "squat-slow-01")
            self.assertEqual(rows[0]["label"], "squat")
            self.assertEqual(rows[0]["sample_t"], "0")
            self.assertEqual(rows[-1]["sample_t"], "980")

    def test_records_several_cins_arriving_between_polls(self):
        with tempfile.TemporaryDirectory(dir="tmp") as directory:
            reader = TinyIoTReader(self.config(directory))
            prefix = "CindySense_AE/SensorData/"
            listings = [[prefix + "baseline"],
                        [prefix + "new-2", prefix + "new-1"],
                        [prefix + "new-4", prefix + "new-3"]]

            def fake_get(ref):
                index = int(ref.rsplit("-", 1)[-1])
                payload = cin(range(index * 1000, (index + 1) * 1000, 20))
                payload["m2m:cin"]["ri"] = ref.rsplit("/", 1)[-1]
                return payload

            with patch.object(reader, "list_cins", side_effect=listings) as listing, \
                 patch.object(reader, "get", side_effect=fake_get), \
                 patch("tools.collect_sensor_data.time.sleep", side_effect=KeyboardInterrupt):
                output = record(reader, "squat", "several-cins", None)
            with output.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 200)
            self.assertEqual([rows[i]["sample_t"] for i in (0, 50, 100, 150)],
                             ["1000", "2000", "3000", "4000"])
            self.assertEqual(listing.call_args_list[1].kwargs, {})

    def test_records_new_cin_after_oldest_baseline_cin_is_evicted(self):
        with tempfile.TemporaryDirectory(dir="tmp") as directory:
            reader = TinyIoTReader(self.config(directory))
            prefix = "TinyIoT/CindySense_AE/SensorData/"
            retained = ["baseline-newest", "baseline-middle", "baseline-oldest"]

            def fake_get(path, params=None):
                if params is not None:
                    return {"m2m:rrl": {"rrf": [
                        {"typ": 4, "val": prefix + name} for name in retained
                    ]}}
                self.assertEqual(path, "CindySense_AE/SensorData/new")
                return cin(range(0, 1000, 20))

            sleeps = 0

            def advance_upload(_seconds):
                nonlocal sleeps
                sleeps += 1
                if sleeps == 1:
                    retained[:] = ["new", "baseline-newest", "baseline-middle"]
                else:
                    raise KeyboardInterrupt

            with patch.object(reader, "get", side_effect=fake_get), \
                 patch("tools.collect_sensor_data.time.sleep", side_effect=advance_upload):
                output = record(reader, "rest", "rolling-baseline", None)
            with output.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 50)
            self.assertEqual(rows[0]["label"], "rest")

    def test_record_continues_after_baseline_expires_with_session_overlap(self):
        with tempfile.TemporaryDirectory(dir="tmp") as directory:
            reader = TinyIoTReader(self.config(directory))
            prefix = "CindySense_AE/SensorData/"
            retained = ["baseline"]

            def fake_get(path, params=None):
                if params is not None:
                    return {"m2m:rrl": {"rrf": [
                        {"typ": 4, "val": prefix + name} for name in retained
                    ]}}
                index = int(path.rsplit("-", 1)[-1])
                payload = cin(range(index * 1000, (index + 1) * 1000, 20))
                payload["m2m:cin"]["ri"] = path.rsplit("/", 1)[-1]
                return payload

            sleeps = 0

            def advance_upload(_seconds):
                nonlocal sleeps
                sleeps += 1
                if sleeps == 1:
                    retained[:] = ["new-1", "baseline"]
                elif sleeps == 2:
                    retained[:] = ["new-2", "new-1"]
                else:
                    raise KeyboardInterrupt

            with patch.object(reader, "get", side_effect=fake_get), \
                 patch("tools.collect_sensor_data.time.sleep", side_effect=advance_upload):
                output = record(reader, "rest", "expired-baseline", None)
            with output.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 100)
            self.assertEqual([rows[0]["sample_t"], rows[50]["sample_t"]],
                             ["1000", "2000"])

    def test_rolling_retention_collects_45_unique_batches(self):
        with tempfile.TemporaryDirectory(dir="tmp") as directory:
            reader = TinyIoTReader(self.config(directory))
            upload = 0
            names = ["old"]

            def fake_list():
                return [f"CindySense_AE/SensorData/{name}" for name in reversed(names[-15:])]

            def fake_get(ref):
                index = int(ref.rsplit("-", 1)[-1])
                payload = cin(range((index - 1) * 1000, index * 1000, 20))
                payload["m2m:cin"]["ri"] = ref.rsplit("/", 1)[-1]
                return payload

            def advance_upload(_seconds):
                nonlocal upload
                upload += 1
                names.append(f"new-{upload}")
                if upload == 45:
                    raise KeyboardInterrupt

            with patch.object(reader, "list_cins", side_effect=fake_list), \
                 patch.object(reader, "get", side_effect=fake_get) as get, \
                 patch("tools.collect_sensor_data.time.sleep", side_effect=advance_upload):
                output = record(reader, "squat", "forty-five-seconds", None)
            with output.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 2250)
            self.assertEqual(len({row["sample_t"] for row in rows}), 2250)
            self.assertEqual(get.call_count, 45)

    def test_disappearing_baseline_and_temporary_retrieval_failure(self):
        with tempfile.TemporaryDirectory(dir="tmp") as directory:
            reader = TinyIoTReader(self.config(directory))
            prefix = "CindySense_AE/SensorData/"
            listings = [[prefix + "baseline"],
                        [prefix + "new-1", prefix + "baseline"],
                        [prefix + "new-2", prefix + "new-1"],
                        [prefix + "new-2", prefix + "new-1"],
                        [prefix + "new-2", prefix + "new-1"]]
            calls = {}

            def fake_get(ref):
                calls[ref] = calls.get(ref, 0) + 1
                if ref.endswith("new-1") and calls[ref] == 1:
                    raise CollectorError("HTTP 404 after 3 attempts")
                index = int(ref.rsplit("-", 1)[-1])
                payload = cin(range((index - 1) * 1000, index * 1000, 20))
                payload["m2m:cin"]["ri"] = ref.rsplit("/", 1)[-1]
                return payload

            sleeps = 0

            def advance(_seconds):
                nonlocal sleeps
                sleeps += 1
                if sleeps == 3:
                    raise KeyboardInterrupt

            with patch.object(reader, "list_cins", side_effect=listings), \
                 patch.object(reader, "get", side_effect=fake_get), \
                 patch("tools.collect_sensor_data.time.sleep", side_effect=advance):
                output = record(reader, "squat", "retry-after-404", None)
            with output.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 100)
            self.assertEqual(calls[prefix + "new-1"], 2)
            self.assertEqual(calls[prefix + "new-2"], 1)

    def test_record_recovers_from_failed_discovery_poll(self):
        with tempfile.TemporaryDirectory(dir="tmp") as directory:
            reader = TinyIoTReader(self.config(directory))
            prefix = "CindySense_AE/SensorData/"
            listings = [[prefix + "baseline"], CollectorError("HTTP 503 after 3 attempts"),
                        [prefix + "new", prefix + "baseline"],
                        [prefix + "new", prefix + "baseline"],
                        [prefix + "new", prefix + "baseline"]]
            sleeps = 0

            def advance(_seconds):
                nonlocal sleeps
                sleeps += 1
                if sleeps == 3:
                    raise KeyboardInterrupt

            with patch.object(reader, "list_cins", side_effect=listings), \
                 patch.object(reader, "get", return_value=cin(range(0, 1000, 20))) as get, \
                 patch("tools.collect_sensor_data.time.sleep", side_effect=advance):
                output = record(reader, "squat", "listing-retry", None)
            with output.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 50)
            get.assert_called_once_with(prefix + "new")


if __name__ == "__main__":
    unittest.main()
