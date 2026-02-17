"""
High-level email service for sending verification and password reset emails.

Renders Jinja2 HTML templates and delegates delivery to the configured EmailProvider.
"""

import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from db.config import settings
from utils.email.provider import EmailProvider, get_email_provider

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=True,
)


class EmailService:
    """Orchestrates rendering and sending of transactional emails."""

    def __init__(self, provider: EmailProvider):
        self.provider = provider

    def _common_context(self) -> dict:
        """Return template variables shared by all email templates."""
        return {
            "app_name": settings.addon_name,
            "logo_url": settings.logo_url,
            "branding_svg": settings.branding_svg,
        }

    async def send_verification_email(self, to: str, token: str, username: str | None = None) -> None:
        """Send an email-verification link to the user."""
        verify_url = f"{settings.host_url}/app/verify-email?token={token}"
        html = _jinja_env.get_template("verify_email.html").render(
            **self._common_context(),
            verify_url=verify_url,
            username=username,
            expires_in="24 hours",
        )
        text = (
            f"Verify your email for {settings.addon_name}\n\n"
            f"Click the link below to verify your email address:\n{verify_url}\n\n"
            f"This link expires in 24 hours."
        )
        await self.provider.send(
            to=to,
            subject=f"Verify your email - {settings.addon_name}",
            html=html,
            text=text,
        )

    async def send_password_reset_email(self, to: str, token: str, username: str | None = None) -> None:
        """Send a password-reset link to the user."""
        reset_url = f"{settings.host_url}/app/reset-password?token={token}"
        html = _jinja_env.get_template("reset_password.html").render(
            **self._common_context(),
            reset_url=reset_url,
            username=username,
            expires_in="1 hour",
        )
        text = (
            f"Reset your password for {settings.addon_name}\n\n"
            f"Click the link below to reset your password:\n{reset_url}\n\n"
            f"This link expires in 1 hour."
        )
        await self.provider.send(
            to=to,
            subject=f"Reset your password - {settings.addon_name}",
            html=html,
            text=text,
        )


def get_email_service() -> EmailService | None:
    """Return an EmailService instance if email is configured, else None."""
    provider = get_email_provider()
    if provider is None:
        return None
    return EmailService(provider)
