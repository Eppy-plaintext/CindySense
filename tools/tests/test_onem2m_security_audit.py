"""Keep the live ACP audit scoped to disposable resources."""

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from tools import onem2m_security_audit as audit


class SecurityAuditTests(unittest.TestCase):
    def test_production_readback_is_read_only(self):
        calls = []

        def fake_request(method, path, origin='CAdmin', body=None, ty=None):
            calls.append((method, path, origin))
            return {'http': 200, 'rsc': '2000', 'body': {'m2m:cnt': {}}}

        with patch.object(audit, 'request', side_effect=fake_request):
            audit.production_readback()
        self.assertTrue(calls)
        self.assertTrue(all(method == 'GET' for method, _, _ in calls))

    def test_disposable_cleanup_uses_admin_and_precedes_acp_delete(self):
        calls = []
        serial = 0
        deleted_paths = set()

        def fake_request(method, path, origin='CAdmin', body=None, ty=None):
            nonlocal serial
            calls.append((method, path, origin))
            if method == 'POST' and ty == 1:
                serial += 1
                return {'http': 201, 'rsc': '2001',
                        'body': {'m2m:acp': {'ri': f'ri-{serial}'}}}
            if method == 'POST' and ty in (3, 4):
                return {'http': 201 if origin in ('CAdmin', 'CWebcam', 'CCindyPT') else 403,
                        'rsc': '2001' if origin in ('CAdmin', 'CWebcam', 'CCindyPT') else '4103',
                        'body': {}}
            if method == 'DELETE':
                deleted_paths.add(path)
                return {'http': 200, 'rsc': '2002', 'body': {}}
            if method == 'GET' and path in deleted_paths:
                return {'http': 404, 'rsc': '4004', 'body': {}}
            if method == 'GET':
                return {'http': 200, 'rsc': '2000',
                        'body': {'m2m:cnt': {'acpi': ['ri-1']},
                                 'm2m:cin': {'con': 'disposable-acp-probe'}}}
            raise AssertionError((method, path))

        with patch.object(audit, 'request', side_effect=fake_request):
            audit.disposable_probe()
        deletes = [(path, origin) for method, path, origin in calls if method == 'DELETE']
        self.assertTrue(deletes)
        self.assertTrue(all(origin == 'CAdmin' for _, origin in deletes))
        self.assertTrue(all('SecurityProbe_' in path for path, _ in deletes))
        self.assertEqual(len(deletes), 5)
        self.assertTrue(deletes[2][0].endswith('_ACP'))
        self.assertTrue(deletes[4][0].endswith('_ACP'))

    def test_verifier_rejects_incomplete_probe(self):
        with self.assertRaisesRegex(RuntimeError, 'temporary ACP creation failed'):
            audit.verify_disposable_results({'workout': {'acp_create': {'http': 403, 'rsc': '4103'}}}, {})

    def test_subscription_backup_omits_callback_uri(self):
        def fake_request(method, path, origin='CAdmin'):
            return {'http': 200, 'rsc': '2000', 'body': {'m2m:sub': {
                'rn': path.rsplit('/', 1)[-1], 'ri': 'sub-ri', 'acpi': None,
                'enc': {'net': [3]}, 'nct': 1,
                'nu': ['http://private/notify/secret-token']}}}

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'snapshot.json'
            with patch.object(audit, 'request', side_effect=fake_request):
                audit.backup_subscription_assignments(destination)
            content = destination.read_text(encoding='utf-8')
            self.assertNotIn('secret-token', content)
            backup = json.loads(content)
            self.assertTrue(all('nu' not in value for value in backup['subscriptions'].values()))

    def test_verifiers_reject_incomplete_migration_and_guard(self):
        with self.assertRaisesRegex(RuntimeError, 'migration roundtrip failed'):
            audit.verify_migration_roundtrip({})
        with self.assertRaisesRegex(RuntimeError, 'subscription guard failed'):
            audit.verify_subscription_guard({})


if __name__ == '__main__':
    unittest.main()
