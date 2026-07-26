"""
Email channel - the MVP implementation, per the Phase 2 workflow design's
own note that a completion email is the expected MVP notification.

Uses only the standard library (smtplib/email), no new dependency. Not
exercised by the automated test suite - a unit test suite should never
depend on a live SMTP server. Tests use FakeNotificationChannel
(dependencies.py) instead. This class exists so the architecture has a
real implementation ready to configure, same reasoning as AI Insights'
AnthropicAIProvider.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


class SMTPEmailChannel:
    channel_name = "email"

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        from_address: str | None = None,
        use_tls: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self._host = host or os.environ.get("SMTP_HOST")
        self._port = int(port or os.environ.get("SMTP_PORT", 587))
        self._username = username or os.environ.get("SMTP_USERNAME")
        self._password = password or os.environ.get("SMTP_PASSWORD")
        self._from_address = from_address or os.environ.get("SMTP_FROM_ADDRESS")
        if not self._host or not self._from_address:
            raise RuntimeError(
                "SMTP_HOST and SMTP_FROM_ADDRESS must be configured - set the "
                "environment variables or pass them explicitly."
            )
        self._use_tls = use_tls
        self._timeout = timeout

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._from_address
        message["To"] = recipient
        message.set_content(body)

        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
            if self._use_tls:
                server.starttls()
            if self._username and self._password:
                server.login(self._username, self._password)
            server.send_message(message)
