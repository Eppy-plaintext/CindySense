"""Deterministic, conservative seven-day Cindy planning."""

import hashlib
import json
from datetime import datetime, timedelta
from statistics import median_low
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


DAY_NAMES = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
HISTORICAL_TEST_ID = '4-20261026T162428,4070040'


class PlanPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    timezone: str = Field(min_length=1, max_length=100)
    trainingDays: list[int] = Field(default_factory=lambda: [0, 2, 4], min_length=1, max_length=4)
    experienceLevel: str = 'beginner'

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError('Use an IANA timezone, such as Asia/Seoul.') from exc
        return value

    @field_validator('trainingDays')
    @classmethod
    def valid_days(cls, value):
        if any(type(day) is not int or day < 0 or day > 6 for day in value) or len(set(value)) != len(value):
            raise ValueError('Choose distinct weekdays numbered 0 through 6.')
        return sorted(value)

    @field_validator('experienceLevel')
    @classmethod
    def valid_experience(cls, value):
        if value not in ('beginner', 'intermediate', 'advanced'):
            raise ValueError('Experience must be beginner, intermediate, or advanced.')
        return value


class SavePlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    preferences: PlanPreferences
    planId: str = Field(pattern=r'^[a-f0-9]{64}$')


def local_today(timezone: str):
    return datetime.now(ZoneInfo(timezone)).date()


def generate_weekly_plan(goal: dict, summaries: list[dict], preferences: PlanPreferences,
                         today=None) -> dict:
    today = today or local_today(preferences.timezone)
    monday = today + timedelta(days=(7 - today.weekday()) % 7)
    recent = summaries[:3]
    latest = recent[0] if recent else None
    limited = len(summaries) < 2
    if latest:
        baseline = min(latest['roundsCompleted'], median_low(item['roundsCompleted'] for item in recent))
        baseline = max(1, baseline)
        achieved = (latest['roundsCompleted'] >= goal['targetRounds'] and
                    latest['durationSeconds'] <= goal['durationSeconds'])
        progression_ready = preferences.experienceLevel != 'beginner' or len(summaries) >= 2
        max_rounds = goal['targetRounds'] if achieved else max(1, goal['targetRounds'] - 1)
        next_rounds = min(max_rounds, baseline if achieved or not progression_ready else baseline + 1)
        next_rounds = min(next_rounds, latest['roundsCompleted'] + 1)
        session_seconds = min(goal['durationSeconds'], max(300, latest['durationSeconds']))
    else:
        baseline = 1
        achieved = False
        next_rounds = 1
        max_rounds = max(1, goal['targetRounds'] - 1)
        session_seconds = min(goal['durationSeconds'], 600)
    workout_index = 0
    days = []
    for offset, name in enumerate(DAY_NAMES):
        day = monday + timedelta(days=offset)
        if offset in preferences.trainingDays:
            adjacent = offset > 0 and offset - 1 in preferences.trainingDays
            lighter = adjacent or (preferences.experienceLevel == 'beginner' and workout_index >= 3)
            rounds = baseline if lighter or workout_index == 0 or achieved else next_rounds
            rounds = min(rounds, max_rounds)
            focus = 'Technique and easy pace' if lighter else ('Repeat with good form' if achieved else 'Steady Cindy practice')
            reason = ('Keep consecutive or extra beginner sessions lighter.' if lighter else
                      'Repeat a proven volume before progressing.' if achieved else
                      'Build from completed work by at most one round.' if latest and progression_ready else
                      'Repeat current volume until more sessions are available.' if latest else
                      'Start with one round because no completed workout is available.')
            days.append({'date': day.isoformat(), 'weekday': name, 'type': 'workout',
                         'suggestedRounds': rounds, 'suggestedDurationSeconds': session_seconds,
                         'exerciseVolume': {'pullup': rounds * 5, 'pushup': rounds * 10, 'squat': rounds * 15},
                         'focus': focus, 'reason': reason})
            workout_index += 1
        else:
            days.append({'date': day.isoformat(), 'weekday': name, 'type': 'recovery',
                         'suggestedRounds': 0, 'suggestedDurationSeconds': 0,
                         'exerciseVolume': None, 'focus': 'Rest or easy movement',
                         'reason': 'Allow recovery between Cindy sessions.'})
    recovery = [
        'Keep recovery days easy. Rest if fatigue or soreness persists.',
        'Use good form and stop a session if pain or dizziness occurs.',
    ]
    test_goal = isinstance(goal.get('label'), str) and goal['label'].upper().startswith('TEST')
    provenance = ('historical-test' if latest and latest['workoutId'] == HISTORICAL_TEST_ID else
                  'unverified' if latest and not latest.get('source') else
                  latest.get('source') if latest else 'no-workout-history')
    plan = {'source': 'rule-based', 'weekStart': monday.isoformat(),
            'timezone': preferences.timezone, 'experienceLevel': preferences.experienceLevel,
            'trainingDays': preferences.trainingDays, 'goal': goal,
            'sourceWorkoutId': latest['workoutId'] if latest else None,
            'completedWorkoutCount': len(summaries), 'limitedInformation': limited,
            'testGoal': test_goal, 'workoutProvenance': provenance,
            'days': days, 'recoveryRecommendations': recovery}
    canonical = json.dumps(plan, sort_keys=True, separators=(',', ':'))
    plan['planId'] = hashlib.sha256(canonical.encode()).hexdigest()
    return plan
