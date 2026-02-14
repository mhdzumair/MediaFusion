"""
User RSS Feed API with JWT authentication.
Users can manage their own RSS feeds, admins can view/manage all.
"""

import logging
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from api.rbac import is_admin
from api.routers.user.auth import require_auth
from db import crud
from db.config import settings
from db.database import get_async_session, get_read_session
from db.models import RSSFeed, User
from db.schemas import (
    RSSFeedFilters,
    RSSFeedMetrics,
    RSSFeedParsingPatterns,
    RSSSchedulerStatus,
    UserRSSFeedCatalogPatternSchema,
    UserRSSFeedCreate,
    UserRSSFeedOwner,
    UserRSSFeedResponse,
    UserRSSFeedTestRequest,
    UserRSSFeedTestResponse,
    UserRSSFeedUpdate,
)

router = APIRouter(prefix="/api/v1/user-rss", tags=["User RSS"])

logger = logging.getLogger(__name__)


def feed_to_response(feed: RSSFeed, include_user: bool = False) -> UserRSSFeedResponse:
    """Convert RSSFeed model to response schema using UUIDs for external API"""
    # Safely get user UUID - check if relationship is loaded to avoid lazy loading
    try:
        user_uuid = feed.user.uuid if feed.user else str(feed.user_id)
    except Exception:
        # If lazy loading fails, fall back to user_id
        user_uuid = str(feed.user_id)

    response = UserRSSFeedResponse(
        id=feed.uuid,  # Use UUID for external API
        user_id=user_uuid,
        name=feed.name,
        url=feed.url,
        is_active=feed.is_active,
        source=feed.source,
        torrent_type=feed.torrent_type,
        auto_detect_catalog=feed.auto_detect_catalog,
        parsing_patterns=RSSFeedParsingPatterns(**feed.parsing_patterns) if feed.parsing_patterns else None,
        filters=RSSFeedFilters(**feed.filters) if feed.filters else None,
        metrics=RSSFeedMetrics(**feed.metrics) if feed.metrics else None,
        catalog_patterns=[
            UserRSSFeedCatalogPatternSchema(
                id=p.uuid,  # Use UUID for external API
                name=p.name,
                regex=p.regex,
                enabled=p.enabled,
                case_sensitive=p.case_sensitive,
                target_catalogs=p.target_catalogs,
            )
            for p in (feed.catalog_patterns or [])
        ],
        last_scraped_at=feed.last_scraped_at,
        created_at=feed.created_at,
        updated_at=feed.updated_at,
    )
    if include_user and feed.user:
        response.user = UserRSSFeedOwner(
            id=feed.user.uuid,  # Use UUID for external API
            email=feed.user.email,
            username=feed.user.username,
        )
    return response


@router.get("/feeds", response_model=list[UserRSSFeedResponse])
async def list_rss_feeds(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
) -> list[UserRSSFeedResponse]:
    """
    List RSS feeds based on user role.
    - Users: Own feeds only
    - Admins: All feeds with user info
    """
    if is_admin(user):
        # Admin gets all feeds with user info
        feeds_data = await crud.list_all_user_rss_feeds_with_users(session)
        responses = []
        for feed_dict in feeds_data:
            response = UserRSSFeedResponse(
                id=feed_dict["id"],
                user_id=feed_dict["user_id"],
                name=feed_dict["name"],
                url=feed_dict["url"],
                is_active=feed_dict["is_active"],
                source=feed_dict.get("source"),
                torrent_type=feed_dict.get("torrent_type", "public"),
                auto_detect_catalog=feed_dict.get("auto_detect_catalog", False),
                parsing_patterns=RSSFeedParsingPatterns(**feed_dict["parsing_patterns"])
                if feed_dict.get("parsing_patterns")
                else None,
                filters=RSSFeedFilters(**feed_dict["filters"]) if feed_dict.get("filters") else None,
                metrics=RSSFeedMetrics(**feed_dict["metrics"]) if feed_dict.get("metrics") else None,
                catalog_patterns=[UserRSSFeedCatalogPatternSchema(**p) for p in feed_dict.get("catalog_patterns", [])],
                last_scraped_at=datetime.fromisoformat(feed_dict["last_scraped_at"])
                if feed_dict.get("last_scraped_at")
                else None,
                created_at=datetime.fromisoformat(feed_dict["created_at"]) if feed_dict.get("created_at") else None,
                updated_at=datetime.fromisoformat(feed_dict["updated_at"]) if feed_dict.get("updated_at") else None,
                user=UserRSSFeedOwner(**feed_dict["user"]) if feed_dict.get("user") else None,
            )
            responses.append(response)
        return responses
    else:
        # Regular users get only their feeds
        feeds = await crud.list_user_rss_feeds(session, user.id)
        return [feed_to_response(feed) for feed in feeds]


@router.get("/feeds/{feed_id}", response_model=UserRSSFeedResponse)
async def get_rss_feed(
    feed_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
) -> UserRSSFeedResponse:
    """Get a specific RSS feed by ID. Users can only access their own feeds."""
    # Admin can access any feed
    user_id = None if is_admin(user) else user.id

    feed = await crud.get_user_rss_feed(session, feed_id, user_id)
    if not feed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RSS feed with ID {feed_id} not found",
        )

    return feed_to_response(feed, include_user=is_admin(user))


@router.post("/feeds", response_model=UserRSSFeedResponse, status_code=status.HTTP_201_CREATED)
async def create_rss_feed(
    feed_data: UserRSSFeedCreate,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
) -> UserRSSFeedResponse:
    """Create a new RSS feed for the current user."""
    # Check if user already has a feed with this URL
    existing = await crud.get_user_rss_feed_by_url(session, feed_data.url, user.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You already have an RSS feed with URL: {feed_data.url}",
        )

    # Prepare feed data
    feed_dict = {
        "name": feed_data.name,
        "url": feed_data.url,
        "is_active": feed_data.is_active,
        "source": feed_data.source,
        "torrent_type": feed_data.torrent_type,
        "auto_detect_catalog": feed_data.auto_detect_catalog,
        "parsing_patterns": feed_data.parsing_patterns.model_dump() if feed_data.parsing_patterns else None,
        "filters": feed_data.filters.model_dump() if feed_data.filters else None,
        "catalog_patterns": [p.model_dump() for p in (feed_data.catalog_patterns or [])],
    }

    new_feed = await crud.create_user_rss_feed(session, user.id, feed_dict)
    return feed_to_response(new_feed)


@router.put("/feeds/{feed_id}", response_model=UserRSSFeedResponse)
async def update_rss_feed(
    feed_id: str,
    feed_update: UserRSSFeedUpdate,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
) -> UserRSSFeedResponse:
    """Update an existing RSS feed. Users can only update their own feeds."""
    # Admin can update any feed (user_id=None), otherwise check ownership
    user_id = None if is_admin(user) else user.id

    # Prepare update data (exclude unset fields)
    update_data = {}
    update_dict = feed_update.model_dump(exclude_unset=True)

    for key, value in update_dict.items():
        if key == "parsing_patterns" and value is not None:
            update_data[key] = value if isinstance(value, dict) else value.model_dump()
        elif key == "filters" and value is not None:
            update_data[key] = value if isinstance(value, dict) else value.model_dump()
        elif key == "catalog_patterns" and value is not None:
            update_data[key] = [p if isinstance(p, dict) else p.model_dump() for p in value]
        else:
            update_data[key] = value

    # Use the UUID-based update function
    updated_feed = await crud.update_user_rss_feed_by_uuid(session, feed_id, user_id, update_data)
    if not updated_feed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RSS feed with ID {feed_id} not found",
        )

    return feed_to_response(updated_feed)


@router.delete("/feeds/{feed_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rss_feed(
    feed_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete an RSS feed. Users can only delete their own feeds."""
    # Admin can delete any feed
    user_id = None if is_admin(user) else user.id

    deleted = await crud.delete_user_rss_feed(session, feed_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RSS feed with ID {feed_id} not found",
        )


@router.post("/feeds/{feed_id}/test", response_model=UserRSSFeedTestResponse)
async def test_rss_feed(
    feed_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
) -> UserRSSFeedTestResponse:
    """Test an existing RSS feed and detect its structure."""
    from scrapers.rss_scraper import RssScraper

    # Admin can test any feed
    user_id = None if is_admin(user) else user.id

    feed = await crud.get_user_rss_feed(session, feed_id, user_id)
    if not feed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RSS feed with ID {feed_id} not found",
        )

    try:
        scraper = RssScraper()
        items = await scraper.fetch_feed(feed.url, feed.name)

        if not items:
            return UserRSSFeedTestResponse(
                status="error",
                message="No items found in feed",
            )

        sample_item = items[0]
        detected_patterns = scraper.detect_feed_patterns(sample_item)

        return UserRSSFeedTestResponse(
            status="success",
            message=f"Successfully fetched feed with {len(items)} items",
            sample_item=sample_item,
            detected_patterns=detected_patterns,
            items_count=len(items),
        )
    except Exception as e:
        logger.exception(f"Failed to test RSS feed {feed_id}: {e}")
        return UserRSSFeedTestResponse(
            status="error",
            message=f"Failed to test RSS feed: {str(e)}",
        )


@router.post("/feeds/test-url", response_model=UserRSSFeedTestResponse)
async def test_rss_feed_url(
    request: UserRSSFeedTestRequest,
    user: User = Depends(require_auth),
) -> UserRSSFeedTestResponse:
    """Test an RSS feed URL before creating a feed."""
    from scrapers.rss_scraper import RssScraper

    try:
        scraper = RssScraper()
        items = await scraper.fetch_feed(request.url, "Test Feed")

        if not items:
            return UserRSSFeedTestResponse(
                status="error",
                message="No items found in feed",
            )

        sample_item = items[0]
        detected_patterns = scraper.detect_feed_patterns(sample_item)

        # Test regex patterns if provided
        regex_results = {}
        if request.patterns:
            for field, pattern in request.patterns.items():
                if field.endswith("_regex") and pattern:
                    source_field = field.replace("_regex", "")
                    source_value = None

                    if request.patterns.get(source_field):
                        source_path = request.patterns.get(source_field)
                        source_value = scraper.extract_value(sample_item, source_path)
                    else:
                        for common_field in ["description", "title", "content"]:
                            value = scraper.extract_value(sample_item, common_field)
                            if value:
                                source_value = value
                                break

                    if source_value:
                        try:
                            regex = re.compile(pattern)
                            match = regex.search(source_value)
                            if match and match.groups():
                                regex_results[field] = {
                                    "source": source_value,
                                    "match": match.group(1),
                                    "status": "success",
                                }
                            elif match:
                                regex_results[field] = {
                                    "source": source_value,
                                    "match": match.group(0),
                                    "status": "success",
                                }
                            else:
                                regex_results[field] = {
                                    "source": source_value,
                                    "match": None,
                                    "status": "no_match",
                                }
                        except re.error as e:
                            regex_results[field] = {
                                "source": source_value,
                                "error": str(e),
                                "status": "error",
                            }

        return UserRSSFeedTestResponse(
            status="success",
            message=f"Successfully fetched feed with {len(items)} items",
            sample_item=sample_item,
            detected_patterns=detected_patterns,
            items_count=len(items),
            regex_results=regex_results if regex_results else None,
        )
    except Exception as e:
        logger.exception(f"Failed to test RSS feed URL: {e}")
        return UserRSSFeedTestResponse(
            status="error",
            message=f"Failed to test RSS feed: {str(e)}",
        )


@router.post("/feeds/{feed_id}/scrape")
async def scrape_single_feed(
    feed_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """Trigger scraping for a single feed. Users can only scrape their own feeds."""
    from scrapers.rss_scraper import RssScraper

    # Admin can scrape any feed
    user_id = None if is_admin(user) else user.id

    feed = await crud.get_user_rss_feed(session, feed_id, user_id)
    if not feed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RSS feed with ID {feed_id} not found",
        )

    try:
        scraper = RssScraper()
        streams = await scraper._scrape_and_parse(feed)

        return {
            "status": "success",
            "message": f"Successfully scraped {len(streams)} items from feed",
            "items_processed": len(streams),
        }
    except Exception as e:
        logger.exception(f"Failed to scrape feed {feed_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to scrape feed: {str(e)}",
        )


@router.post("/feeds/run-all")
async def run_all_scrapers(
    user: User = Depends(require_auth),
):
    """Run the global RSS scraper (admin only)."""
    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can run the global scraper",
        )

    try:
        from scrapers.rss_scraper import run_rss_feed_scraper

        run_rss_feed_scraper()
        return {"status": "success", "message": "RSS feed scraper started"}
    except Exception as e:
        logger.exception(f"Failed to start RSS scraper: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start RSS scraper: {str(e)}",
        )


@router.post("/feeds/bulk-status")
async def bulk_update_feed_status(
    feed_ids: list[str],
    is_active: bool,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Bulk update status for multiple feeds. Users can only update their own feeds."""
    # Admin can update any feeds
    user_id = None if is_admin(user) else user.id

    updated_count = await crud.bulk_update_user_rss_feed_status(session, feed_ids, user_id, is_active)

    action = "activated" if is_active else "deactivated"
    return {
        "status": "success",
        "message": f"Successfully {action} {updated_count} feeds",
        "updated_count": updated_count,
    }


@router.get("/scheduler-status", response_model=RSSSchedulerStatus)
async def get_scheduler_status(
    user: User = Depends(require_auth),
) -> RSSSchedulerStatus:
    """Get RSS scheduler status including next run time."""
    try:
        from apscheduler.triggers.cron import CronTrigger

        crontab = settings.rss_feed_scraper_crontab
        enabled = not settings.disable_rss_feed_scraper

        next_run = None
        if enabled:
            cron_trigger = CronTrigger.from_crontab(crontab)
            next_run = cron_trigger.get_next_fire_time(None, datetime.now(tz=cron_trigger.timezone))

        return RSSSchedulerStatus(
            crontab=crontab,
            next_run=next_run,
            enabled=enabled,
        )
    except Exception as e:
        logger.exception(f"Failed to get scheduler status: {e}")
        return RSSSchedulerStatus(
            crontab=settings.rss_feed_scraper_crontab,
            enabled=not settings.disable_rss_feed_scraper,
        )
