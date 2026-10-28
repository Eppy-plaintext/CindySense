"""Opt-in development publisher with durable, deterministic event IDs."""

import asyncio

from .events import RepetitionEvent, classify_repetition_content
from .tinyiot import TinyIoTClient, TinyIoTError


class SimulationPublisher:
    def __init__(self):
        self._lock = asyncio.Lock()

    async def publish(self, client: TinyIoTClient, container: str, origin: str, event: RepetitionEvent) -> bool:
        """Return True on creation, False for an exact replay; reject changed payloads."""
        child_path = f'{container.strip("/")}/{event.resource_name}'
        async with self._lock:
            try:
                existing = await client.retrieve(child_path, origin)
            except TinyIoTError as exc:
                if exc.code != 'resource_not_found':
                    raise
            else:
                self._verify_match(client, existing, event)
                return False

            try:
                await client.create_content_instance(container, origin, event.resource_name, event.content())
            except TinyIoTError as exc:
                if exc.code != 'resource_conflict':
                    raise
                # Another process may have created the same deterministic name.
                existing = await client.retrieve(child_path, origin)
                self._verify_match(client, existing, event)
                return False

            # Do not claim success if the server ignored the requested resource name.
            existing = await client.retrieve(child_path, origin)
            self._verify_match(client, existing, event)
            return True

    @staticmethod
    def _verify_match(client: TinyIoTClient, payload: dict, expected: RepetitionEvent) -> None:
        try:
            content, _ = client.parse_content_instance(payload)
            kind, stored, _ = classify_repetition_content(content)
        except TinyIoTError:
            raise
        except Exception as exc:
            raise TinyIoTError('invalid_content', 'Stored event content is malformed.', 502) from exc
        if kind != 'structured_event' or stored is None or not stored.same_as(expected):
            raise TinyIoTError('event_id_conflict', 'Event ID is already associated with different content.', 409)
