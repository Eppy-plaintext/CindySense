"""Select evidence for CindyPT without conflating a recent test with a full Cindy workout."""

from .cindypt import analyze


def select_workout(history: dict, summaries: list[dict], goal: dict) -> dict:
    rows = [row for row in history['sessions'] if row['status'] != 'test_or_synthetic']
    latest = rows[0] if rows else None
    by_id = {item['workoutId']: item for item in summaries}
    full = next((row for row in rows if row['comparable'] and row['summaryVerified'] and
                 row['eventCount'] >= 30 and row['suitable']), None)
    limited = next((row for row in rows if row['status'] == 'stopped_early' and
                    row['summaryVerified'] and row['durationSeconds'] >= 120 and
                    row['suitable']), None)
    selected = full or limited
    workout = by_id.get(selected['sessionId']) if selected else None
    if selected:
        reason = ('Verified timer completion with matching retained repetition counts.' if full else
                  'Verified explicit stop; pace observations are limited and cannot change a 20-minute target.')
        status = 'sufficient' if full else 'not_comparable'
    elif latest:
        status = ('unverified_summary' if latest['status'] in ('unverified_summary', 'summary_event_mismatch',
                   'summary_without_retained_events') else 'insufficient_data')
        reason = (f"Latest session is {latest['status'].replace('_', ' ')}; "
                  'no sufficiently verified workout is available for personalized target adjustments.')
    else:
        status = 'insufficient_data'
        reason = 'No legitimate retained workout session is available.'
    progress = analyze(goal, workout)
    if selected and not full:
        progress['goalAchieved'] = False  # A short practice cannot achieve a 20-minute AMRAP target.
    quality = (selected.get('performance') or {}).get('detectorQuality') if selected else None
    progress.update({'latestWorkout': latest, 'coachingWorkout': selected,
                     'selectionStatus': status, 'selectionReason': reason,
                     'detectorReliability': 'verified' if quality == 'verified' else 'detector_quality_unknown',
                     'trainingPlanChangeAppropriate': False,
                     'comparableSessionCount': history['comparableSessionCount']})
    return progress
