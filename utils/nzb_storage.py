"""NZB file storage backends.

Provides a storage abstraction for uploaded NZB files with two backends:
- LocalNZBStorage: Stores files on local disk, served via an API endpoint
- S3NZBStorage: Uploads to S3/R2-compatible object storage

The factory function `get_nzb_storage()` selects the backend based on config.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path

import aioboto3

from db.config import settings

logger = logging.getLogger(__name__)

LOCAL_NZB_DIR = Path("data/nzb")


class NZBStorage(ABC):
    """Abstract base class for NZB file storage."""

    @abstractmethod
    async def store(self, guid: str, content: bytes) -> str:
        """Store NZB content and return its URL.

        Args:
            guid: Unique NZB identifier
            content: Raw NZB file bytes

        Returns:
            URL where the NZB file can be fetched
        """

    @abstractmethod
    async def retrieve(self, guid: str) -> bytes | None:
        """Retrieve NZB content by guid.

        Args:
            guid: Unique NZB identifier

        Returns:
            Raw NZB file bytes or None if not found
        """


class LocalNZBStorage(NZBStorage):
    """Stores NZB files on local disk.

    Files are saved to data/nzb/{guid}.nzb and served via the
    GET /api/v1/nzb/{guid}/download endpoint.
    """

    def __init__(self):
        LOCAL_NZB_DIR.mkdir(parents=True, exist_ok=True)

    async def store(self, guid: str, content: bytes) -> str:
        file_path = LOCAL_NZB_DIR / f"{guid}.nzb"
        file_path.write_bytes(content)
        logger.info(f"Stored NZB {guid} locally at {file_path}")
        return f"{settings.host_url}/api/v1/nzb/{guid}/download"

    async def retrieve(self, guid: str) -> bytes | None:
        file_path = LOCAL_NZB_DIR / f"{guid}.nzb"
        if file_path.exists():
            return file_path.read_bytes()
        return None


class S3NZBStorage(NZBStorage):
    """Uploads NZB files to S3/R2-compatible object storage."""

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
        return f"nzb/{guid}.nzb"

    async def store(self, guid: str, content: bytes) -> str:
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
        ) as s3:
            key = self._get_key(guid)
            await s3.put_object(
                Bucket=settings.s3_bucket_name,
                Key=key,
                Body=content,
                ContentType="application/x-nzb",
            )

        if settings.s3_public_url:
            url = f"{settings.s3_public_url.rstrip('/')}/{key}"
        else:
            url = f"{settings.s3_endpoint_url.rstrip('/')}/{settings.s3_bucket_name}/{key}"

        logger.info(f"Stored NZB {guid} to S3 at {url}")
        return url

    async def retrieve(self, guid: str) -> bytes | None:
        session = aioboto3.Session()
        try:
            async with session.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key_id,
                aws_secret_access_key=settings.s3_secret_access_key,
                region_name=settings.s3_region,
            ) as s3:
                response = await s3.get_object(
                    Bucket=settings.s3_bucket_name,
                    Key=self._get_key(guid),
                )
                return await response["Body"].read()
        except Exception:
            logger.warning(f"Failed to retrieve NZB {guid} from S3")
            return None


_storage_instance: NZBStorage | None = None


def get_nzb_storage() -> NZBStorage:
    """Get the configured NZB storage backend (singleton).

    Returns:
        NZBStorage instance based on settings.nzb_file_storage_backend
    """
    global _storage_instance
    if _storage_instance is None:
        if settings.nzb_file_storage_backend == "s3":
            _storage_instance = S3NZBStorage()
        else:
            _storage_instance = LocalNZBStorage()
    return _storage_instance
