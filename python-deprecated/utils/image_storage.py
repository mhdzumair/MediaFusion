"""S3-backed storage helpers for uploaded metadata images."""

import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from uuid import uuid4

import aioboto3

from db.config import settings

logger = logging.getLogger(__name__)


class ImageStorage(ABC):
    """Abstract storage contract for uploaded images."""

    @abstractmethod
    async def store_image(self, key: str, content: bytes, content_type: str) -> str:
        """Store image bytes and return storage key."""

    @abstractmethod
    async def retrieve_image(self, key: str) -> tuple[bytes, str] | None:
        """Retrieve image bytes and content type by key."""


class S3ImageStorage(ImageStorage):
    """Stores uploaded images in S3/R2-compatible object storage."""

    def __init__(self) -> None:
        if not settings.image_upload_enabled:
            raise ValueError(
                "S3 image uploads require s3_endpoint_url, s3_access_key_id, "
                "s3_secret_access_key, and s3_bucket_name to be configured."
            )

    def _get_s3_client(self):
        session = aioboto3.Session()
        return session.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
        )

    async def store_image(self, key: str, content: bytes, content_type: str) -> str:
        async with self._get_s3_client() as s3:
            await s3.put_object(
                Bucket=settings.s3_bucket_name,
                Key=key,
                Body=content,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
            )
        return key

    async def retrieve_image(self, key: str) -> tuple[bytes, str] | None:
        try:
            async with self._get_s3_client() as s3:
                response = await s3.get_object(
                    Bucket=settings.s3_bucket_name,
                    Key=key,
                )
                content = await response["Body"].read()
                content_type = response.get("ContentType") or "application/octet-stream"
                return content, content_type
        except Exception:
            logger.warning("Failed to retrieve uploaded image from S3 key=%s", key)
            return None


def generate_image_storage_key(extension: str) -> str:
    """Build a safe image object key under date-based prefixes."""
    normalized_ext = extension.strip().lower().lstrip(".")
    if not normalized_ext:
        normalized_ext = "bin"

    now = datetime.now(UTC)
    return f"images/{now.strftime('%Y')}/{now.strftime('%m')}/{uuid4().hex}.{normalized_ext}"


def normalize_image_storage_key(raw_key: str) -> str:
    """Normalize and validate a user-facing storage key."""
    key = raw_key.strip().lstrip("/")
    if not key or ".." in key or "\\" in key or not key.startswith("images/"):
        raise ValueError("Invalid image key")
    return key


_storage_instance: ImageStorage | None = None


def get_image_storage() -> ImageStorage:
    """Get configured image storage backend (singleton)."""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = S3ImageStorage()
    return _storage_instance
