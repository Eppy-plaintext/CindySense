"""Apply the approved CindySense publisher ACP with verified rollback.

This tool intentionally requires --apply-approved. It never prints callback URLs.
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .onem2m_retention_acp_probe import compact, request
else:
    from onem2m_retention_acp_probe import compact, request


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs' / 'hackathon-evidence'
ACP_BACKUP = EVIDENCE / 'acp-production-backup-20261112.json'
SUB_BACKUP = EVIDENCE / 'acp-subscription-assignments-backup-20261112.json'
BACKUP_HASHES = {
    ACP_BACKUP: '2e64ee888166a4b0e5637c6fa9fdd2f0df3afd095efa1c418cb9cdd5c917423f',
    SUB_BACKUP: '2e134ae7cea6c0130f46a9a13bd13cb469a2df6d575ff5dc924065376b7c2ca5',
}
OLD_ACP = 'CindySense_ACP'
NEW_ACP = 'CindySense_AE/CindySense_WebcamPublisher_ACP'
CONTAINERS = ('CindySense_AE/RepetitionCount', 'CindySense_AE/WorkoutSummary')
SUBSCRIPTIONS = ('CindySense_AE/RepetitionCount/RepCount_Sub',
                 'CindySense_AE/WorkoutSummary/Summary_Sub')
PUBLISHER_POLICY = {
    'pv': {'acr': [
        {'acor': ['CAdmin'], 'acop': 63},
        {'acor': ['CWebcam'], 'acop': 3},
        {'acor': ['CCindyPT', 'CDashboard'], 'acop': 2},
    ]},
    'pvs': {'acr': [{'acor': ['CAdmin'], 'acop': 63}]},
}
SUCCESS = {'http': 200, 'rsc': '2000'}
UPDATED = {'http': 200, 'rsc': '2004'}
CREATED = {'http': 201, 'rsc': '2001'}
DENIED = {'http': 403, 'rsc': '4103'}
ABSENT = {'http': 404, 'rsc': '4004'}
DELETED = {'http': 200, 'rsc': '2002'}


def expect(response, expected, label, log):
    status = compact(response)
    log.append({'operation': label, **status})
    if status != expected:
        raise RuntimeError(f'{label}: expected {expected}, got {status}')
    return response


def get(path, origin, expected, log):
    return expect(request('GET', path, origin), expected, f'GET {path} as {origin}', log)


def preflight(log):
    for path, digest in BACKUP_HASHES.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Backup checksum changed: {path.name}')
    acp_backup = json.loads(ACP_BACKUP.read_text(encoding='utf-8'))['resources']
    sub_backup = json.loads(SUB_BACKUP.read_text(encoding='utf-8'))['subscriptions']
    get('', 'CAdmin', SUCCESS, log)
    get(NEW_ACP, 'CAdmin', ABSENT, log)
    originals = {}
    for path, saved in acp_backup.items():
        live = get(path, 'CAdmin', SUCCESS, log)['body']
        kind = next(iter(saved))
        old = saved[kind]
        current = live.get(kind)
        if not isinstance(current, dict) or current.get('ri') != old.get('ri') or current.get('ty') != old.get('ty'):
            raise RuntimeError(f'Resource identity changed: {path}')
        if current.get('acpi') != old.get('acpi'):
            raise RuntimeError(f'ACP assignment changed: {path}')
        if kind == 'm2m:acp' and (current.get('pv') != old.get('pv') or current.get('pvs') != old.get('pvs')):
            raise RuntimeError(f'ACP permissions changed: {path}')
        originals[path] = current
    subscriptions = {}
    for path, saved in sub_backup.items():
        live = get(path, 'CAdmin', SUCCESS, log)['body'].get('m2m:sub')
        if not isinstance(live, dict) or any(live.get(key) != saved.get(key) for key in
                                             ('rn', 'ri', 'acpi', 'enc', 'nct')):
            raise RuntimeError(f'Subscription identity or configuration changed: {path}')
        if len(live.get('nu', [])) != saved['notificationUriCount'] or not live.get('nu'):
            raise RuntimeError(f'Subscription URI count changed: {path}')
        subscriptions[path] = live
    # Preserve the existing authorized read paths before altering inheritance.
    for path in (*CONTAINERS, *SUBSCRIPTIONS):
        for origin in ('CCindyPT', 'CDashboard'):
            get(path, origin, SUCCESS, log)
        get(path, 'CWebcam', DENIED, log)
    expected_old = acp_backup[OLD_ACP]['m2m:acp']['ri']
    if expected_old != '1-20261026T160317,2210029':
        raise RuntimeError('Approved existing ACP RI changed')
    if originals['CindySense_AE'].get('acpi') != [expected_old]:
        raise RuntimeError('CindySense AE parent assignment changed')
    for path in (*CONTAINERS, *SUBSCRIPTIONS):
        if (originals if path in originals else subscriptions)[path].get('acpi') is not None:
            raise RuntimeError(f'Expected inherited ACP on {path}')
    log.append({'operation': 'preflight verified', 'result': 'PASS'})
    return expected_old, subscriptions


def verify_sub(path, expected_acpi, original, log):
    current = get(path, 'CAdmin', SUCCESS, log)['body'].get('m2m:sub')
    if not isinstance(current, dict) or current.get('acpi') != expected_acpi:
        raise RuntimeError(f'Subscription ACP readback changed: {path}')
    if any(current.get(key) != original.get(key) for key in ('ri', 'rn', 'nu', 'enc', 'nct')):
        raise RuntimeError(f'Subscription delivery configuration changed: {path}')
    for origin in ('CCindyPT', 'CDashboard'):
        get(path, origin, SUCCESS, log)
    get(path, 'CWebcam', DENIED, log)


def verify_cnt(path, expected_acpi, webcam_status, log):
    current = get(path, 'CAdmin', SUCCESS, log)['body'].get('m2m:cnt')
    if not isinstance(current, dict) or current.get('acpi') != expected_acpi:
        raise RuntimeError(f'Container ACP readback changed: {path}')
    get(path, 'CWebcam', webcam_status, log)
    for origin in ('CCindyPT', 'CDashboard'):
        get(path, origin, SUCCESS, log)


def apply(old_ri, originals, log, state):
    payload = {'m2m:acp': {'rn': NEW_ACP.rsplit('/', 1)[-1], **PUBLISHER_POLICY}}
    # A lost HTTP reply can follow a successful creation; inspect the path on failure.
    state['acp_may_exist'] = True
    created = expect(request('POST', 'CindySense_AE', 'CAdmin', payload, 1),
                     CREATED, 'POST new publisher ACP', log)
    new_ri = created['body'].get('m2m:acp', {}).get('ri')
    if not isinstance(new_ri, str) or not new_ri:
        raise RuntimeError('New publisher ACP has no RI')
    state['new_ri'] = new_ri
    stored = get(NEW_ACP, 'CAdmin', SUCCESS, log)['body'].get('m2m:acp')
    if not isinstance(stored, dict) or stored.get('ri') != new_ri or any(
            stored.get(key) != PUBLISHER_POLICY[key] for key in ('pv', 'pvs')):
        raise RuntimeError('New publisher ACP representation mismatch')
    for path in SUBSCRIPTIONS:
        state['changed_subs'].append(path)
        expect(request('PUT', path, 'CAdmin', {'m2m:sub': {'acpi': [old_ri]}}),
               UPDATED, f'PUT guard on {path}', log)
        verify_sub(path, [old_ri], originals[path], log)
    for path in CONTAINERS:
        state['changed_cnts'].append(path)
        expect(request('PUT', path, 'CAdmin', {'m2m:cnt': {'acpi': [new_ri]}}),
               UPDATED, f'PUT publisher on {path}', log)
        verify_cnt(path, [new_ri], SUCCESS, log)
        for sub_path in SUBSCRIPTIONS:
            verify_sub(sub_path, [old_ri], originals[sub_path], log)
    return new_ri


def rollback(originals, log, state):
    errors = []
    for path in reversed(state['changed_cnts']):
        try:
            expect(request('PUT', path, 'CAdmin', {'m2m:cnt': {'acpi': None}}),
                   UPDATED, f'ROLLBACK {path}', log)
            verify_cnt(path, None, DENIED, log)
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        return errors  # Never expose an unguarded SUB while a parent is permissive.
    for path in reversed(state['changed_subs']):
        try:
            expect(request('PUT', path, 'CAdmin', {'m2m:sub': {'acpi': None}}),
                   UPDATED, f'ROLLBACK {path}', log)
            verify_sub(path, None, originals[path], log)
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        return errors
    acp = request('GET', NEW_ACP, 'CAdmin')
    log.append({'operation': 'GET publisher ACP before cleanup', **compact(acp)})
    if compact(acp) == SUCCESS:
        new_ri = acp['body'].get('m2m:acp', {}).get('ri')
        if any(get(path, 'CAdmin', SUCCESS, log)['body']['m2m:cnt'].get('acpi') == [new_ri]
               for path in CONTAINERS):
            return ['Publisher ACP remains referenced; refusing DELETE']
        try:
            expect(request('DELETE', NEW_ACP, 'CAdmin'), DELETED,
                   'DELETE new publisher ACP', log)
            get(NEW_ACP, 'CAdmin', ABSENT, log)
        except Exception as exc:
            errors.append(str(exc))
    elif compact(acp) != ABSENT:
        errors.append(f'Publisher ACP retrieval failed: {compact(acp)}')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply-approved', action='store_true')
    args = parser.parse_args()
    if not args.apply_approved:
        parser.error('Production migration requires --apply-approved')
    log = []
    state = {'changed_cnts': [], 'changed_subs': [], 'acp_may_exist': False, 'new_ri': None}
    result = {'at': datetime.now(timezone.utc).isoformat(), 'operations': log,
              'publisher_acp_ri': None, 'outcome': 'preflight_failed'}
    originals = {}
    try:
        old_ri, originals = preflight(log)
        result['outcome'] = 'applying'
        result['publisher_acp_ri'] = apply(old_ri, originals, log, state)
        result['outcome'] = 'applied_and_verified'
    except Exception as exc:
        result['error'] = str(exc)
        if state['acp_may_exist'] or state['changed_cnts'] or state['changed_subs']:
            errors = rollback(originals, log, state)
            result['rollback_errors'] = errors
            result['outcome'] = 'rolled_back' if not errors else 'rollback_incomplete'
    destination = EVIDENCE / 'acp-migration-execution-20261112.json'
    if destination.exists():
        raise FileExistsError(f'Execution evidence already exists: {destination}')
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({key: result[key] for key in ('outcome', 'publisher_acp_ri')}, indent=2))
    if result['outcome'] != 'applied_and_verified':
        raise RuntimeError(f'ACP migration ended with {result["outcome"]}; see execution evidence')


if __name__ == '__main__':
    main()
