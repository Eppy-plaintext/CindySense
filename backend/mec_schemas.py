"""Validated, transport-neutral schemas for MEC 028 subscriptions and callbacks."""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MecSchemaError(ValueError):
    """A sanitized schema validation failure."""


class ApIdentifier(BaseModel):
    model_config = ConfigDict(extra='ignore')
    bssid: str = Field(min_length=12, max_length=12)

    @field_validator('bssid')
    @classmethod
    def valid_bssid(cls, value: str) -> str:
        if not re.fullmatch(r'[0-9A-Fa-f]{12}', value):
            raise ValueError('apId.bssid must contain exactly 12 hexadecimal characters')
        return value.upper()


class MecAssocStaSubscription(BaseModel):
    model_config = ConfigDict(extra='ignore', populate_by_name=True)
    subscription_type: Literal['AssocStaSubscription'] = Field(alias='subscriptionType')
    callback_reference: str = Field(alias='callbackReference', min_length=1)
    ap_id: ApIdentifier = Field(alias='apId')
    expiry_deadline: dict[str, Any] | None = Field(default=None, alias='expiryDeadline')
    notification_event: dict[str, Any] | None = Field(default=None, alias='notificationEvent')
    notification_period: int | None = Field(default=None, alias='notificationPeriod', ge=0)
    request_test_notification: bool | None = Field(default=None, alias='requestTestNotification')

    @field_validator('callback_reference')
    @classmethod
    def callback_is_https(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('callbackReference must be a credential-free HTTPS URL')
        if parsed.query or parsed.fragment:
            raise ValueError('callbackReference cannot contain query or fragment')
        return value


class MecAssocStaNotification(BaseModel):
    model_config = ConfigDict(extra='ignore', populate_by_name=True)
    notification_type: Literal['AssocStaNotification'] = Field(alias='notificationType')
    ap_id: ApIdentifier = Field(alias='apId')
    sta_id: list[dict[str, Any]] | dict[str, Any] | str | None = Field(default=None, alias='staId')
    time_stamp: str | None = Field(default=None, alias='timeStamp')


class MecTestNotification(BaseModel):
    model_config = ConfigDict(extra='ignore', populate_by_name=True)
    links: dict[str, Any] = Field(alias='_links')
    notification_type: Literal['TestNotification'] = Field(alias='notificationType')


def parse_mec_notification(payload: Any) -> MecAssocStaNotification | MecTestNotification:
    if not isinstance(payload, dict):
        raise MecSchemaError('MEC notification must be a JSON object')
    event_type = payload.get('notificationType')
    if event_type == 'AssocStaNotification':
        return MecAssocStaNotification.model_validate(payload)
    if event_type == 'TestNotification':
        return MecTestNotification.model_validate(payload)
    raise MecSchemaError('Unsupported MEC notification type')
