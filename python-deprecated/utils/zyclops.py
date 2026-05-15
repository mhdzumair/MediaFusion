"""Fire-and-forget NZB submission to the Zyclops health API."""

import asyncio
import json
import logging
from datetime import datetime

import httpx

from db.config import settings

logger = logging.getLogger(__name__)


async def _submit_nzb_to_zyclops(
    nzb_content: bytes,
    name: str,
    pub_date: datetime | None = None,
    password: str | None = None,
) -> None:
    """POST NZB content + metadata to the Zyclops health API.

    All exceptions are caught and logged — this never raises.
    """
    url = f"{settings.zyclops_health_api_url.rstrip('/')}/api/v1/nzb"

    metadata: dict[str, str] = {"name": name}
    if pub_date:
        metadata["pubDate"] = pub_date.isoformat()
    if password:
        metadata["password"] = password

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                data={"metadata": json.dumps(metadata)},
                files={"nzb": ("release.nzb", nzb_content, "application/x-nzb")},
            )
            logger.debug(
                "Zyclops NZB ingestion %s: %s",
                response.status_code,
                name[:80],
            )
    except Exception as e:
        logger.debug("Zyclops NZB submission failed for %s: %s", name[:80], e)


def submit_nzb_to_zyclops(
    nzb_content: bytes,
    name: str,
    pub_date: datetime | None = None,
    password: str | None = None,
) -> None:
    """Fire-and-forget wrapper — creates an asyncio task if Zyclops URL is configured."""
    if not settings.zyclops_health_api_url:
        return
    asyncio.create_task(_submit_nzb_to_zyclops(nzb_content, name, pub_date, password))
