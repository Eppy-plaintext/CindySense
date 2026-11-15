"""Join retained repetition observations to completed summaries by session ID."""

from datetime import datetime, timezone


def _recorded_time(row: dict) -> datetime:
    value = row.get('recordedAt') or row.get('lastEventAt')
    if not isinstance(value, str):
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = (datetime.strptime(value[:15], '%Y%m%dT%H%M%S') if value[:8].isdigit() and 'T' in value else
                  datetime.fromisoformat(value.replace('Z', '+00:00')))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)

def build_history(observed: dict, summaries: list[dict], goal: dict | None = None) -> dict:
    by_id = {item['workoutId']: item for item in summaries}
    sessions = []
    for item in observed['availableSessions']:
        session_id = item['sessionId']
        summary = by_id.pop(session_id, None)
        counts = item['exerciseCounts']
        matching = bool(summary and summary.get('reps') == counts)
        source_verified = bool(summary and summary.get('source') == 'webcam' and not summary.get('synthetic'))
        reason = summary.get('completionReason') if summary else None
        if summary and summary.get('synthetic'):
            status = 'test_or_synthetic'
        elif summary and not source_verified:
            status = 'unverified_summary'
        elif summary and not matching:
            status = 'summary_event_mismatch'
        elif reason == 'timer' and summary['durationSeconds'] >= 1140:
            status = 'completed_20_minute'
        elif reason == 'explicit_stop':
            status = 'stopped_early'
        elif summary:
            status = 'completed_duration_unverified'
        else:
            status = 'no_verified_summary'
        comparable = status == 'completed_20_minute' and item['suitable'] and item['eventCount'] >= 30
        sessions.append({**item, 'status': status, 'summaryVerified': source_verified and matching,
                         'comparable': comparable,
                         'completionReason': reason,
                         'durationSeconds': summary['durationSeconds'] if summary else None,
                         'completedRounds': summary['roundsCompleted'] if summary else None,
                         'recordedAt': summary.get('recordedAt') if summary else None,
                         'targetRounds': goal['targetRounds'] if goal else None,
                         'targetProgressPercent': round(100 * summary['roundsCompleted'] / goal['targetRounds'], 1)
                         if comparable and goal else None})
    # A summary without retained event evidence is visible, but cannot be used
    # for pace or fair comparison. Retention may have removed its repetitions.
    for summary in by_id.values():
        sessions.append({'sessionId': summary['workoutId'], 'eventCount': 0,
                         'exerciseCounts': summary.get('reps'), 'firstEventAt': None,
                         'lastEventAt': None, 'suitable': False,
                         'performance': None,
                         'status': ('test_or_synthetic' if summary.get('synthetic') else
                                    'unverified_summary' if summary.get('source') != 'webcam' else
                                    'summary_without_retained_events'),
                         'summaryVerified': False, 'comparable': False,
                         'completionReason': summary.get('completionReason'),
                         'durationSeconds': summary['durationSeconds'],
                         'completedRounds': summary['roundsCompleted'],
                         'recordedAt': summary.get('recordedAt'),
                         'targetRounds': goal['targetRounds'] if goal else None,
                         'targetProgressPercent': None})
    sessions.sort(key=_recorded_time, reverse=True)
    comparable = [row for row in sessions if row['comparable']]
    for index, row in enumerate(comparable):
        previous = comparable[index + 1] if index + 1 < len(comparable) else None
        row['roundChangeFromPrevious'] = row['completedRounds'] - previous['completedRounds'] if previous else None
    return {'available': bool(sessions), 'source': 'retained TinyIoT repetitions and workout summaries',
            'sessions': sessions, 'comparableSessionCount': len(comparable),
            'retainedInstanceCount': observed['retainedInstanceCount'],
            'exclusionReasons': observed['exclusionReasons'],
            'message': None if sessions else 'No eligible retained webcam events or completed summaries.'}
