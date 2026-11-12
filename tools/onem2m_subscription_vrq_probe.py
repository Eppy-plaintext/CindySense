"""Verify TinyIoT can reach the configured FastAPI callback via a disposable SUB.

The callback URI is loaded from backend configuration and is never printed.
"""

import json
import uuid

from backend.config import settings
from tools.onem2m_retention_acp_probe import compact, request


def run():
    if not settings.notification_url or len(settings.notification_token) < 32:
        raise RuntimeError('Notification callback configuration is incomplete')
    parent = 'CindySense_AE'
    name = 'SubscriptionVRQ_' + uuid.uuid4().hex[:12]
    cnt_path = f'{parent}/{name}'
    sub_path = cnt_path + '/Disposable_Sub'
    result = {'container': cnt_path}
    created = False
    try:
        response = request('POST', parent, 'CAdmin', {'m2m:cnt': {'rn': name}}, 3)
        result['container_create'] = compact(response)
        if result['container_create'] != {'http': 201, 'rsc': '2001'}:
            return result
        created = True
        response = request('POST', cnt_path, 'CAdmin', {'m2m:sub': {
            'rn': 'Disposable_Sub', 'nu': [settings.notification_url],
            'enc': {'net': [3]}, 'nct': 1}}, 23)
        result['subscription_create'] = compact(response)
        if result['subscription_create'] == {'http': 201, 'rsc': '2001'}:
            stored = request('GET', sub_path, 'CAdmin')
            result['subscription_readback'] = compact(stored)
            sub = stored['body'].get('m2m:sub', {})
            result['callback_unchanged'] = sub.get('nu') == [settings.notification_url]
            result['event_type'] = sub.get('enc', {}).get('net')
    finally:
        if created:
            result['container_delete'] = compact(request('DELETE', cnt_path, 'CAdmin'))
            result['container_absent'] = compact(request('GET', cnt_path, 'CAdmin'))
            result['subscription_absent'] = compact(request('GET', sub_path, 'CAdmin'))
    return result


if __name__ == '__main__':
    output = run()
    print(json.dumps(output, indent=2))
    expected_create = {'http': 201, 'rsc': '2001'}
    expected_absent = {'http': 404, 'rsc': '4004'}
    if (output.get('container_create') != expected_create or
            output.get('subscription_create') != expected_create or
            output.get('subscription_readback') != {'http': 200, 'rsc': '2000'} or
            not output.get('callback_unchanged') or output.get('event_type') != [3] or
            output.get('container_delete') != {'http': 200, 'rsc': '2002'} or
            output.get('container_absent') != expected_absent or
            output.get('subscription_absent') != expected_absent):
        raise SystemExit('Disposable subscription verification failed; see sanitized statuses')
