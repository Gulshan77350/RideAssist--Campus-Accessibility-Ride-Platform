"""Client for the notification service (internal REST + X-API-Key, X-Request-ID propagated)."""

import asyncio
import logging
import uuid

import httpx

from rideassist_common.config import get_settings
from rideassist_common.observability import log_extra, request_id_ctx

log = logging.getLogger(__name__)


class NotificationError(Exception):
    pass


class NotificationClient:
    def __init__(self, base_url: str, api_key: str, retries: int = 2) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.retries = retries

    async def _post(self, path: str, payload: dict) -> None:
        headers = {"X-API-Key": self.api_key, "X-Request-ID": request_id_ctx.get()}
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(
                        f"{self.base_url}{path}", json=payload, headers=headers
                    )
                if resp.status_code < 500:
                    resp.raise_for_status()
                    return
                last_exc = NotificationError(f"HTTP {resp.status_code}")
            except httpx.HTTPError as exc:
                last_exc = exc
            await asyncio.sleep(0.2 * 2**attempt)  # exponential backoff between retries
        raise NotificationError(str(last_exc))

    async def send_email(self, to: str, subject: str, body: str) -> None:
        await self._post("/internal/email", {"to": to, "subject": subject, "body": body})

    async def push(
        self, user_id: uuid.UUID, title: str, body: str, data: dict | None = None
    ) -> None:
        """Best-effort: a failed push must never fail the ride transition that triggered it."""
        try:
            await self._post(
                "/internal/push",
                {"user_id": str(user_id), "title": title, "body": body, "data": data or {}},
            )
        except NotificationError as exc:
            log.warning("push failed", extra=log_extra(user_id=str(user_id), error=str(exc)))


def get_notifier() -> NotificationClient:
    s = get_settings()
    return NotificationClient(s.notification_service_url, s.internal_api_key)
