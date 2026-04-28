"""
Pre-warm task for the Discover feature.

Pulls weekly trending movies + series from TMDB (using the server-side key),
materializes them as Media rows, and links them to the discover_pinned_* system
catalogs so the most-visited Discover rows resolve immediately from the DB.
"""

import logging

from api.task_queue import actor
from db.config import settings
from db.database import get_background_session

logger = logging.getLogger(__name__)

PREWARM_LIMIT = 20  # top-N titles per media type to pre-import


@actor(priority=5, max_retries=2, time_limit=30 * 60 * 1000)
async def run_discover_prewarm(**kwargs):
    """Scheduled task: pre-warm Discover catalog with TMDB weekly trending."""
    if not settings.discover_enabled:
        logger.debug("Discover pre-warm skipped: feature disabled")
        return
    if not settings.tmdb_api_key:
        logger.debug("Discover pre-warm skipped: no server-side TMDB API key configured")
        return

    from scrapers.scraper_tasks import meta_fetcher
    from scrapers.tmdb_discover import tmdb_trending

    catalog_names = {
        "movie": "discover_pinned_movies",
        "series": "discover_pinned_series",
    }

    for media_type, catalog_name in catalog_names.items():
        tmdb_media = "movie" if media_type == "movie" else "tv"
        try:
            raw = await tmdb_trending(
                settings.tmdb_api_key,
                media_type=tmdb_media,
                window="week",
                page=1,
            )
        except Exception as e:
            logger.warning(f"Discover pre-warm: TMDB trending fetch failed for {media_type}: {e}")
            continue

        items = raw.get("items", [])[:PREWARM_LIMIT]
        if not items:
            continue

        async with get_background_session() as session:
            from sqlmodel import select

            from db.models import MediaCatalogLink
            from db.models.providers import MediaExternalID
            from db.models.reference import Catalog

            catalog_result = await session.exec(select(Catalog).where(Catalog.name == catalog_name))
            catalog = catalog_result.first()
            if not catalog:
                logger.warning(f"Discover pre-warm: catalog '{catalog_name}' not found in DB")
                continue

            for item in items:
                tmdb_id = item["external_id"]
                try:
                    # Skip if already in DB with tmdb external ID
                    existing = await session.exec(
                        select(MediaExternalID).where(
                            MediaExternalID.provider == "tmdb",
                            MediaExternalID.external_id == tmdb_id,
                        )
                    )
                    ext_row = existing.first()
                    if ext_row:
                        media_id = ext_row.media_id
                    else:
                        # Fetch full metadata and materialize
                        data = await meta_fetcher.get_metadata_from_provider("tmdb", tmdb_id, media_type)
                        if not data:
                            continue
                        media_id = await _create_media_from_data(session, data, media_type)
                        if not media_id:
                            continue

                    # Link to system catalog (idempotent)
                    existing_link = await session.exec(
                        select(MediaCatalogLink).where(
                            MediaCatalogLink.media_id == media_id,
                            MediaCatalogLink.catalog_id == catalog.id,
                        )
                    )
                    if not existing_link.first():
                        session.add(MediaCatalogLink(media_id=media_id, catalog_id=catalog.id))

                except Exception as e:
                    logger.warning(f"Discover pre-warm: failed to import tmdb:{tmdb_id}: {e}")
                    continue

            try:
                await session.commit()
            except Exception as e:
                logger.warning(f"Discover pre-warm: commit failed for {catalog_name}: {e}")
                await session.rollback()

    logger.info("Discover pre-warm completed")


async def _create_media_from_data(session, data: dict, media_type: str) -> int | None:
    """Create a Media row from fetched metadata, return its id."""

    from db.enums import MediaType
    from db.models import Media
    from db.models.providers import MediaExternalID

    mt = MediaType.MOVIE if media_type == "movie" else MediaType.SERIES
    title = data.get("title", "Unknown")

    media = Media(
        type=mt,
        title=title,
        year=data.get("year"),
        description=data.get("description"),
        is_user_created=False,
        is_public=True,
    )
    session.add(media)
    await session.flush()

    for provider, field in [("imdb", "imdb_id"), ("tmdb", "tmdb_id"), ("tvdb", "tvdb_id")]:
        val = data.get(field)
        if val:
            ext = MediaExternalID(
                media_id=media.id,
                provider=provider,
                external_id=str(val),
            )
            try:
                session.add(ext)
                await session.flush()
            except Exception:
                pass

    return media.id
