"""Email service module for sending verification and password reset emails."""

from utils.email.provider import EmailProvider, SMTPProvider, get_email_provider
from utils.email.service import EmailService, get_email_service

__all__ = [
    "EmailProvider",
    "SMTPProvider",
    "get_email_provider",
    "EmailService",
    "get_email_service",
]
