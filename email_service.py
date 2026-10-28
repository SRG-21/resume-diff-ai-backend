"""
Transactional email (verification, password reset).

EMAIL_BACKEND=console logs the message (local development and tests);
EMAIL_BACKEND=ses sends through Amazon SES using the Lambda role's credentials.
"""
import asyncio
import logging
from html import escape

from config import settings

logger = logging.getLogger(__name__)


def _send_ses(to: str, subject: str, text: str, html: str) -> None:
    import boto3

    client = boto3.client("ses", region_name=settings_region())
    client.send_email(
        Source=settings.EMAIL_FROM,
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": text, "Charset": "UTF-8"}, "Html": {"Data": html, "Charset": "UTF-8"}},
        },
    )


def settings_region() -> str:
    import os
    return os.environ.get("AWS_REGION", "us-east-1")


async def send_email(to: str, subject: str, text: str, html: str) -> None:
    """Send an email. Failures are logged, never raised, so auth flows still respond."""
    if settings.EMAIL_BACKEND == "ses":
        try:
            await asyncio.to_thread(_send_ses, to, subject, text, html)
            logger.info("Sent email via SES")
        except Exception as e:
            logger.error(f"Failed to send email via SES: {e}")
        return
    logger.info(f"[email:console] to={to} subject={subject}\n{text}")


def _layout(title: str, body: str, url: str, button: str) -> str:
    return (
        f"<div style='font-family:sans-serif;max-width:480px;margin:auto'>"
        f"<h2>{escape(title)}</h2><p>{escape(body)}</p>"
        f"<p><a href='{escape(url)}' style='background:#2563eb;color:#fff;padding:12px 20px;"
        f"border-radius:8px;text-decoration:none'>{escape(button)}</a></p>"
        f"<p style='color:#6b7280;font-size:12px'>If the button does not work, copy this link: {escape(url)}</p></div>"
    )


async def send_verification_email(to: str, token: str) -> None:
    url = f"{settings.FRONTEND_URL}/verify-email?token={token}"
    text = f"Confirm your email address for Resume Diff AI:\n{url}\n\nThis link expires in 48 hours."
    await send_email(to, "Confirm your email", text, _layout("Confirm your email", "Click below to confirm your email address.", url, "Confirm email"))


async def send_password_reset_email(to: str, token: str) -> None:
    url = f"{settings.FRONTEND_URL}/reset-password?token={token}"
    text = f"Reset your Resume Diff AI password:\n{url}\n\nThis link expires in 1 hour. If you did not ask for this, ignore this email."
    await send_email(to, "Reset your password", text, _layout("Reset your password", "Click below to choose a new password. The link expires in 1 hour.", url, "Reset password"))
