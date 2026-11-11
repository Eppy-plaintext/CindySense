import unittest
import json
from uuid import uuid4

import httpx

from tools.monitor_cindy_reliability import (
    CSEReader, EventLedger, latency_ms, metric_delta, snapshot_count,
)


class MonitorContractTests(unittest.TestCase):
    def test_selected_session_count(self):
        self.assertEqual(snapshot_count({'sources': {'webcam': {
            'sessionId': 'webcam-a', 'eventCount': 30}}}), ('webcam-a', 30))

    def test_no_physical_webcam_event_is_not_zero_loss(self):
        self.assertEqual(snapshot_count({'sources': {'webcam': None}}), (None, None))

    def test_id_correlation_duplicates_missing_order_reconnect_and_latency(self):
        ledger = EventLedger()
        ids = [str(uuid4()) for _ in range(3)]
        prefix = 'TinyIoT/CindySense_AE/RepetitionCount/'
        ledger.baseline([prefix + 'webcam-' + uuid4().hex])
        for sequence, event_id in enumerate(ids, 1):
            ledger.store(prefix + 'webcam-' + event_id.replace('-', ''), {
                'source': 'webcam', 'eventId': event_id, 'eventSequence': sequence,
                'sessionId': 'webcam-test', 'timestamp': '2026-11-11T00:00:00Z'})
        def signal(event_id, sequence):
            ledger.observe('repetition', {'eventId': event_id, 'eventSequence': sequence,
                'sessionId': 'webcam-test', 'timestamp': '2026-11-11T00:00:00Z',
                'callbackReceivedAt': '2026-11-11T00:00:00.120Z',
                'sseEmittedAt': '2026-11-11T00:00:00.150Z'}, '2026-11-11T00:00:00.180Z')
        signal(ids[1], 2)
        signal(ids[0], 1)
        signal(ids[1], 2)
        ledger.reconnects += 1
        ledger.observe('reconcile', {})
        report = ledger.report()
        self.assertEqual(report['storedWithoutSseIds'], [ids[2]])
        self.assertEqual(report['duplicateSseIds'], [ids[1]])
        self.assertEqual(report['outOfOrderSseIds'], [ids[0]])
        self.assertEqual(report['reconnects'], 1)
        self.assertEqual(report['reconcileEvents'], 1)
        self.assertEqual(report['latency']['eventToCallbackMs']['medianMs'], 120)
        self.assertEqual(report['latency']['callbackToSseMs']['medianMs'], 30)
        self.assertEqual(report['latency']['sseToObserverMs']['medianMs'], 30)
        self.assertEqual(report['missingStoredSequencesBySession']['webcam-test'], [])

    def test_internal_stored_sequence_gap_is_not_called_detection_error(self):
        ledger = EventLedger()
        for sequence in (1, 3):
            ledger.store('TinyIoT/CindySense_AE/RepetitionCount/webcam-' + uuid4().hex, {
                'source': 'webcam', 'eventId': str(uuid4()), 'eventSequence': sequence,
                'sessionId': 'webcam-gap'})
        self.assertEqual(ledger.report()['missingStoredSequencesBySession']['webcam-gap'], [2])

    def test_invalid_timestamps_and_backend_restart_are_not_false_latency(self):
        self.assertIsNone(latency_ms('invalid', '2026-11-11T00:00:01Z'))
        self.assertIsNone(latency_ms('2026-11-11T00:00:02Z', '2026-11-11T00:00:01Z'))
        self.assertIsNone(latency_ms('2026-11-11T00:00:01', '2026-11-11T00:00:02Z'))
        before = {'instanceId': 'run-1', 'validatedCallbacks': 10, 'duplicateCallbacks': 2,
                  'rejectedCallbacks': 1, 'sseQueueDrops': 0}
        after = {**before, 'validatedCallbacks': 12, 'rejectedCallbacks': 3}
        self.assertEqual(metric_delta(before, after)['rejectedCallbacks'], 2)
        self.assertIsNone(metric_delta(before, {**after, 'validatedCallbacks': 1}))
        self.assertIsNone(metric_delta(before, {**after, 'instanceId': 'run-2'}))


class CseReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_new_refs_are_retrieved_with_read_only_origin(self):
        event_id = str(uuid4())
        old = 'TinyIoT/CindySense_AE/RepetitionCount/webcam-' + uuid4().hex
        new = 'TinyIoT/CindySense_AE/RepetitionCount/webcam-' + event_id.replace('-', '')
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.params.get('rcn') == '6':
                return httpx.Response(200, json={'m2m:rrl': {'rrf': [
                    {'typ': 4, 'val': new}, {'typ': 4, 'val': old}]}})
            return httpx.Response(200, json={'m2m:cin': {'con': json.dumps({
                'source': 'webcam', 'eventId': event_id, 'eventSequence': 1,
                'sessionId': 'webcam-test', 'timestamp': '2026-11-11T00:00:00Z'})}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader = CSEReader(client, 'http://localhost:3001/TinyIoT', 'CDashboard')
            self.assertEqual(await reader.refs({old}), [new])
            self.assertEqual((await reader.content(new))['eventId'], event_id)
        self.assertEqual([request.method for request in requests], ['GET', 'GET'])
        self.assertTrue(all(request.headers['X-M2M-Origin'] == 'CDashboard' for request in requests))


if __name__ == '__main__':
    unittest.main()
