"""NZB file storage backends with gzip compression and signed URL support.

Provides a storage abstraction for uploaded NZB files with two backends:
- LocalNZBStorage: Stores files on local disk
- S3NZBStorage: Uploads to S3/R2-compatible object storage

All stored files are gzip-compressed to reduce storage size (NZB XML compresses
very well, typically 5-10x reduction).

Download URLs are signed with HMAC-SHA256 and time-limited so they cannot be
shared or scraped.

The factory function `get_nzb_storage()` selects the backend based on config.
"""

import gzip
import hashlib
import hmac
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path

import aioboto3

from db.config import settings

logger = logging.getLogger(__name__)

LOCAL_NZB_DIR = Path("data/nzb")


def generate_signed_nzb_url(guid: str, expires_in: int | None = None) -> str:
    """Generate a signed, time-limited download URL for an NZB file.

    Args:
        guid: NZB identifier
        expires_in: Seconds until expiry. Defaults to settings.nzb_download_url_expiry.

    Returns:
        Signed URL like /api/v1/import/nzb/{guid}/download?expires=...&sig=...
    """
    if expires_in is None:
        expires_in = settings.nzb_download_url_expiry
    expires = int(time.time()) + expires_in
    sig = _compute_signature(guid, expires)
    return f"{settings.host_url}/api/v1/import/nzb/{guid}/download?expires={expires}&sig={sig}"


def verify_nzb_signature(guid: str, expires: int, sig: str) -> bool:
    """Verify a signed NZB download URL.

    Returns False if the signature is invalid or the URL has expired.
    """
    if time.time() > expires:
        return False
    expected = _compute_signature(guid, expires)
    return hmac.compare_digest(sig, expected)


def _compute_signature(guid: str, expires: int) -> str:
    return hmac.new(
        settings.secret_key.encode(),
        f"{guid}:{expires}".encode(),
        hashlib.sha256,
    ).hexdigest()


class NZBStorage(ABC):
    """Abstract base class for NZB file storage."""

    @abstractmethod
    async def store(self, guid: str, content: bytes) -> None:
        """Store NZB content (gzip-compressed).

        Args:
            guid: Unique NZB identifier
            content: Raw NZB file bytes (will be compressed before storing)
        """

    @abstractmethod
    async def retrieve(self, guid: str) -> bytes | None:
        """Retrieve and decompress NZB content by guid.

        Returns:
            Raw (decompressed) NZB file bytes or None if not found
        """


class LocalNZBStorage(NZBStorage):
    """Stores gzip-compressed NZB files on local disk."""

    def __init__(self):
        LOCAL_NZB_DIR.mkdir(parents=True, exist_ok=True)

    async def store(self, guid: str, content: bytes) -> None:
        file_path = LOCAL_NZB_DIR / f"{guid}.nzb.gz"
        compressed = gzip.compress(content, compresslevel=6)
        file_path.write_bytes(compressed)
        logger.info(
            "Stored NZB %s locally (%d bytes -> %d bytes gzipped)",
            guid,
            len(content),
            len(compressed),
        )

    async def retrieve(self, guid: str) -> bytes | None:
        gz_path = LOCAL_NZB_DIR / f"{guid}.nzb.gz"
        if gz_path.exists():
            return gzip.decompress(gz_path.read_bytes())
        # Fallback: check for uncompressed files from before this change
        raw_path = LOCAL_NZB_DIR / f"{guid}.nzb"
        if raw_path.exists():
            return raw_path.read_bytes()
        return None


class S3NZBStorage(NZBStorage):
    """Uploads gzip-compressed NZB files to S3/R2-compatible object storage."""

    def __init__(self):
        if not all(
            [
                settings.s3_endpoint_url,
                settings.s3_access_key_id,
                settings.s3_secret_access_key,
                settings.s3_bucket_name,
            ]
        ):
            raise ValueError(
                "S3 storage requires s3_endpoint_url, s3_access_key_id, "
                "s3_secret_access_key, and s3_bucket_name to be configured."
            )

    def _get_key(self, guid: str) -> str:
        return f"nzb/{guid}.nzb.gz"

    def _get_s3_client(self):
        session = aioboto3.Session()
        return session.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
        )

    async def store(self, guid: str, content: bytes) -> None:
        compressed = gzip.compress(content, compresslevel=6)
        async with self._get_s3_client() as s3:
            await s3.put_object(
                Bucket=settings.s3_bucket_name,
                Key=self._get_key(guid),
                Body=compressed,
                ContentType="application/gzip",
            )
        logger.info(
            "Stored NZB %s to S3 (%d bytes -> %d bytes gzipped)",
            guid,
            len(content),
            len(compressed),
        )

    async def retrieve(self, guid: str) -> bytes | None:
        try:
            async with self._get_s3_client() as s3:
                response = await s3.get_object(
                    Bucket=settings.s3_bucket_name,
                    Key=self._get_key(guid),
                )
                compressed = await response["Body"].read()
                return gzip.decompress(compressed)
        except Exception:
            logger.warning("Failed to retrieve NZB %s from S3", guid)
            return None


_storage_instance: NZBStorage | None = None


def get_nzb_storage() -> NZBStorage:
    """Get the configured NZB storage backend (singleton)."""
    global _storage_instance
    if _storage_instance is None:
        if settings.nzb_file_storage_backend == "s3":
            _storage_instance = S3NZBStorage()
        else:
            _storage_instance = LocalNZBStorage()
    return _storage_instance
