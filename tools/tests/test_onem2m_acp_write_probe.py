"""The disposable ACP probe must detect unintended cross-application writes."""

import unittest

from tools.onem2m_acp_write_probe import verify_write_permissions


class ACPProbeTests(unittest.TestCase):
    def test_cross_application_write_is_a_regression(self):
        data = {'Dashboard_AE': {
            'owner': 'CDashboard', 'root_create': {'http': 201},
            'cleanup': {'http': 200}, 'readback': {'http': 404},
            'writes': {
                'CAdmin': {'create': {'http': 201}, 'update': {'http': 200}, 'delete': {'http': 200}},
                'CDashboard': {'create': {'http': 201}, 'update': {'http': 200}, 'delete': {'http': 200}},
                'CCindyPT': {'create': {'http': 403}, 'update': {'http': 403}, 'delete': {'http': 403}},
                'CWebcam': {'create': {'http': 403}, 'update': {'http': 403}, 'delete': {'http': 403}},
            },
        }}
        verify_write_permissions(data)
        data['Dashboard_AE']['writes']['CCindyPT']['create']['http'] = 201
        with self.assertRaisesRegex(RuntimeError, 'write permissions changed'):
            verify_write_permissions(data)


if __name__ == '__main__':
    unittest.main()
