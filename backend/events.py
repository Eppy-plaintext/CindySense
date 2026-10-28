"""Versioned, absolute-count repetition events stored as oneM2M content."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class RepetitionEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    event_id: UUID = Field(alias='eventId')
    session_id: str = Field(alias='sessionId', min_length=1, max_length=100, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
    exercise: Literal['pullup', 'pushup', 'squat']
    repetitions: int = Field(ge=0, strict=True)
    round: int = Field(ge=1, strict=True)
    timestamp: AwareDatetime
    source: Literal['simulator', 'mpu6050', 'manual']

    @field_validator('timestamp', mode='before')
    @classmethod
    def timestamp_must_be_iso8601_string(cls, value):
        if not isinstance(value, str) or 'T' not in value:
            raise ValueError('timestamp must be an ISO 8601 string with a timezone')
        return value

    def content(self) -> dict:
        return self.model_dump(mode='json', by_alias=True)

    def same_as(self, other: 'RepetitionEvent') -> bool:
        # datetime values are compared as instants, including across UTC offsets.
        return self.model_dump() == other.model_dump()

    @property
    def resource_name(self) -> str:
        return f'sim-{self.event_id.hex}'


def classify_repetition_content(content: object) -> tuple[str, RepetitionEvent | None, int | float | None]:
    if isinstance(content, bool):
        raise ValueError('Boolean content is not a repetition count')
    if isinstance(content, (int, float)):
        if not isinstance(content, int) and (content != content or content in (float('inf'), float('-inf'))):
            raise ValueError('Non-finite legacy content is invalid')
        return 'legacy_unclassified', None, content
    if not isinstance(content, dict):
        raise ValueError('Repetition content must be numeric legacy data or a structured event')
    event = RepetitionEvent.model_validate(content)
    return 'structured_event', event, None


def deduplicate_and_order(events: list[RepetitionEvent]) -> list[RepetitionEvent]:
    """Deterministic event order for a future complete-session event collection."""
    unique: dict[UUID, RepetitionEvent] = {}
    for event in events:
        previous = unique.get(event.event_id)
        if previous is not None and not previous.same_as(event):
            raise ValueError('eventId is associated with different content')
        unique[event.event_id] = event
    return sorted(unique.values(), key=lambda event: (event.timestamp, str(event.event_id)))
