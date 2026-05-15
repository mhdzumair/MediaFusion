"""
Email provider abstraction layer.

Defines the EmailProvider ABC and concrete SMTP implementation.
To add new providers (SendGrid, SES, Resend, etc.), subclass EmailProvider.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from db.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InlineImage:
    """Inline image attachment rendered via CID in HTML emails."""

    content_id: str
    content: bytes
    mime_subtype: str
    filename: str


class EmailProvider(ABC):
    """Abstract base class for email sending providers."""

    @abstractmethod
    async def send(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        inline_images: list[InlineImage] | None = None,
    ) -> None:
        """Send an email.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            html: HTML body content.
            text: Plain-text fallback body.
            inline_images: Optional inline images referenced via cid: URLs.

        Raises:
            Exception: If sending fails.
        """
        ...


class SMTPProvider(EmailProvider):
    """SMTP email provider using aiosmtplib."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        from_email: str,
        from_name: str,
        use_tls: bool,
        use_ssl: bool,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_email = from_email
        self.from_name = from_name
        self.use_tls = use_tls
        self.use_ssl = use_ssl

    async def send(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        inline_images: list[InlineImage] | None = None,
    ) -> None:
        message = MIMEMultipart("related") if inline_images else MIMEMultipart("alternative")
        message["From"] = f"{self.from_name} <{self.from_email}>"
        message["To"] = to
        message["Subject"] = subject

        if inline_images:
            alternative = MIMEMultipart("alternative")
            alternative.attach(MIMEText(text, "plain", "utf-8"))
            alternative.attach(MIMEText(html, "html", "utf-8"))
            message.attach(alternative)

            for image in inline_images:
                mime_image = MIMEImage(
                    image.content,
                    _subtype=image.mime_subtype,
                    name=image.filename,
                )
                mime_image.add_header("Content-ID", f"<{image.content_id}>")
                mime_image.add_header("Content-Disposition", "inline", filename=image.filename)
                message.attach(mime_image)
        else:
            message.attach(MIMEText(text, "plain", "utf-8"))
            message.attach(MIMEText(html, "html", "utf-8"))

        try:
            await aiosmtplib.send(
                message,
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                start_tls=self.use_tls,
                use_tls=self.use_ssl,
            )
            logger.info("Email sent to %s: %s", to, subject)
        except Exception:
            logger.exception("Failed to send email to %s: %s", to, subject)
            raise


def get_email_provider() -> EmailProvider | None:
    """Create and return the configured email provider, or None if not configured."""
    if not settings.smtp_host:
        return None

    return SMTPProvider(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        from_email=settings.smtp_from_email or settings.contact_email,
        from_name=settings.smtp_from_name or settings.addon_name,
        use_tls=settings.smtp_use_tls,
        use_ssl=settings.smtp_use_ssl,
    )
