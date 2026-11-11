"""Test application AE write ACPs using only uniquely named disposable children."""

import json
import uuid

if __package__:
    from .onem2m_retention_acp_probe import IDENTITIES, compact, request
else:
    from onem2m_retention_acp_probe import IDENTITIES, compact, request


def verify_write_permissions(results):
    for ae, data in results.items():
        if data['root_create']['http'] != 201 or data['cleanup']['http'] != 200 or data['readback']['http'] != 404:
            raise RuntimeError(f'{ae} disposable resource lifecycle changed')
        for identity, operations in data['writes'].items():
            expected = {'create': 201, 'update': 200, 'delete': 200} if identity in ('CAdmin', data['owner']) else {
                'create': 403, 'update': 403, 'delete': 403}
            actual = {operation: response['http'] for operation, response in operations.items()}
            if actual != expected:
                raise RuntimeError(f'{ae} {identity} write permissions changed: {actual}')


def main():
    applications = (('Webcam_AE', 'CWebcam'), ('CindyPT_AE', 'CCindyPT'),
                    ('Dashboard_AE', 'CDashboard'))
    results = {}
    for ae, owner in applications:
        name = 'ACPProbe_' + uuid.uuid4().hex[:12]
        root = f'{ae}/{name}'
        created = False
        try:
            result = request('POST', ae, owner, {'m2m:cnt': {'rn': name}}, 3)
            results[ae] = {'owner': owner, 'root_create': compact(result), 'writes': {}}
            if result['http'] != 201:
                break
            created = True
            for identity in IDENTITIES:
                entry = {}
                child_name = 'create_' + identity
                entry['create'] = compact(request('POST', root, identity,
                                                  {'m2m:cnt': {'rn': child_name}}, 3))
                entry['update'] = compact(request('PUT', root, identity,
                                                  {'m2m:cnt': {'lbl': [identity]}}))
                delete_name = 'delete_' + identity
                prep = request('POST', root, owner, {'m2m:cnt': {'rn': delete_name}}, 3)
                if prep['http'] != 201:
                    raise RuntimeError(f'{ae} DELETE setup failed: {compact(prep)}')
                entry['delete'] = compact(request('DELETE', f'{root}/{delete_name}', identity))
                results[ae]['writes'][identity] = entry
                if any(value['http'] not in (200, 201, 403) for value in entry.values()):
                    raise RuntimeError(f'unexpected ACP result: {ae} {identity} {entry}')
        finally:
            if created:
                results[ae]['cleanup'] = compact(request('DELETE', root, owner))
                results[ae]['readback'] = compact(request('GET', root, owner))
    verify_write_permissions(results)
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
