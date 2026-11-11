"""Regression checks for interpreting the live TinyIoT retention experiment."""

import unittest

from tools.onem2m_retention_acp_probe import verify_retention_results


class RetentionProbeTests(unittest.TestCase):
    def test_expected_eviction_and_trigger_based_expiration(self):
        results = {
            'mni': {'create_value': 2, 'cni': 2,
                    'readback': {'item_1': {'http': 404}, 'item_2': {'http': 200},
                                 'item_3': {'http': 200}}},
            'mbs': {'create_value': 10, 'cni': 1, 'cbs': 6,
                    'readback': {'item_1': {'http': 404}, 'item_2': {'http': 404},
                                 'item_3': {'http': 200}}},
            'mia': {'before_new': {'http': 200}, 'after_new': {
                'old': {'http': 404}, 'new': {'http': 200}}, 'cni': 1},
            'et': {'before': {'http': 200}, 'after': {'http': 404}},
        }
        verify_retention_results(results)
        results['mia']['after_new']['old']['http'] = 200
        with self.assertRaisesRegex(RuntimeError, 'age or expiration'):
            verify_retention_results(results)
        results['mia']['after_new']['old']['http'] = 404
        results['mbs']['readback']['item_1']['http'] = 200
        with self.assertRaisesRegex(RuntimeError, 'mbs retention'):
            verify_retention_results(results)


if __name__ == '__main__':
    unittest.main()
