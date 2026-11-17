"""Restricted, token-free persistence for verified MEC callback bindings."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MecBindingStateError(RuntimeError):
    """Raised when callback binding state cannot be trusted or persisted."""


@dataclass(frozen=True)
class PersistedMecBinding:
    subscription_id: str | None
    token_digest: str
    callback_reference_digest: str
    expires_at: float | None = None
    status: str = 'active'


class MecBindingStateStore:
    """Atomically stores HMAC digests only; callback tokens never enter the file."""

    VERSION = 1

    def __init__(self, path: str | os.PathLike[str], key: str | bytes):
        self.path = Path(path).expanduser()
        self.lock_path = Path(str(self.path) + '.lock')
        self.key = key.encode('utf-8') if isinstance(key, str) else bytes(key)
        if len(self.key) < 32:
            raise MecBindingStateError('MEC callback binding key must be at least 32 bytes.')

    def digest(self, value: str) -> str:
        return hmac.new(self.key, value.encode('utf-8'), hashlib.sha256).hexdigest()

    def _validate(self, payload: Any) -> list[PersistedMecBinding]:
        if not isinstance(payload, dict) or payload.get('version') != self.VERSION:
            raise MecBindingStateError('MEC callback binding state version is invalid.')
        records = payload.get('bindings')
        if not isinstance(records, list):
            raise MecBindingStateError('MEC callback binding state is malformed.')
        result: list[PersistedMecBinding] = []
        for item in records:
            if not isinstance(item, dict):
                raise MecBindingStateError('MEC callback binding record is malformed.')
            subscription_id = item.get('subscription_id')
            token_digest = item.get('token_digest')
            callback_digest = item.get('callback_reference_digest')
            expires_at = item.get('expires_at')
            if (subscription_id is not None and (not isinstance(subscription_id, str) or not subscription_id) or
                    not isinstance(token_digest, str) or len(token_digest) != 64 or
                    not isinstance(callback_digest, str) or len(callback_digest) != 64 or
                    (expires_at is not None and not isinstance(expires_at, (int, float))) or
                    item.get('status', 'active') not in ('active', 'pending')):
                raise MecBindingStateError('MEC callback binding record is invalid.')
            result.append(PersistedMecBinding(subscription_id, token_digest,
                                              callback_digest,
                                              float(expires_at) if expires_at is not None else None,
                                              item.get('status', 'active')))
        return result

    @contextmanager
    def _lock(self):
        """Hold an OS advisory lock for the complete state transaction."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open('a+b') as stream:
            stream.seek(0)
            stream.write(b'0')
            stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == 'nt':
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _load_unlocked(self) -> list[PersistedMecBinding]:
        if not self.path.exists():
            return []
        try:
            with self.path.open('r', encoding='utf-8') as stream:
                return self._validate(json.load(stream))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise MecBindingStateError('MEC callback binding state cannot be read safely.') from exc

    def _save_unlocked(self, bindings: list[PersistedMecBinding]) -> None:
        payload = {'version': self.VERSION, 'bindings': [
            {'subscription_id': item.subscription_id,
             'token_digest': item.token_digest,
             'callback_reference_digest': item.callback_reference_digest,
             'expires_at': item.expires_at,
             'status': item.status}
            for item in bindings
        ]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8',
                                             dir=self.path.parent, delete=False,
                                             prefix=f'.{self.path.name}.', suffix='.tmp') as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise MecBindingStateError('MEC callback binding state could not be written atomically.') from exc

    @contextmanager
    def transaction(self):
        """Serialize the complete read-modify-write operation across processes."""
        with self._lock():
            records = self._load_unlocked()
            yield records
            self._save_unlocked(records)

    def load(self) -> list[PersistedMecBinding]:
        with self._lock():
            return self._load_unlocked()

    def save(self, bindings: list[PersistedMecBinding]) -> None:
        with self._lock():
            self._save_unlocked(bindings)

    def upsert(self, subscription_id: str, callback_token: str,
               callback_reference: str, expires_at: float | None) -> None:
        token_digest = self.digest(callback_token)
        with self.transaction() as records:
            records[:] = [item for item in records
                          if item.subscription_id != subscription_id and item.token_digest != token_digest]
            records.append(PersistedMecBinding(
                subscription_id, token_digest, self.digest(callback_reference),
                expires_at, 'active'))

    def upsert_pending(self, callback_token: str, callback_reference: str,
                       expires_at: float) -> None:
        token_digest = self.digest(callback_token)
        with self.transaction() as records:
            records[:] = [item for item in records if item.token_digest != token_digest]
            records.append(PersistedMecBinding(
                None, token_digest, self.digest(callback_reference), expires_at, 'pending'))

    def remove(self, subscription_id: str) -> None:
        with self.transaction() as records:
            records[:] = [item for item in records if item.subscription_id != subscription_id]

    def remove_token(self, callback_token: str) -> None:
        digest = self.digest(callback_token)
        with self.transaction() as records:
            records[:] = [item for item in records if item.token_digest != digest]

    def remove_pending_digests(self, digests: set[str]) -> None:
        with self.transaction() as records:
            records[:] = [item for item in records
                          if not (item.status == 'pending' and item.token_digest in digests)]

    def matches(self, record: PersistedMecBinding, callback_token: str,
                callback_reference: str) -> bool:
        return (hmac.compare_digest(record.token_digest, self.digest(callback_token)) and
                hmac.compare_digest(record.callback_reference_digest,
                                    self.digest(callback_reference)))
