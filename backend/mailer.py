"""Sending mail, behind a single switch.

There was no mail path in this codebase at all -- only an aspirational note in worker.py
about "a failing SMTP server". Client reports have to reach a client, so this adds one.

stdlib `smtplib` rather than a provider SDK: it is one protocol, every provider speaks it
(Gmail, SES, Postmark, Resend all expose SMTP), and it adds nothing to requirements.txt.

Configuration is by environment, and absent configuration is reported rather than hidden.
A report that silently "sent" to nobody is worse than one that refuses -- the tenant tells
their client it is on the way and it never arrives.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class MailNotConfigured(RuntimeError):
    """Raised instead of silently discarding a message nobody could have received."""


def is_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip() and sender())


def sender() -> str:
    """The From address, falling back to the login when only that is set."""
    return (os.getenv("SMTP_FROM", "") or os.getenv("SMTP_USER", "")).strip()


def missing_variables() -> list[str]:
    """The settings still needed before mail can be sent, as a list."""
    missing = [name for name in ("SMTP_HOST",) if not os.getenv(name, "").strip()]
    if not sender():
        missing.append("SMTP_FROM (or SMTP_USER)")
    return missing


def _describe_missing() -> str:
    return ", ".join(missing_variables())


def send(
    to: str,
    subject: str,
    body: str,
    attachment: bytes | None = None,
    attachment_name: str = "report.pdf",
    attachment_type: tuple[str, str] = ("application", "pdf"),
) -> None:
    """Deliver one message. Raises rather than returning False, so a caller cannot ignore it."""
    if not is_configured():
        raise MailNotConfigured(
            f"Email is not configured on this server (missing: {_describe_missing()})"
        )
    recipient = (to or "").strip()
    if not recipient:
        raise ValueError("No recipient address")

    message = EmailMessage()
    message["From"] = sender()
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    if attachment is not None:
        message.add_attachment(
            attachment,
            maintype=attachment_type[0],
            subtype=attachment_type[1],
            filename=attachment_name,
        )

    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587") or 587)
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")

    # 465 is implicit TLS and must not be STARTTLS'd; everything else is plain-then-upgrade.
    # Getting this backwards is the usual cause of a hang rather than an error, so it is
    # chosen from the port rather than left to a separate flag someone has to set right.
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
            if user:
                server.login(user, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls(context=context)
            if user:
                server.login(user, password)
            server.send_message(message)

    logger.info("Sent '%s' to %s", subject, recipient)
