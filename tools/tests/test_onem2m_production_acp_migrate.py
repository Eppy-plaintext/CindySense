"""Transaction guards for the approved production ACP migration."""

import unittest
from unittest.mock import patch

from tools import onem2m_production_acp_migrate as migration


class MigrationSafetyTests(unittest.TestCase):
    def test_proposed_policy_does_not_grant_webcam_admin(self):
        acr = migration.PUBLISHER_POLICY['pv']['acr']
        self.assertEqual(next(item['acop'] for item in acr if 'CWebcam' in item['acor']), 3)
        self.assertEqual(migration.PUBLISHER_POLICY['pvs']['acr'],
                         [{'acor': ['CAdmin'], 'acop': 63}])
        self.assertEqual(len(migration.CONTAINERS), 2)
        self.assertEqual(len(migration.SUBSCRIPTIONS), 2)

    def test_rollback_restores_containers_before_subscriptions_and_deletes_last(self):
        operations = []
        original_subs = {path: {'ri': path, 'rn': path.rsplit('/', 1)[-1],
                                'nu': ['private-value-in-memory'],
                                'enc': {'net': [3]}, 'nct': 1}
                         for path in migration.SUBSCRIPTIONS}

        def fake_request(method, path, origin='CAdmin', body=None, ty=None):
            operations.append((method, path, body))
            if method == 'PUT':
                return {'http': 200, 'rsc': '2004', 'body': {}}
            if method == 'DELETE':
                return {'http': 200, 'rsc': '2002', 'body': {}}
            if path == migration.NEW_ACP:
                return {'http': 200, 'rsc': '2000',
                        'body': {'m2m:acp': {'ri': 'new-ri'}}}
            if path in migration.CONTAINERS:
                return {'http': 200, 'rsc': '2000',
                        'body': {'m2m:cnt': {'acpi': None}}}
            if path in migration.SUBSCRIPTIONS:
                return {'http': 200, 'rsc': '2000',
                        'body': {'m2m:sub': {**original_subs[path], 'acpi': None}}}
            raise AssertionError((method, path))

        state = {'changed_cnts': list(migration.CONTAINERS),
                 'changed_subs': list(migration.SUBSCRIPTIONS)}
        with patch.object(migration, 'request', side_effect=fake_request):
            with patch.object(migration, 'get', side_effect=lambda path, origin, expected, log:
                              fake_request('GET', path, origin)):
                errors = migration.rollback(original_subs, [], state)
        self.assertEqual(errors, [])
        puts = [path for method, path, _ in operations if method == 'PUT']
        self.assertEqual(puts, list(reversed(migration.CONTAINERS)) +
                         list(reversed(migration.SUBSCRIPTIONS)))
        self.assertEqual([method for method, _, _ in operations if method == 'DELETE'], ['DELETE'])
        self.assertEqual(operations[-2][:2], ('DELETE', migration.NEW_ACP))


if __name__ == '__main__':
    unittest.main()
