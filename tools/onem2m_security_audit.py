"""Read-only production ACP audit and opt-in disposable permission probe."""

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .onem2m_retention_acp_probe import compact, request
else:
    from onem2m_retention_acp_probe import compact, request

ORIGINS = ('CWebcam', 'CCindyPT', 'CDashboard')
ACPS = ('CindySense_ACP', 'CindyPT_ACP', 'Dashboard_ACP')
TARGETS = ('CindySense_AE', 'CindySense_AE/RepetitionCount',
           'CindySense_AE/WorkoutSummary', 'CindyPT_AE',
           'CindyPT_AE/AIRecommendations', 'Dashboard_AE',
           'Webcam_AE', 'Webcam_AE/PoseData')
BACKUP_TARGETS = (*ACPS, 'CindySense_AE', 'CindySense_AE/RepetitionCount',
                  'CindySense_AE/WorkoutSummary')
SUBSCRIPTION_TARGETS = ('CindySense_AE/RepetitionCount/RepCount_Sub',
                        'CindySense_AE/WorkoutSummary/Summary_Sub')


def production_readback():
    result = {'acps': {}, 'targets': {}}
    for path in ACPS:
        response = request('GET', path, 'CAdmin')
        acp = response['body'].get('m2m:acp', {})
        result['acps'][path] = {'status': compact(response), 'ri': acp.get('ri'),
                                'pv': acp.get('pv', {}).get('acr'),
                                'pvs': acp.get('pvs', {}).get('acr')}
    for path in TARGETS:
        response = request('GET', path, 'CAdmin')
        resource = next(iter(response['body'].values()), {})
        result['targets'][path] = {'status': compact(response), 'ri': resource.get('ri'),
                                   'ty': resource.get('ty'), 'aei': resource.get('aei'),
                                   'acpi': resource.get('acpi'),
                                   'reads': {origin: compact(request('GET', path, origin))
                                             for origin in ORIGINS}}
    return result


def backup_production(path):
    """Save the complete affected ACP/CNT representations and AE assignment."""
    resources = {}
    for resource_path in BACKUP_TARGETS:
        response = request('GET', resource_path, 'CAdmin')
        if compact(response) != {'http': 200, 'rsc': '2000'}:
            raise RuntimeError(f'Cannot back up {resource_path}: {compact(response)}')
        expected_type = 'm2m:acp' if resource_path in ACPS else (
            'm2m:ae' if resource_path == 'CindySense_AE' else 'm2m:cnt')
        representation = response['body'].get(expected_type)
        if not isinstance(representation, dict):
            raise RuntimeError(f'Unexpected representation for {resource_path}')
        resources[resource_path] = {expected_type: representation}
    backup = {'capturedAt': datetime.now(timezone.utc).isoformat(),
              'purpose': 'Read-only pre-migration ACP and assignment backup',
              'resources': resources}
    destination = Path(path)
    if destination.exists():
        raise FileExistsError(f'Backup already exists: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(backup, stream, indent=2)
        stream.write('\n')
    return destination


def backup_subscription_assignments(path):
    """Back up SUB assignments without storing callback URLs or tokens."""
    subscriptions = {}
    for resource_path in SUBSCRIPTION_TARGETS:
        response = request('GET', resource_path, 'CAdmin')
        if compact(response) != {'http': 200, 'rsc': '2000'}:
            raise RuntimeError(f'Cannot back up {resource_path}: {compact(response)}')
        sub = response['body'].get('m2m:sub')
        if not isinstance(sub, dict):
            raise RuntimeError(f'Unexpected subscription representation for {resource_path}')
        subscriptions[resource_path] = {key: sub.get(key) for key in
                                        ('rn', 'ri', 'acpi', 'enc', 'nct')}
        subscriptions[resource_path]['notificationUriCount'] = len(sub.get('nu', []))
    backup = {'capturedAt': datetime.now(timezone.utc).isoformat(),
              'purpose': 'Read-only subscription assignment backup; callback URIs omitted',
              'subscriptions': subscriptions}
    destination = Path(path)
    if destination.exists():
        raise FileExistsError(f'Backup already exists: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(backup, stream, indent=2)
        stream.write('\n')
    return destination


def disposable_probe():
    suffix = uuid.uuid4().hex[:12]
    specs = (
        ('workout', 'CindySense_AE', 'CAdmin',
         [{'acor': ['CAdmin'], 'acop': 63},
          {'acor': ['CWebcam'], 'acop': 3},
          {'acor': ['CCindyPT', 'CDashboard'], 'acop': 2}],
         ['Repetition', 'Summary']),
        ('coaching', 'CindyPT_AE', 'CCindyPT',
         [{'acor': ['CCindyPT'], 'acop': 3},
          {'acor': ['CDashboard'], 'acop': 2}],
         ['Recommendations']),
    )
    results = {}
    for label, parent, owner, privileges, containers in specs:
        acp_path = f'{parent}/SecurityProbe_{suffix}_ACP'
        created = []
        result = {'acp_path': acp_path, 'containers': {}, 'cleanup': []}
        results[label] = result
        try:
            acp_response = request('POST', parent, owner, {
                'm2m:acp': {'rn': acp_path.rsplit('/', 1)[-1],
                            'pv': {'acr': privileges},
                            'pvs': {'acr': [{'acor': [owner], 'acop': 63}]}}}, 1)
            result['acp_create'] = compact(acp_response)
            if result['acp_create'] != {'http': 201, 'rsc': '2001'}:
                continue
            created.append(acp_path)
            acp_ri = acp_response['body']['m2m:acp']['ri']
            result['acp_ri'] = acp_ri
            for kind in containers:
                cnt_path = f'{parent}/SecurityProbe_{suffix}_{kind}'
                response = request('POST', parent, owner, {
                    'm2m:cnt': {'rn': cnt_path.rsplit('/', 1)[-1], 'acpi': [acp_ri]}}, 3)
                entry = {'create': compact(response)}
                result['containers'][kind] = entry
                if entry['create'] != {'http': 201, 'rsc': '2001'}:
                    continue
                created.append(cnt_path)
                stored = request('GET', cnt_path, owner)
                entry['readback'] = compact(stored)
                entry['acpi'] = stored['body'].get('m2m:cnt', {}).get('acpi')
                entry['operations'] = {}
                for origin in ORIGINS:
                    child_name = 'cin_' + origin
                    operations = {'get': compact(request('GET', cnt_path, origin))}
                    posted = request('POST', cnt_path, origin, {
                        'm2m:cin': {'rn': child_name, 'con': 'disposable-acp-probe'}}, 4)
                    operations['post'] = compact(posted)
                    if operations['post'] == {'http': 201, 'rsc': '2001'}:
                        child = request('GET', f'{cnt_path}/{child_name}', origin)
                        operations['cin_readback'] = compact(child)
                        operations['content_verified'] = (child['body'].get('m2m:cin', {}).get('con') ==
                                                          'disposable-acp-probe')
                    entry['operations'][origin] = operations
        finally:
            containers_removed = True
            for path in reversed(created):
                # A least-privilege application origin cannot DELETE its test CNT.
                # Administrative cleanup is confined to these uniquely named paths.
                if path == acp_path and not containers_removed:
                    result['cleanup'].append({'path': path, 'skipped': 'container cleanup failed'})
                    continue
                deleted = request('DELETE', path, 'CAdmin')
                absent = request('GET', path, 'CAdmin')
                entry = {'path': path, 'delete': compact(deleted), 'absent': compact(absent)}
                result['cleanup'].append(entry)
                if entry['delete'] != {'http': 200, 'rsc': '2002'} or entry['absent'] != {'http': 404, 'rsc': '4004'}:
                    containers_removed = False
    return results


def inherited_policy_probe():
    """Test the existing CindySense AE policy via a disposable child only."""
    name = 'SecurityInherited_' + uuid.uuid4().hex[:12]
    path = f'CindySense_AE/{name}'
    result = {'path': path, 'operations': {}}
    created = False
    try:
        response = request('POST', 'CindySense_AE', 'CAdmin',
                           {'m2m:cnt': {'rn': name}}, 3)
        result['create'] = compact(response)
        if result['create'] != {'http': 201, 'rsc': '2001'}:
            return result
        created = True
        result['readback'] = compact(request('GET', path, 'CAdmin'))
        for origin in ORIGINS:
            result['operations'][origin] = {
                'get': compact(request('GET', path, origin)),
                'post': compact(request('POST', path, origin,
                                        {'m2m:cin': {'rn': f'cin_{origin}',
                                                    'con': 'disposable-inherited-probe'}}, 4))}
    finally:
        if created:
            result['cleanup'] = compact(request('DELETE', path, 'CAdmin'))
            result['absent'] = compact(request('GET', path, 'CAdmin'))
    return result


def migration_roundtrip_probe():
    """Exercise the exact CNT acpi update and null rollback on disposable paths."""
    suffix = uuid.uuid4().hex[:12]
    parent = 'CindySense_AE'
    acp_path = f'{parent}/MigrationProbe_{suffix}_ACP'
    cnt_path = f'{parent}/MigrationProbe_{suffix}_Summary'
    result = {'acp_path': acp_path, 'cnt_path': cnt_path, 'cleanup': []}
    created = []
    try:
        policy = {'m2m:acp': {'rn': acp_path.rsplit('/', 1)[-1],
                             'pv': {'acr': [
                                 {'acor': ['CAdmin'], 'acop': 63},
                                 {'acor': ['CWebcam'], 'acop': 3},
                                 {'acor': ['CCindyPT', 'CDashboard'], 'acop': 2}]},
                             'pvs': {'acr': [{'acor': ['CAdmin'], 'acop': 63}]}}}
        response = request('POST', parent, 'CAdmin', policy, 1)
        result['acp_create'] = compact(response)
        if result['acp_create'] != {'http': 201, 'rsc': '2001'}:
            return result
        created.append(acp_path)
        acp_ri = response['body']['m2m:acp']['ri']
        result['acp_ri'] = acp_ri
        response = request('POST', parent, 'CAdmin',
                           {'m2m:cnt': {'rn': cnt_path.rsplit('/', 1)[-1]}}, 3)
        result['cnt_create'] = compact(response)
        if result['cnt_create'] != {'http': 201, 'rsc': '2001'}:
            return result
        created.append(cnt_path)
        result['before'] = compact(request('GET', cnt_path, 'CWebcam'))
        result['apply'] = compact(request('PUT', cnt_path, 'CAdmin',
                                          {'m2m:cnt': {'acpi': [acp_ri]}}))
        applied = request('GET', cnt_path, 'CAdmin')
        result['applied_readback'] = compact(applied)
        result['applied_acpi'] = applied['body'].get('m2m:cnt', {}).get('acpi')
        result['publisher_get'] = compact(request('GET', cnt_path, 'CWebcam'))
        result['publisher_post'] = compact(request('POST', cnt_path, 'CWebcam',
                                                   {'m2m:cin': {'rn': 'probe-cin',
                                                               'con': 'disposable-migration-probe'}}, 4))
        result['publisher_cin_readback'] = compact(request('GET', cnt_path + '/probe-cin', 'CWebcam'))
        result['rollback'] = compact(request('PUT', cnt_path, 'CAdmin',
                                             {'m2m:cnt': {'acpi': None}}))
        rolled_back = request('GET', cnt_path, 'CAdmin')
        result['rollback_readback'] = compact(rolled_back)
        result['rollback_acpi'] = rolled_back['body'].get('m2m:cnt', {}).get('acpi')
        result['after_webcam'] = compact(request('GET', cnt_path, 'CWebcam'))
        result['after_cindypt'] = compact(request('GET', cnt_path, 'CCindyPT'))
        result['after_dashboard'] = compact(request('GET', cnt_path, 'CDashboard'))
    finally:
        for path in reversed(created):
            deleted = compact(request('DELETE', path, 'CAdmin'))
            absent = compact(request('GET', path, 'CAdmin'))
            result['cleanup'].append({'path': path, 'delete': deleted, 'absent': absent})
    return result


def subscription_guard_probe():
    """Verify a child SUB remains private when its parent allows webcam GET."""
    suffix = uuid.uuid4().hex[:12]
    parent = 'CindySense_AE'
    cnt_path = f'{parent}/SubscriptionGuard_{suffix}_Summary'
    sub_path = cnt_path + '/Disposable_Sub'
    result = {'cnt_path': cnt_path, 'sub_path': sub_path, 'cleanup': []}
    created = []
    try:
        existing_acp = request('GET', 'CindySense_ACP', 'CAdmin')
        result['existing_acp_get'] = compact(existing_acp)
        if result['existing_acp_get'] != {'http': 200, 'rsc': '2000'}:
            return result
        result['subscription_acp_ri'] = existing_acp['body']['m2m:acp']['ri']
        privileges = [{'acor': ['CAdmin'], 'acop': 63},
                      {'acor': ['CWebcam'], 'acop': 3},
                      {'acor': ['CCindyPT', 'CDashboard'], 'acop': 2}]
        name = f'SubscriptionGuard_{suffix}_publisher_ACP'
        path = f'{parent}/{name}'
        response = request('POST', parent, 'CAdmin', {
            'm2m:acp': {'rn': name, 'pv': {'acr': privileges},
                        'pvs': {'acr': [{'acor': ['CAdmin'], 'acop': 63}]}}}, 1)
        result['publisher_acp_create'] = compact(response)
        if result['publisher_acp_create'] != {'http': 201, 'rsc': '2001'}:
            return result
        created.append(path)
        result['publisher_acp_ri'] = response['body']['m2m:acp']['ri']
        response = request('POST', parent, 'CAdmin',
                           {'m2m:cnt': {'rn': cnt_path.rsplit('/', 1)[-1]}}, 3)
        result['cnt_create'] = compact(response)
        if result['cnt_create'] != {'http': 201, 'rsc': '2001'}:
            return result
        created.append(cnt_path)
        # The administrative AE target avoids any callback URL or token.
        response = request('POST', cnt_path, 'CAdmin', {
            'm2m:sub': {'rn': 'Disposable_Sub', 'nu': ['CAdmin'],
                        'enc': {'net': [3]}, 'nct': 1}}, 23)
        result['sub_create'] = compact(response)
        if result['sub_create'] != {'http': 201, 'rsc': '2001'}:
            return result
        result['sub_guard_apply'] = compact(request('PUT', sub_path, 'CAdmin',
                                                     {'m2m:sub': {'acpi': [result['subscription_acp_ri']]}}))
        result['publisher_apply'] = compact(request('PUT', cnt_path, 'CAdmin',
                                                     {'m2m:cnt': {'acpi': [result['publisher_acp_ri']]}}))
        sub = request('GET', sub_path, 'CAdmin')
        result['sub_acpi'] = sub['body'].get('m2m:sub', {}).get('acpi')
        result['reads'] = {origin: {'container': compact(request('GET', cnt_path, origin)),
                                    'subscription': compact(request('GET', sub_path, origin))}
                           for origin in ('CAdmin', *ORIGINS)}
        result['webcam_post'] = compact(request('POST', cnt_path, 'CWebcam',
                                                {'m2m:cin': {'rn': 'disposable-event',
                                                            'con': 'guard-probe'}}, 4))
        result['publisher_rollback'] = compact(request('PUT', cnt_path, 'CAdmin',
                                                        {'m2m:cnt': {'acpi': None}}))
        result['sub_guard_rollback'] = compact(request('PUT', sub_path, 'CAdmin',
                                                        {'m2m:sub': {'acpi': None}}))
        restored = request('GET', sub_path, 'CAdmin')
        result['restored_sub_acpi'] = restored['body'].get('m2m:sub', {}).get('acpi')
        result['restored_webcam_container'] = compact(request('GET', cnt_path, 'CWebcam'))
        result['restored_webcam_sub'] = compact(request('GET', sub_path, 'CWebcam'))
        result['restored_cindypt_sub'] = compact(request('GET', sub_path, 'CCindyPT'))
    finally:
        for path in reversed(created):
            deleted = compact(request('DELETE', path, 'CAdmin'))
            absent = compact(request('GET', path, 'CAdmin'))
            result['cleanup'].append({'path': path, 'delete': deleted, 'absent': absent})
        if cnt_path in created:
            result['sub_absent'] = compact(request('GET', sub_path, 'CAdmin'))
    return result


def production_publisher_disposable_probe():
    """Exercise the installed publisher ACP without touching workout containers."""
    acp_path = 'CindySense_AE/CindySense_WebcamPublisher_ACP'
    acp = request('GET', acp_path, 'CAdmin')
    result = {'acp_get': compact(acp), 'operations': {}}
    if result['acp_get'] != {'http': 200, 'rsc': '2000'}:
        return result
    acp_ri = acp['body']['m2m:acp']['ri']
    name = 'PublisherPolicyCheck_' + uuid.uuid4().hex[:12]
    path = f'CindySense_AE/{name}'
    result['path'] = path
    created = False
    try:
        response = request('POST', 'CindySense_AE', 'CAdmin',
                           {'m2m:cnt': {'rn': name, 'acpi': [acp_ri]}}, 3)
        result['create'] = compact(response)
        if result['create'] != {'http': 201, 'rsc': '2001'}:
            return result
        created = True
        stored = request('GET', path, 'CAdmin')
        result['readback'] = compact(stored)
        result['acpi'] = stored['body'].get('m2m:cnt', {}).get('acpi')
        result['acp_ri'] = acp_ri
        for origin in (*ORIGINS, 'CIntruder'):
            read = request('GET', path, origin)
            posted = request('POST', path, origin, {'m2m:cin': {
                'rn': 'check_' + origin, 'con': 'disposable-publisher-check'}}, 4)
            result['operations'][origin] = {'get': compact(read), 'post': compact(posted)}
            if compact(posted) == {'http': 201, 'rsc': '2001'}:
                child = request('GET', path + '/check_' + origin, origin)
                result['operations'][origin]['cin_readback'] = compact(child)
                result['operations'][origin]['content_verified'] = (
                    child['body'].get('m2m:cin', {}).get('con') == 'disposable-publisher-check')
    finally:
        if created:
            result['cleanup'] = compact(request('DELETE', path, 'CAdmin'))
            result['absent'] = compact(request('GET', path, 'CAdmin'))
    return result


def verify_disposable_results(disposable, inherited):
    success = {'http': 200, 'rsc': '2000'}
    created = {'http': 201, 'rsc': '2001'}
    denied = {'http': 403, 'rsc': '4103'}
    deleted = {'http': 200, 'rsc': '2002'}
    absent = {'http': 404, 'rsc': '4004'}
    for label, result in disposable.items():
        if result.get('acp_create') != created:
            raise RuntimeError(f'{label}: temporary ACP creation failed')
        for kind, entry in result['containers'].items():
            if entry.get('create') != created or entry.get('readback') != success or entry.get('acpi') != [result['acp_ri']]:
                raise RuntimeError(f'{label}/{kind}: CNT creation or ACP assignment failed')
            for origin in ORIGINS:
                operations = entry['operations'][origin]
                can_read = origin != 'CWebcam' or label == 'workout'
                can_write = origin == ('CWebcam' if label == 'workout' else 'CCindyPT')
                if operations['get'] != (success if can_read else denied):
                    raise RuntimeError(f'{label}/{kind}/{origin}: GET permission changed')
                if operations['post'] != (created if can_write else denied):
                    raise RuntimeError(f'{label}/{kind}/{origin}: POST permission changed')
                if can_write and (operations.get('cin_readback') != success or not operations.get('content_verified')):
                    raise RuntimeError(f'{label}/{kind}/{origin}: stored CIN was not verified')
        if len(result['cleanup']) != len(result['containers']) + 1:
            raise RuntimeError(f'{label}: disposable cleanup incomplete')
        for entry in result['cleanup']:
            if entry.get('delete') != deleted or entry.get('absent') != absent:
                raise RuntimeError(f'{label}: disposable cleanup failed at {entry["path"]}')
    if inherited.get('create') != created or inherited.get('readback') != success:
        raise RuntimeError('Inherited production policy setup failed')
    for origin in ORIGINS:
        operations = inherited['operations'][origin]
        if operations['get'] != (denied if origin == 'CWebcam' else success) or operations['post'] != denied:
            raise RuntimeError(f'Inherited policy changed for {origin}')
    if inherited.get('cleanup') != deleted or inherited.get('absent') != absent:
        raise RuntimeError('Inherited policy cleanup failed')


def verify_migration_roundtrip(result):
    ok = {'http': 200, 'rsc': '2000'}
    created = {'http': 201, 'rsc': '2001'}
    updated = {'http': 200, 'rsc': '2004'}
    denied = {'http': 403, 'rsc': '4103'}
    expected = {'acp_create': created, 'cnt_create': created, 'before': denied,
                'apply': updated, 'applied_readback': ok, 'publisher_get': ok,
                'publisher_post': created, 'publisher_cin_readback': ok,
                'rollback': updated, 'rollback_readback': ok,
                'after_webcam': denied, 'after_cindypt': ok, 'after_dashboard': ok}
    for name, value in expected.items():
        if result.get(name) != value:
            raise RuntimeError(f'Disposable migration roundtrip failed: {name}')
    if result.get('applied_acpi') != [result.get('acp_ri')] or result.get('rollback_acpi') is not None:
        raise RuntimeError('Disposable migration ACP assignment was not restored')
    verify_cleanup(result, 2)


def verify_cleanup(result, expected_count):
    if len(result.get('cleanup', [])) != expected_count:
        raise RuntimeError('Disposable cleanup has the wrong resource count')
    for entry in result['cleanup']:
        if entry.get('delete') != {'http': 200, 'rsc': '2002'} or entry.get('absent') != {'http': 404, 'rsc': '4004'}:
            raise RuntimeError(f'Disposable cleanup failed: {entry["path"]}')


def verify_subscription_guard(result):
    ok = {'http': 200, 'rsc': '2000'}
    created = {'http': 201, 'rsc': '2001'}
    updated = {'http': 200, 'rsc': '2004'}
    denied = {'http': 403, 'rsc': '4103'}
    expected = {'existing_acp_get': ok, 'publisher_acp_create': created,
                'cnt_create': created, 'sub_create': created,
                'sub_guard_apply': updated, 'publisher_apply': updated,
                'webcam_post': created, 'publisher_rollback': updated,
                'sub_guard_rollback': updated,
                'restored_webcam_container': denied, 'restored_webcam_sub': denied,
                'restored_cindypt_sub': ok,
                'sub_absent': {'http': 404, 'rsc': '4004'}}
    for name, value in expected.items():
        if result.get(name) != value:
            raise RuntimeError(f'Disposable subscription guard failed: {name}')
    if result.get('sub_acpi') != [result.get('subscription_acp_ri')]:
        raise RuntimeError('Subscription guard ACP was not stored')
    if result.get('restored_sub_acpi') is not None:
        raise RuntimeError('Subscription guard ACP was not restored')
    for origin in ('CAdmin', *ORIGINS):
        reads = result['reads'][origin]
        if reads['container'] != ok or reads['subscription'] != (denied if origin == 'CWebcam' else ok):
            raise RuntimeError(f'Subscription guard access changed for {origin}')
    verify_cleanup(result, 2)


def verify_production_publisher_disposable(result):
    ok = {'http': 200, 'rsc': '2000'}
    created = {'http': 201, 'rsc': '2001'}
    denied = {'http': 403, 'rsc': '4103'}
    if result.get('acp_get') != ok or result.get('create') != created or result.get('readback') != ok:
        raise RuntimeError('Production publisher disposable setup failed')
    if result.get('acpi') != [result.get('acp_ri')]:
        raise RuntimeError('Production publisher ACP assignment was not stored')
    for origin in (*ORIGINS, 'CIntruder'):
        operations = result['operations'][origin]
        expected_get = denied if origin == 'CIntruder' else ok
        expected_post = created if origin == 'CWebcam' else denied
        if operations['get'] != expected_get or operations['post'] != expected_post:
            raise RuntimeError(f'Production publisher permissions changed for {origin}')
        if origin == 'CWebcam' and (operations.get('cin_readback') != ok or
                                   not operations.get('content_verified')):
            raise RuntimeError('Production publisher CIN was not verified')
    if result.get('cleanup') != {'http': 200, 'rsc': '2002'} or result.get('absent') != {'http': 404, 'rsc': '4004'}:
        raise RuntimeError('Production publisher disposable cleanup failed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', action='store_true', help='Create and clean disposable ACP/CNT resources')
    parser.add_argument('--backup', metavar='PATH', help='Write a new read-only ACP/assignment backup')
    parser.add_argument('--backup-subscriptions', metavar='PATH',
                        help='Write a token-free backup of subscription ACP assignments')
    parser.add_argument('--migration-roundtrip', action='store_true',
                        help='Apply and roll back the proposed ACP on disposable resources')
    parser.add_argument('--subscription-guard', action='store_true',
                        help='Prove a child subscription stays private under the publisher ACP')
    parser.add_argument('--production-policy-probe', action='store_true',
                        help='Test the installed publisher ACP on one disposable CNT')
    args = parser.parse_args()
    if args.backup:
        backup_production(args.backup)
    if args.backup_subscriptions:
        backup_subscription_assignments(args.backup_subscriptions)
    output = {'production': production_readback()}
    if args.probe:
        output['disposable'] = disposable_probe()
        output['inherited_policy'] = inherited_policy_probe()
    if args.migration_roundtrip:
        output['migration_roundtrip'] = migration_roundtrip_probe()
    if args.subscription_guard:
        output['subscription_guard'] = subscription_guard_probe()
    if args.production_policy_probe:
        output['production_policy_probe'] = production_publisher_disposable_probe()
    print(json.dumps(output, indent=2))
    if args.probe:
        verify_disposable_results(output['disposable'], output['inherited_policy'])
    if args.migration_roundtrip:
        verify_migration_roundtrip(output['migration_roundtrip'])
    if args.subscription_guard:
        verify_subscription_guard(output['subscription_guard'])
    if args.production_policy_probe:
        verify_production_publisher_disposable(output['production_policy_probe'])


if __name__ == '__main__':
    main()
