"""
High-level email service for sending verification and password reset emails.

Renders Jinja2 HTML templates and delegates delivery to the configured EmailProvider.
"""

import logging
from urllib.parse import urlparse
from pathlib import Path

import aiohttp
from jinja2 import Environment, FileSystemLoader

from db.config import settings
from utils.email.provider import EmailProvider, InlineImage, get_email_provider

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

    @staticmethod
    def _is_http_url(value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    async def _get_inline_logo(self, logo_url: str) -> tuple[str, list[InlineImage]]:
        """Return a cid logo source when remote logo download succeeds."""
        if not self._is_http_url(logo_url):
            return logo_url, []

        timeout = aiohttp.ClientTimeout(total=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(logo_url, allow_redirects=True) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "").split(";", maxsplit=1)[0].strip()
                    if not content_type.startswith("image/"):
                        logger.warning("Skipping inline logo; non-image content type: %s", content_type or "unknown")
                        return logo_url, []

                    mime_subtype = content_type.split("/", maxsplit=1)[1]
                    logo_bytes = await response.read()
        except Exception:
            logger.exception("Failed to fetch logo for inline email embedding: %s", logo_url)
            return logo_url, []

        inline_image = InlineImage(
            content_id="mf-logo",
            content=logo_bytes,
            mime_subtype=mime_subtype,
            filename=f"logo.{mime_subtype.split('+', maxsplit=1)[0]}",
        )
        return "cid:mf-logo", [inline_image]

    async def send_verification_email(self, to: str, token: str, username: str | None = None) -> None:
        """Send an email-verification link to the user."""
        verify_url = f"{settings.host_url}/app/verify-email?token={token}"
        common_context = self._common_context()
        common_context["logo_url"], inline_images = await self._get_inline_logo(common_context["logo_url"])
        html = _jinja_env.get_template("verify_email.html").render(
            **common_context,
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
            inline_images=inline_images,
        )

    async def send_password_reset_email(self, to: str, token: str, username: str | None = None) -> None:
        """Send a password-reset link to the user."""
        reset_url = f"{settings.host_url}/app/reset-password?token={token}"
        common_context = self._common_context()
        common_context["logo_url"], inline_images = await self._get_inline_logo(common_context["logo_url"])
        html = _jinja_env.get_template("reset_password.html").render(
            **common_context,
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
            inline_images=inline_images,
        )

    async def send_upload_warning_email(self, to: str, reason: str, username: str | None = None) -> None:
        """Send an upload policy warning email to a user."""
        greeting_name = username or "there"
        common_context = self._common_context()
        common_context["logo_url"], inline_images = await self._get_inline_logo(common_context["logo_url"])
        html = (
            f"<p>Hi {greeting_name},</p>"
            f"<p>We detected upload activity on your {settings.addon_name} account that triggered our moderation safeguards.</p>"
            f"<p><strong>Reason:</strong> {reason}</p>"
            "<p>If this was unintentional, please slow down and retry later. "
            "Continued abuse may lead to account restrictions.</p>"
            f"<p>Thanks,<br>{settings.addon_name} Team</p>"
        )
        text = (
            f"Hi {greeting_name},\n\n"
            f"We detected upload activity on your {settings.addon_name} account that triggered our moderation safeguards.\n"
            f"Reason: {reason}\n\n"
            "If this was unintentional, please slow down and retry later. "
            "Continued abuse may lead to account restrictions.\n\n"
            f"Thanks,\n{settings.addon_name} Team"
        )
        await self.provider.send(
            to=to,
            subject=f"Upload warning - {settings.addon_name}",
            html=html,
            text=text,
            inline_images=inline_images,
        )


def get_email_service() -> EmailService | None:
    """Return an EmailService instance if email is configured, else None."""
    provider = get_email_provider()
    if provider is None:
        return None
    return EmailService(provider)
