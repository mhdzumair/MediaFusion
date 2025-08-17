import logging
from typing import List, Optional, Dict, Any
from pydantic import BaseModel

from fastapi import APIRouter, HTTPException, Depends, Request, Body, Query
from fastapi.responses import HTMLResponse
from beanie.operators import In

from db.config import settings
from db.models import RSSFeed
from db.schemas import RSSFeedCreate, RSSFeedUpdate, RSSFeedBulkImport
from scrapers.rss_scraper import run_rss_feed_scraper
from utils import const
from utils.validation_helper import api_password_dependency
from utils.runtime_const import TEMPLATES
import re  # Add this import at the top of the file

router = APIRouter()

logger = logging.getLogger(__name__)


# Add a new model for the test feed request
class TestFeedRequest(BaseModel):
    url: str
    patterns: Optional[Dict[str, Any]] = None


@router.get("/", response_class=HTMLResponse)
async def get_rss_manager(
    request: Request,
):
    """
    Renders the RSS Feed Manager interface.
    This page allows administrators to manage RSS feeds used for content discovery.
    """
    return TEMPLATES.TemplateResponse(
        "html/rss_manager.html",
        {
            "request": request,
            "addon_name": settings.addon_name,
            "logo_url": settings.logo_url,
            "catalog_data": const.CATALOG_DATA,
            "supported_movie_catalog_ids": const.USER_UPLOAD_SUPPORTED_MOVIE_CATALOG_IDS,
            "supported_series_catalog_ids": const.USER_UPLOAD_SUPPORTED_SERIES_CATALOG_IDS,
        },
    )

@router.get("/feeds", response_model=List[RSSFeed])
async def get_all_rss_feeds(
    _: str = Depends(api_password_dependency)
) -> List[RSSFeed]:
    """Get all RSS feeds"""
    return await RSSFeed.find_all().to_list()


@router.get("/feeds/{feed_id}", response_model=RSSFeed)
async def get_rss_feed(
    feed_id: str, _: str = Depends(api_password_dependency)
) -> RSSFeed:
    """Get a specific RSS feed by ID"""
    existing_feed = await RSSFeed.get(feed_id)
    if not existing_feed:
        raise HTTPException(status_code=404, detail=f"RSS feed with ID {feed_id} not found")
    return existing_feed


@router.post("/feeds", response_model=RSSFeed)
async def create_rss_feed(
    feed: RSSFeedCreate, _: str = Depends(api_password_dependency)
) -> RSSFeed:
    """Create a new RSS feed"""
    try:
        # Check if a feed with the same URL already exists
        existing_feed = await RSSFeed.find_one({"url": feed.url})
        if existing_feed:
            raise HTTPException(
                status_code=400, detail=f"RSS feed with URL {feed.url} already exists"
            )

        # Create new feed model (catalog_ids no longer used)
        new_feed = RSSFeed(
            name=feed.name,
            url=feed.url,
            parsing_patterns=feed.parsing_patterns.model_dump(),
            filters=feed.filters.model_dump(),
            active=feed.active,
            auto_detect_catalog=feed.auto_detect_catalog,
            catalog_patterns=[pattern.model_dump() for pattern in (feed.catalog_patterns or [])],
            source=feed.source,
            torrent_type=feed.torrent_type,
        )

        await new_feed.insert()
        return new_feed
    except Exception as e:
        logger.exception(f"Failed to create RSS feed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create RSS feed: {str(e)}")


@router.put("/feeds/{feed_id}", response_model=RSSFeed)
async def update_rss_feed(
    feed_id: str, feed_update: RSSFeedUpdate, _: str = Depends(api_password_dependency)
) -> RSSFeed:
    """Update an existing RSS feed"""
    existing_feed = await RSSFeed.get(feed_id)
    if not existing_feed:
        raise HTTPException(status_code=404, detail=f"RSS feed with ID {feed_id} not found")

    # Update fields if provided
    update_data = feed_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(existing_feed, field, value)

    await existing_feed.save()
    return existing_feed


@router.delete("/feeds/{feed_id}")
async def delete_rss_feed(feed_id: str, _: str = Depends(api_password_dependency)):
    """Delete an RSS feed"""
    existing_feed = await RSSFeed.get(feed_id)
    if not existing_feed:
        raise HTTPException(status_code=404, detail=f"RSS feed with ID {feed_id} not found")

    await existing_feed.delete()
    return {"detail": f"RSS feed {feed_id} deleted successfully"}



@router.post("/feeds/bulk-import")
async def bulk_import_rss_feeds(import_data: RSSFeedBulkImport):
    """Import multiple RSS feeds at once"""
    if import_data.api_password != settings.api_password:
        raise HTTPException(status_code=401, detail="Invalid API password")

    try:
        existing_feed_urls = [feed.url for feed in await RSSFeed.find_all().to_list()]

        imported_feeds = []
        skipped_feeds = []

        for feed_data in import_data.feeds:
            # Skip duplicates
            if feed_data.url in existing_feed_urls:
                skipped_feeds.append(feed_data.url)
                continue

            new_feed = RSSFeed(
                name=feed_data.name,
                url=feed_data.url,
                parsing_patterns=feed_data.parsing_patterns,
                active=feed_data.active,
            )

            await new_feed.insert()
            imported_feeds.append(str(new_feed.id))
            existing_feed_urls.append(feed_data.url)

        return {
            "detail": f"Imported {len(imported_feeds)} RSS feeds, skipped {len(skipped_feeds)} duplicates",
            "imported": imported_feeds,
            "skipped": skipped_feeds
        }
    except Exception as e:
        logger.exception(f"Failed to import RSS feeds: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to import RSS feeds: {str(e)}")


@router.post("/feeds/run")
async def run_rss_feed_scraper_endpoint(_: str = Depends(api_password_dependency)):
    """Manually trigger the RSS feed scraper"""
    try:
        run_rss_feed_scraper()
        return {"detail": "RSS feed scraper started successfully"}
    except Exception as e:
        logger.exception(f"Failed to start RSS feed scraper: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to start RSS feed scraper: {str(e)}")


@router.post("/feeds/test-feed", response_model=dict)
async def test_rss_feed(
    request_data: TestFeedRequest,
    _: str = Depends(api_password_dependency)
):
    """Test an RSS feed and detect its structure"""
    from scrapers.rss_scraper import RssScraper

    try:
        scraper = RssScraper()
        items = await scraper.fetch_feed(request_data.url, "Test Feed")

        if not items:
            return {"status": "error", "message": "No items found in feed"}

        # Extract sample item for analysis
        sample_item = items[0]

        # Detect possible patterns
        detected_patterns = scraper.detect_feed_patterns(sample_item)

        # If we have regex patterns in the request, test them on the sample
        regex_results = {}
        if request_data.patterns:
            for field, pattern in request_data.patterns.items():
                if field.endswith('_regex') and pattern:
                    # Extract source field (e.g., description for size_regex)
                    source_field = field.replace('_regex', '')
                    source_value = None

                    # Get the source value from base fields or from sample directly
                    if request_data.patterns.get(source_field):
                        source_path = request_data.patterns.get(source_field)
                        source_value = scraper.extract_value(sample_item, source_path)
                    else:
                        # Try some common fields
                        for common_field in ['description', 'title', 'content']:
                            value = scraper.extract_value(sample_item, common_field)
                            if value:
                                source_value = value
                                break

                    if source_value:
                        try:
                            # Test the regex
                            regex = re.compile(pattern)
                            match = regex.search(source_value)
                            if match and match.groups():
                                regex_results[field] = {
                                    "source": source_value,
                                    "match": match.group(1),
                                    "status": "success"
                                }
                            elif match:
                                regex_results[field] = {
                                    "source": source_value,
                                    "match": match.group(0),
                                    "status": "success"
                                }
                            else:
                                regex_results[field] = {
                                    "source": source_value,
                                    "match": None,
                                    "status": "no_match"
                                }
                        except re.error as e:
                            regex_results[field] = {
                                "source": source_value,
                                "error": str(e),
                                "status": "error"
                            }

        return {
            "status": "success",
            "message": f"Successfully fetched feed with {len(items)} items",
            "sample_item": sample_item,
            "detected_patterns": detected_patterns,
            "items_count": len(items),
            "regex_results": regex_results
        }
    except Exception as e:
        logger.exception(f"Failed to test RSS feed: {str(e)}")
        return {"status": "error", "message": f"Failed to test RSS feed: {str(e)}"}


@router.post("/feeds/activate-deactivate-feeds")
async def activate_deactivate_feeds(
    feed_ids: List[str], activate: bool, _: str = Depends(api_password_dependency)
):
    """Activate or deactivate multiple RSS feeds at once"""
    try:
        result = await RSSFeed.find(In(RSSFeed.id, feed_ids)).update({"$set": {"active": activate}})
        action = "activated" if activate else "deactivated"
        return {"detail": f"Successfully {action} {result.modified_count} RSS feeds"}
    except Exception as e:
        logger.exception(f"Failed to update RSS feeds: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to update RSS feeds: {str(e)}")

