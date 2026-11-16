"""Read-only subscription health checks and conservative oneM2M repair.

The monitor deliberately does not update or delete an existing subscription.
It may create a subscription only when the parent is a verified container and
neither the preferred nor legacy-compatible subscription exists.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .tinyiot import TinyIoTError

log = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sanitize_notification_url(value: str | None) -> str | None:
    """Return URL metadata without exposing the callback token."""
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        path = parsed.path.split("/")
        if path:
            path[-1] = "<redacted>"
        return f"{parsed.scheme}://{parsed.netloc}{'/'.join(path)}"
    except Exception:
        return "<redacted>"


class SubscriptionMonitor:
    def __init__(self, enabled=True, interval_seconds=60.0, recovery_enabled=True,
                 max_backoff_seconds=300.0, on_verified=None):
        self.enabled = bool(enabled)
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.recovery_enabled = bool(recovery_enabled)
        self.max_backoff_seconds = max(self.interval_seconds, float(max_backoff_seconds))
        self.on_verified = on_verified
        self._recovery_lock = asyncio.Lock()
        self._task = None
        self._stop = asyncio.Event()
        self._state = {
            "enabled": self.enabled,
            "recoveryEnabled": self.recovery_enabled,
            "lastCheckAt": None,
            "lastSuccessfulVerificationAt": None,
            "lastError": None,
            "verificationStatus": "not_checked",
            "required": {},
            "missingSubscriptionCount": 0,
            "conflictCount": 0,
            "recoveryAttempts": 0,
            "recoverySuccesses": 0,
            "retryAttempt": 0,
            "backoffSeconds": 0,
            "nextRetryAt": None,
        }

    @staticmethod
    def _specs(settings):
        return (
            ("repetition", settings.repetition_resource,
             "cindysense-fastapi-repetitions", "RepCount_Sub"),
            ("summary", settings.workout_summary_resource,
             "cindysense-fastapi-summaries", "Summary_Sub"),
        )

    @staticmethod
    def _validate_config(settings):
        url = getattr(settings, "notification_url", "")
        token = getattr(settings, "notification_token", "")
        origin = getattr(settings, "subscription_origin", "")
        if not url:
            raise ValueError("notification URL is not configured")
        parsed = urlsplit(url)
        if (len(token) < 32 or parsed.scheme not in ("http", "https") or
                not parsed.hostname or parsed.hostname in ("localhost", "127.0.0.1", "::1") or
                parsed.query or parsed.fragment or
                not url.endswith("/api/onem2m/notify/" + token)):
            raise ValueError("notification URL or token configuration is invalid")
        if not origin:
            raise ValueError("subscription origin is not configured")
        return url, origin

    @staticmethod
    def _safe_error(exc):
        if isinstance(exc, TinyIoTError):
            return exc.code
        text = str(exc).lower()
        for marker in ("unreachable", "timeout", "not configured", "invalid", "container"):
            if marker in text:
                return marker
        return "subscription_monitor_error"

    async def _read_subscriptions(self, client, resource, origin, names):
        parent = await client.retrieve(resource, origin)
        if "m2m:cnt" not in parent:
            raise TinyIoTError("invalid_resource", f"{resource} is not a container.", 502)
        subscriptions = await client.embedded_subscriptions(resource, origin)
        known = {sub.get("rn") for sub in subscriptions if isinstance(sub, dict)}
        for name in names:
            if name in known:
                continue
            try:
                payload = await client.retrieve(f"{resource}/{name}", origin,
                                                expected_not_found=True)
            except TinyIoTError as exc:
                if exc.code == "resource_not_found":
                    continue
                raise
            sub = payload.get("m2m:sub") if isinstance(payload, dict) else None
            if isinstance(sub, dict) and sub.get("rn") == name:
                subscriptions.append(sub)
        return subscriptions

    async def audit_once(self, client, settings):
        """Verify subscriptions without changing TinyIoT."""
        url, origin = self._validate_config(settings)
        required = {}
        for kind, resource, preferred, legacy in self._specs(settings):
            subscriptions = await self._read_subscriptions(
                client, resource, origin, (preferred, legacy))
            named = [sub for sub in subscriptions
                     if sub.get("rn") in (preferred, legacy)]
            healthy = [sub for sub in subscriptions
                       if url in (sub.get("nu") or []) and
                       3 in ((sub.get("enc") or {}).get("net") or [])]
            conflicts = [sub for sub in named if sub not in healthy]
            if healthy:
                status = "healthy"
            elif conflicts:
                status = "conflict"
            else:
                status = "missing"
            required[kind] = {
                "status": status,
                "resource": resource,
                "preferredName": preferred,
                "legacyName": legacy,
                "subscriptions": [f"{resource}/{sub.get('rn')}" for sub in healthy
                                   if isinstance(sub.get("rn"), str)],
                "subscriptionIds": [sub.get("ri") for sub in healthy
                                    if isinstance(sub.get("ri"), str)],
                "conflictCount": len(conflicts),
                "equivalentCount": len(healthy),
                "notification": sanitize_notification_url(url),
            }
        return required

    async def _recover_missing(self, client, settings, required):
        missing = [kind for kind, info in required.items() if info["status"] == "missing"]
        if not missing or not self.recovery_enabled:
            return False
        async with self._recovery_lock:
            # A concurrent check may have repaired the subscription while this
            # task waited for the lock. Re-read before every write.
            current = await self.audit_once(client, settings)
            missing = [kind for kind, info in current.items() if info["status"] == "missing"]
            if not missing:
                return False
            self._state["recoveryAttempts"] += len(missing)
            url, origin = self._validate_config(settings)
            for kind, resource, preferred, legacy in self._specs(settings):
                if kind not in missing:
                    continue
                # Parent and children were checked by audit_once immediately
                # before creation. Only create the preferred name when no
                # compatible or conflicting named subscription exists.
                await client.create_subscription(resource, origin, preferred, url)
                readback = await self._read_subscriptions(
                    client, resource, origin, (preferred, legacy))
                confirmed = next((sub for sub in readback
                                  if sub.get("rn") == preferred and
                                  url in (sub.get("nu") or []) and
                                  3 in ((sub.get("enc") or {}).get("net") or [])), None)
                if confirmed is None:
                    raise TinyIoTError("subscription_unconfirmed",
                                      f"{resource}/{preferred} could not be verified.", 502)
                self._state["recoverySuccesses"] += 1
            return True

    async def check_once(self, client, settings, recover=None):
        if not self.enabled:
            self._state["lastCheckAt"] = _now()
            return self.health()
        self._state["lastCheckAt"] = _now()
        try:
            required = await self.audit_once(client, settings)
            if recover is None:
                recover = self.recovery_enabled
            if recover:
                await self._recover_missing(client, settings, required)
                if any(info["status"] == "missing" for info in required.values()):
                    required = await self.audit_once(client, settings)
            self._state["required"] = required
            if self.on_verified is not None:
                self.on_verified(required)
            self._state["missingSubscriptionCount"] = sum(
                info["status"] == "missing" for info in required.values())
            self._state["conflictCount"] = sum(info["conflictCount"] for info in required.values())
            self._state["lastSuccessfulVerificationAt"] = _now()
            self._state["lastError"] = None
            self._state["verificationStatus"] = "verified"
            self._state["retryAttempt"] = 0
            self._state["backoffSeconds"] = 0
            self._state["nextRetryAt"] = None
        except (TinyIoTError, ValueError, TypeError) as exc:
            self._state["lastError"] = self._safe_error(exc)
            self._state["verificationStatus"] = "unavailable"
            self._state["retryAttempt"] += 1
            self._state["backoffSeconds"] = min(
                self.max_backoff_seconds,
                max(self.interval_seconds, self.interval_seconds * (2 ** (self._state["retryAttempt"] - 1))))
            self._state["nextRetryAt"] = (
                datetime.now(timezone.utc) +
                timedelta(seconds=self._state["backoffSeconds"])
            ).isoformat(timespec="milliseconds")
        return self.health()

    def health(self):
        return dict(self._state, required={key: dict(value) for key, value in self._state["required"].items()})

    async def run(self, client, settings):
        """Run until cancelled; cancellation is the normal shutdown path."""
        self._stop.clear()
        try:
            while not self._stop.is_set():
                await self.check_once(client, settings)
                delay = self._state["backoffSeconds"] or self.interval_seconds
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    async def start(self, client, settings):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(client, settings))
        return self._task

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
