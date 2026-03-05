"""
src/notifications/teams_notifier.py
=====================================
Microsoft Teams notification module for the Automated Book Generation System.

Sends Adaptive Card messages to a Teams channel via an Incoming Webhook URL.
Uses `httpx` for the HTTP POST call. After each successful delivery the event
is logged to the `notification_log` table via SupabaseClient.

No business logic lives here — this is a pure delivery mechanism.
"""

import logging
from typing import Optional

import httpx

from src.config import Config
from src.database.supabase_client import SupabaseClient
from src.notifications.email_notifier import TEMPLATES  # re-use same templates

logger = logging.getLogger(__name__)

# HTTP timeout in seconds for webhook POSTs
_TIMEOUT = 10.0


class TeamsNotifier:
    """
    Delivers notifications to a Microsoft Teams channel via an Incoming Webhook.

    Messages are sent as Adaptive Cards for rich rendering in Teams clients.
    If the webhook URL is not configured, the notifier silently skips sending.
    """

    def __init__(self, config: Config, db: SupabaseClient) -> None:
        """
        Initialise the Teams notifier.

        Args:
            config: Application configuration with the Teams webhook URL.
            db: Supabase client for logging sent notifications.
        """
        self._config = config
        self._db = db

    def notify(
        self,
        event_type: str,
        book_id: Optional[str] = None,
        **kwargs,
    ) -> None:
        """
        Send a Teams notification for the given event type.

        If `TEAMS_WEBHOOK_URL` is empty, this method logs a debug message and
        returns immediately — Teams notifications are optional. On delivery
        failure, the error is logged but NOT raised.

        Args:
            event_type: Key into TEMPLATES (e.g. 'chapter_ready').
            book_id: Optional book UUID for logging.
            **kwargs: Template variables (e.g. title='My Book', chapter_num=3).
        """
        if not self._config.teams_webhook_url:
            logger.debug("TeamsNotifier: webhook URL not configured — skipping")
            return

        template = TEMPLATES.get(event_type)
        if not template:
            logger.warning("TeamsNotifier: unknown event_type='%s'", event_type)
            return

        try:
            body = template.format(**kwargs)
        except KeyError as exc:
            logger.error("TeamsNotifier: missing template variable %s for event=%s", exc, event_type)
            return

        card = self._build_adaptive_card(event_type, body)

        try:
            self._post(card)
            self._db.log_notification(
                event_type=event_type,
                channel="teams",
                payload={"body": body, **kwargs},
                book_id=book_id,
            )
            logger.info("Teams notification sent — event=%s", event_type)
        except Exception as exc:
            logger.error("TeamsNotifier: delivery failure event=%s: %s", event_type, exc)
            # Non-fatal: pipeline continues even if Teams delivery fails

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _build_adaptive_card(self, event_type: str, message: str) -> dict:
        """
        Build a Teams Incoming Webhook payload with an Adaptive Card body.

        Args:
            event_type: Used as the card header/title.
            message: Main notification body text.

        Returns:
            dict: JSON-serialisable payload for the Teams webhook endpoint.
        """
        title = event_type.replace("_", " ").title()
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "size": "Medium",
                                "weight": "Bolder",
                                "text": f"📚 Book Gen: {title}",
                                "wrap": True,
                            },
                            {
                                "type": "TextBlock",
                                "text": message,
                                "wrap": True,
                                "color": "Default",
                            },
                        ],
                    },
                }
            ],
        }

    def _post(self, payload: dict) -> None:
        """
        HTTP POST the payload to the Teams webhook endpoint.

        Args:
            payload: The Adaptive Card payload dict.

        Raises:
            httpx.HTTPStatusError: If the webhook returns a non-2xx status.
            httpx.RequestError: On network-level failures.
        """
        response = httpx.post(
            self._config.teams_webhook_url,
            json=payload,
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        logger.debug("Teams webhook response: %s", response.status_code)
