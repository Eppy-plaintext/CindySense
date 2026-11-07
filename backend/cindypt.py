"""Rule-based Cindy coaching from completed TinyIoT workout summaries."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .tinyiot import TinyIoTClient, TinyIoTError


class Goal(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    label: str | None = Field(default=None, min_length=1, max_length=100)
    targetRounds: int = Field(ge=1, le=100)
    durationSeconds: int = Field(ge=60, le=7200)


class WorkoutSummary(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)

    workoutId: str | None = None
    sessionId: str | None = None
    rounds: int = Field(ge=0)
    durationSeconds: int = Field(gt=0)
    reps: dict[str, int] | None = None
    pullups: int | None = Field(default=None, ge=0)
    pushups: int | None = Field(default=None, ge=0)
    squats: int | None = Field(default=None, ge=0)
    status: str | None = None

    @model_validator(mode='after')
    def check_reps(self):
        if self.reps is not None and (set(self.reps) - {'pullup', 'pushup', 'squat'} or
                                      any(value < 0 for value in self.reps.values())):
            raise ValueError('reps must contain nonnegative Cindy exercise counts')
        top_level = self.exercise_counts_from_top_level()
        if self.reps is not None and any(self.reps.get(key) != value for key, value in top_level.items()):
            raise ValueError('reps conflicts with top-level Cindy exercise counts')
        return self

    def exercise_counts_from_top_level(self) -> dict[str, int]:
        return {key: value for key, value in (
            ('pullup', self.pullups), ('pushup', self.pushups), ('squat', self.squats))
            if value is not None}

    def exercise_counts(self) -> dict[str, int] | None:
        counts = self.exercise_counts_from_top_level()
        if self.reps is not None:
            return self.reps
        return counts or None


def _parse(model, content, label):
    try:
        return model.model_validate(content)
    except ValidationError as exc:
        raise TinyIoTError('invalid_content', f'Stored {label} is malformed.', 502) from exc


async def read_goal(client: TinyIoTClient, resource: str, origin: str) -> dict | None:
    try:
        content, _ = await client.latest_content(resource, origin)
    except TinyIoTError as exc:
        if exc.code == 'resource_not_found':
            container = await client.retrieve(resource, origin)
            if 'm2m:cnt' in container:
                return None
        raise
    return _parse(Goal, content, 'goal').model_dump()


async def read_summaries(client: TinyIoTClient, resource: str, origin: str) -> list[dict]:
    container = await client.retrieve(resource, origin)
    if 'm2m:cnt' not in container:
        raise TinyIoTError('invalid_resource', 'Workout summary resource is not a container.', 502)
    refs = await client.list_content_instances(resource, origin, expected_count=container['m2m:cnt'].get('cni'))
    summaries = []
    seen = {}
    for ref in refs:
        content, instance = client.parse_content_instance(await client.retrieve(ref, origin))
        if isinstance(content, dict):
            status = content.get('status')
            if not isinstance(status, str) or status.lower() not in ('complete', 'completed'):
                continue
        summary = _parse(WorkoutSummary, content, 'workout summary')
        identity = summary.workoutId or summary.sessionId or instance.get('ri') or ref
        normalized = {'rounds': summary.rounds, 'durationSeconds': summary.durationSeconds,
                      'reps': summary.exercise_counts()}
        if identity in seen:
            if seen[identity] != normalized:
                raise TinyIoTError('duplicate_workout_conflict', 'A workout ID has conflicting summaries.', 502)
            continue
        seen[identity] = normalized
        summaries.append({'workoutId': identity, 'roundsCompleted': summary.rounds,
                          'durationSeconds': summary.durationSeconds, 'reps': summary.exercise_counts(),
                          'recordedAt': instance.get('ct'),
                          'source': content.get('source') if isinstance(content, dict) else None})
    return summaries


def analyze(goal: dict, workout: dict | None) -> dict:
    if workout is None:
        return {'available': False, 'goal': goal, 'workout': None, 'message': 'No completed workout summary is stored.'}
    rounds = workout['roundsCompleted']
    duration = workout['durationSeconds']
    achieved = rounds >= goal['targetRounds'] and duration <= goal['durationSeconds']
    return {'available': True, 'goal': goal, 'workout': workout, 'goalAchieved': achieved,
            'roundsRemaining': max(0, goal['targetRounds'] - rounds),
            'durationDifferenceSeconds': duration - goal['durationSeconds']}


def recommend(progress: dict) -> dict:
    workout = progress['workout']
    goal = progress['goal']
    if progress['goalAchieved']:
        training = 'Goal met. Repeat this effort with good form before considering a higher target.'
    elif workout['durationSeconds'] > goal['durationSeconds']:
        training = 'Keep the current round target and work toward the time goal gradually; do not rush repetitions.'
    else:
        training = 'Build toward the goal gradually. Aim for no more than one additional complete round in a future session.'
    return {'source': 'rule-based', 'goal': goal, 'sourceWorkoutId': workout['workoutId'],
            'roundsCompleted': workout['roundsCompleted'], 'durationSeconds': workout['durationSeconds'],
            'reps': workout['reps'], 'goalAchieved': progress['goalAchieved'],
            'recommendation': training,
            'recoveryRecommendation': 'Allow recovery between hard sessions. If soreness or fatigue persists, rest or choose easy movement.'}
