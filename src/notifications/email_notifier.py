"""
src/notifications/email_notifier.py
====================================
Email notification module for the Automated Book Generation System.

Uses Python's built-in `smtplib` with STARTTLS encryption to send plain-text
notification emails. After each successful send the event is logged to the
`notification_log` table via SupabaseClient.

No business logic lives here — this module is a pure delivery mechanism.
"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from src.config import Config
from src.database.supabase_client import SupabaseClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Notification message templates
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, str] = {
    "outline_ready": "Outline for '{title}' is ready for your review.",
    "outline_regenerated": "Outline v{version} for '{title}' has been regenerated.",
    "chapter_ready": "Chapter {chapter_num} of '{title}' needs your review.",
    "awaiting_chapter_notes": "System paused — notes required for Chapter {chapter_num} of '{title}'.",
    "final_compiled": "Book '{title}' has been compiled and is ready!",
    "system_paused": "System paused for '{title}' at stage: {stage}.",
    "error": "Error in '{title}' at stage {stage}: {message}",
}


class EmailNotifier:
    """
    Sends HTML / plain-text notification emails via SMTP.

    All configuration is loaded from Config (SMTP host, port, credentials).
    """

    def __init__(self, config: Config, db: SupabaseClient) -> None:
        """
        Initialise the email notifier.

        Args:
            config: Application configuration with SMTP credentials.
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
        Send an email notification for the given event type.

        Looks up the message template by `event_type`, formats it with
        `kwargs`, and delivers it. On failure, logs the error but does NOT
        raise — notification failure must never crash the pipeline.

        Args:
            event_type: Key into TEMPLATES (e.g. 'outline_ready').
            book_id: Optional book UUID for logging purposes.
            **kwargs: Template variables (e.g. title='My Book', version=2).
        """
        template = TEMPLATES.get(event_type)
        if not template:
            logger.warning("EmailNotifier: unknown event_type='%s'", event_type)
            return

        try:
            body = template.format(**kwargs)
        except KeyError as exc:
            logger.error("EmailNotifier: missing template variable %s for event=%s", exc, event_type)
            return

        subject = f"[Book Gen System] {event_type.replace('_', ' ').title()}"

        try:
            self._send(subject=subject, body=body)
            self._db.log_notification(
                event_type=event_type,
                channel="email",
                payload={"subject": subject, "body": body, **kwargs},
                book_id=book_id,
            )
            logger.info("Email sent — event=%s to=%s", event_type, self._config.notification_to)
        except Exception as exc:
            logger.error("EmailNotifier: failed to send email event=%s: %s", event_type, exc)
            # Non-fatal: pipeline continues even if email fails

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _send(self, subject: str, body: str) -> None:
        """
        Compose and transmit an email via SMTP with STARTTLS.

        Args:
            subject: Email subject line.
            body: Plain-text email body.

        Raises:
            smtplib.SMTPException: If the SMTP session fails.
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._config.notification_from
        msg["To"] = self._config.notification_to

        # Plain-text part
        msg.attach(MIMEText(body, "plain"))

        # Simple HTML part for readability in modern clients
        html_body = f"""<html><body>
<p style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">{body}</p>
<hr style="border: none; border-top: 1px solid #eee;" />
<p style="font-family: Arial, sans-serif; font-size: 11px; color: #999;">
  Automated Book Generation System
</p>
</body></html>"""
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(self._config.smtp_host, self._config.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(self._config.smtp_username, self._config.smtp_password)
            server.sendmail(
                self._config.notification_from,
                [self._config.notification_to],
                msg.as_string(),
            )
