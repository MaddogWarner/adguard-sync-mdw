from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.config import NotificationsConfig
from app.storage import utc_now

logger = logging.getLogger(__name__)

_PRIORITY = {"problem": "high", "recovered": "default"}
_TAGS = {"problem": "warning", "recovered": "white_check_mark"}


@dataclass(frozen=True)
class Alert:
    event: str  # host_down | host_up | sync_problem | sync_recovered
    subject: str  # host or follower name
    message: str
    severity: str  # problem | recovered


class Notifier:
    """Posts state-change alerts to a webhook. Never raises: alerting must not break syncs."""

    def __init__(self, config: NotificationsConfig, *, timeout: float = 10.0) -> None:
        self.config = config
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return self.config.webhook_url is not None

    async def send(self, alert: Alert) -> None:
        if self.config.webhook_url is None:
            return
        url = self.config.webhook_url.get_secret_value()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if self.config.format == "ntfy":
                    response = await client.post(
                        url,
                        content=alert.message.encode(),
                        headers={
                            "Title": f"AdGuard Sync: {alert.subject}",
                            "Priority": _PRIORITY[alert.severity],
                            "Tags": _TAGS[alert.severity],
                        },
                    )
                else:
                    text = f"AdGuard Sync: {alert.message}"
                    response = await client.post(
                        url,
                        json={
                            "event": alert.event,
                            "subject": alert.subject,
                            "severity": alert.severity,
                            "message": alert.message,
                            "timestamp": utc_now(),
                            # Slack-style and Discord-style fields so common webhooks just work.
                            "text": text,
                            "content": text,
                        },
                    )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Log the host only: webhook URLs often embed a secret token.
            logger.warning(
                "alert_delivery_failed",
                extra={"host": urlparse(url).hostname, "error": type(exc).__name__},
            )
