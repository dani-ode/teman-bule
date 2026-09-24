"""Durable SMTP mailer (FND-06). TLS mode eksplisit; token/link tidak pernah di-log."""

from __future__ import annotations

import logging
from email.message import EmailMessage
from typing import Any

import aiosmtplib

from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings

logger = logging.getLogger(__name__)


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, *, to_address: str, subject: str, body: str) -> None:
        s = self._settings
        message = EmailMessage()
        message["From"] = f"{s.mail_from_name} <{s.mail_from_address}>"
        message["To"] = to_address
        message["Subject"] = subject
        message.set_content(body)

        kwargs: dict[str, Any] = {
            "hostname": s.mail_host,
            "port": s.mail_port,
        }
        if s.mail_encryption == "implicit_tls":
            kwargs["use_tls"] = True
        elif s.mail_encryption == "starttls":
            kwargs["start_tls"] = True
        # "plain" tidak memakai TLS; hanya untuk development terkontrol.

        if s.mail_username:
            kwargs["username"] = s.mail_username
            kwargs["password"] = s.mail_password

        try:
            await aiosmtplib.send(message, **kwargs)
        except Exception as exc:  # jangan sertakan detail kredensial
            logger.error("smtp_send_failed", extra={"error_code": type(exc).__name__})
            raise DependencyUnavailableError(
                "Layanan email tidak tersedia.", code="SMTP_UNAVAILABLE"
            ) from exc
