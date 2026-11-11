"""Regression checks for the live TinyIoT filter probe's measurement contract."""

import unittest

from tools.onem2m_filter_probe import Collector, counts, verify_filter_matrix


class FilterProbeTests(unittest.TestCase):
    def tearDown(self):
        with Collector.lock:
            Collector.events.clear()

    def test_counts_only_real_notifications_for_exact_subscription(self):
        with Collector.lock:
            Collector.events[:] = [
                {'sur': '/tinyiot/atr-id', 'vrq': True},
                {'sur': '/tinyiot/atr-id', 'vrq': False},
                {'sur': '/tinyiot/chty-id', 'vrq': False},
                {'sur': '/tinyiot/unrelated', 'vrq': False},
            ]
        self.assertEqual(counts({'atr': 'atr-id', 'chty': 'chty-id'}),
                         {'atr': 1, 'chty': 1})

    def test_live_matrix_flags_an_unexpected_delivery(self):
        rows = [
            {'case': 'cin-unlabeled', 'http': 201, 'rsc': '2001',
             'notifications': {'atr': 0, 'chty': 0}},
            {'case': 'cin-labeled', 'http': 201, 'rsc': '2001',
             'notifications': {'atr': 0, 'chty': 0}},
            {'case': 'cnt-unlabeled', 'http': 201, 'rsc': '2001',
             'notifications': {'atr': 0, 'chty': 1}},
            {'case': 'cnt-labeled', 'http': 201, 'rsc': '2001',
             'notifications': {'atr': 1, 'chty': 1}},
        ]
        verify_filter_matrix(rows)
        rows[1]['notifications']['atr'] = 1
        with self.assertRaisesRegex(RuntimeError, 'filter behavior changed'):
            verify_filter_matrix(rows)


if __name__ == '__main__':
    unittest.main()
