import logging
import re
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

import dramatiq
import httpx
import xmltodict

from db import sql_crud
from db.database import get_background_session
from db.redis_database import REDIS_ASYNC_CLIENT
from db.enums import TorrentType
from db.schemas import (
    RSSFeedFilters,
    RSSFeedParsingPatterns as RSSParsingPatterns,
    TorrentStreamData,
    MetadataData,
)
from db.sql_models import RSSFeed
from scrapers.base_scraper import BaseScraper
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker
from utils.parser import convert_size_to_bytes, is_contain_18_plus_keywords
from utils.wrappers import minimum_run_interval
from utils.const import CATALOG_DATA
from db.config import settings

logger = logging.getLogger(__name__)


class RssScraper(BaseScraper):
    def __init__(self):
        super().__init__(cache_key_prefix="rss_scraper", logger_name=__name__)
        self.processed_items_key = "rss_scraper:processed_items"
        self.processed_items_expiry = 60 * 60 * 24 * 7  # 7 days

    async def is_item_processed(self, item_id: str) -> bool:
        """Check if an item has already been processed"""
        return bool(
            await REDIS_ASYNC_CLIENT.sismember(self.processed_items_key, item_id)
        )

    async def mark_item_as_processed(self, item_id: str):
        """Mark an item as processed"""
        await REDIS_ASYNC_CLIENT.sadd(self.processed_items_key, item_id)
        await REDIS_ASYNC_CLIENT.expire(
            self.processed_items_key, self.processed_items_expiry
        )

    def contains_blocklist_keywords(self, title: str, description: str = "") -> bool:
        """Check if title or description contains blocklist keywords"""
        content = f"{title} {description}".lower()
        return any(keyword.lower() in content for keyword in self.blocklist_keywords)

    async def _scrape_and_parse(
        self, feed: RSSFeed, *args, **kwargs
    ) -> List[TorrentStreamData]:
        """Scrape and parse a single RSS feed"""
        results = []
        start_time = time.time()

        feed_id = feed.id  # Keep as int

        # Initialize per-run metrics
        items_processed_this_run = 0
        items_skipped_this_run = 0
        errors_this_run = 0
        skip_reasons_this_run = {}

        try:
            # Fetch the feed content
            items = await self.fetch_feed(feed.url, feed.name)
            if not items:
                self.logger.warning(f"No items found in feed: {feed.name}")
                await self.update_feed_metrics(
                    feed_id, 0, 0, 0, 0, time.time() - start_time, {}
                )
                return []

            items_found = len(items)
            self.metrics.record_found_items(items_found)

            # Process items with circuit breaker
            circuit_breaker = CircuitBreaker(
                failure_threshold=3, recovery_timeout=10, half_open_attempts=2
            )
            processed_info_hashes = set()
            collected_streams = []
            processed_item_ids = []

            async for processed_item in batch_process_with_circuit_breaker(
                self.process_feed_item,
                items,
                batch_size=10,
                rate_limit_delay=1,  # 1 second delay between batches
                cb=circuit_breaker,
                feed=feed,
                feed_id=feed_id,
                processed_info_hashes=processed_info_hashes,
            ):
                if processed_item:
                    if isinstance(processed_item, TorrentStreamData):
                        collected_streams.append(processed_item)
                        processed_item_ids.append(processed_item.id)
                        self.metrics.record_processed_item()
                        items_processed_this_run += 1
                    elif (
                        isinstance(processed_item, dict)
                        and "skip_reason" in processed_item
                    ):
                        # Track skip reason
                        reason = processed_item["skip_reason"]
                        skip_reasons_this_run[reason] = (
                            skip_reasons_this_run.get(reason, 0) + 1
                        )
                    else:
                        processed_item_ids.append(processed_item)

            # Bulk insert all collected streams
            if collected_streams:
                async with get_background_session() as session:
                    await sql_crud.store_new_torrent_streams(
                        session, [s.model_dump(by_alias=True) for s in collected_streams]
                    )
                results.extend(collected_streams)
                self.logger.info(
                    f"Bulk inserted {len(collected_streams)} streams for feed: {feed.name}"
                )

            # Mark all processed items as processed
            for item_id in processed_item_ids:
                await self.mark_item_as_processed(item_id)

            # Calculate skipped items (items found - items processed)
            items_skipped_this_run = items_found - items_processed_this_run

            # Update feed metrics
            scrape_duration = time.time() - start_time
            await self.update_feed_metrics(
                feed_id,
                items_found,
                items_processed_this_run,
                items_skipped_this_run,
                errors_this_run,
                scrape_duration,
                skip_reasons_this_run,
            )

            # Update last scraped timestamp
            await self.update_feed_last_scraped(feed_id)

            return results
        except Exception as e:
            errors_this_run += 1
            self.metrics.record_error("feed_processing_error")
            self.logger.exception(f"Error processing RSS feed {feed.name}: {str(e)}")

            # Update metrics even on error
            scrape_duration = time.time() - start_time
            await self.update_feed_metrics(
                feed_id,
                0,
                items_processed_this_run,
                items_skipped_this_run,
                errors_this_run,
                scrape_duration,
                skip_reasons_this_run,
            )
            return []

    async def fetch_feed(self, url: str, name: str) -> List[Dict]:
        """Fetch and parse an RSS feed"""
        try:
            # Fetch the feed content
            response = await self.http_client.get(url, follow_redirects=True)
            response.raise_for_status()
            content = response.text
            try:
                # Try to parse as XML first
                xml_dict = xmltodict.parse(content)
                # Most RSS feeds have their items under channel.item
                if "rss" in xml_dict and "channel" in xml_dict["rss"]:
                    items = xml_dict["rss"]["channel"].get("item", [])
                # Some feeds might have a different structure
                elif "feed" in xml_dict and "entry" in xml_dict["feed"]:
                    items = xml_dict["feed"].get("entry", [])
                else:
                    self.logger.error(f"Unexpected RSS feed structure for {name}")
                    return []
                if not isinstance(items, list):
                    items = [items]
                return items
            except Exception as xml_error:
                self.logger.info(f"XML parsing failed for {name}, {str(xml_error)}")
                return []
        except Exception as e:
            self.logger.error(f"Error fetching RSS feed {name}: {str(e)}")
            return []

    def extract_value(self, item: Dict, path: str) -> Any:
        """
        Extract value from an item using a dotted path notation.
        Supports:
        - Basic dot notation: "enclosure.url"
        - Array indexing with '$': "enclosure.$.url" (first item)
        - Array search by attribute: "torznab:attr[@name="seeders"]@value"
        """
        if not path or not item:
            return None

        # Handle complex array search pattern like: torznab:attr[@name="seeders"]@value
        if "[@" in path and "]" in path:
            return self._extract_with_array_search(item, path)

        # Handle basic dot notation and array indexing
        parts = path.split(".")
        current = item
        for part in parts:
            if not current:
                return None
            if "$" in part:
                # Handle array with wildcard
                array_part, index = part.split("$")
                if array_part and array_part.endswith("."):
                    array_part = array_part[:-1]
                if array_part:
                    current = current.get(array_part)
                if not current or not isinstance(current, list) or not current:
                    return None
                # Get the specific index or the first item
                idx = int(index) if index else 0
                if len(current) > idx:
                    current = current[idx]
                else:
                    return None
            else:
                current = current.get(part)
            if current is None:
                return None
        return current

    def _extract_with_array_search(self, item: Dict, path: str) -> Any:
        """
        Extract value using array search pattern like: torznab:attr[@name="seeders"]@value
        """
        try:
            # Parse the pattern: torznab:attr[@name="seeders"]@value
            # First, find the bracket section
            bracket_start = path.find("[@")
            bracket_end = path.find("]", bracket_start)

            if bracket_start == -1 or bracket_end == -1:
                self.logger.warning(f"Invalid bracket syntax in path: {path}")
                return None

            base_path = path[:bracket_start]
            search_condition = path[bracket_start + 2 : bracket_end]
            remaining_path = path[bracket_end + 1 :]

            # The remaining path should start with @ for attribute access
            target_field = remaining_path
            # Keep the @ prefix if present, as it's part of the actual key name in XML attributes
            # Don't remove it since the actual data has keys like "@value", "@name", etc.

            self.logger.debug(
                f"Parsed components: base_path='{base_path}', search_condition='{search_condition}', target_field='{target_field}'"
            )

            # Parse search condition: @name="seeders" or name="seeders"
            if "=" not in search_condition:
                self.logger.warning(f"Invalid search condition in path: {path}")
                return None

            equal_index = search_condition.find("=")
            search_key = search_condition[:equal_index]
            search_value = search_condition[equal_index + 1 :]
            search_value = search_value.strip("\"'")  # Remove quotes

            # Add @ prefix to search key if not already present (to match XML attribute format)
            if not search_key.startswith("@"):
                search_key = "@" + search_key

            self.logger.debug(
                f"Search params: search_key='{search_key}', search_value='{search_value}'"
            )

            # Navigate to the array
            current = item
            if base_path:
                for part in base_path.split("."):
                    if not part:  # Skip empty parts
                        continue
                    if not current or part not in current:
                        self.logger.debug(f"Base path part '{part}' not found")
                        return None
                    current = current[part]

            # Search in the array
            if not isinstance(current, list):
                self.logger.debug(
                    f"Expected array for path '{base_path}' but got {type(current)}"
                )
                return None

            self.logger.debug(
                f"Searching in array of {len(current)} items for {search_key}='{search_value}'"
            )

            for i, array_item in enumerate(current):
                if isinstance(array_item, dict):
                    # Check if this item matches our search condition
                    item_value = array_item.get(search_key)
                    self.logger.debug(
                        f"Item {i}: {search_key} = '{item_value}' (looking for '{search_value}')"
                    )

                    if item_value == search_value:
                        # Return the target field from this item
                        result = array_item.get(target_field)
                        self.logger.debug(
                            f"Found match! Returning {target_field} = '{result}'"
                        )
                        return result

            self.logger.debug(
                f"No matching item found for {search_key}='{search_value}'"
            )
            return None

        except (ValueError, AttributeError, KeyError) as e:
            self.logger.warning(f"Error parsing complex path '{path}': {e}")
            return None

    async def process_feed_item(
        self, item: Dict, feed: RSSFeed, feed_id: int, processed_info_hashes: set
    ) -> Optional[TorrentStreamData | str]:
        """Process a single RSS feed item"""
        try:
            # Convert dict to Pydantic model for attribute access
            patterns = RSSParsingPatterns(**(feed.parsing_patterns or {}))
            title = self.extract_value(item, patterns.title)
            if not title:
                self.logger.debug("Missing title in RSS item")
                return None

            # Skip adult content
            if is_contain_18_plus_keywords(title):
                self.logger.info(f"Skipping adult content: {title}")
                self.metrics.record_skip("Adult content")
                return {"skip_reason": "Adult content"}

            # Skip blocklisted content (games, software, etc.)
            description = self.extract_value(item, patterns.description) or ""
            if self.contains_blocklist_keywords(title, str(description)):
                self.logger.info(f"Skipping blocklisted content: {title}")
                self.metrics.record_skip("Blocklisted content")
                return {"skip_reason": "Blocklisted content"}

            # Check if this feed contains torrent information
            has_torrent_info = bool(
                patterns.magnet
                or patterns.torrent
                or patterns.magnet_regex
                or patterns.torrent_regex
            )
            if has_torrent_info:
                return await self.process_torrent_feed_item(
                    item, feed, feed_id, processed_info_hashes
                )
            return None
        except Exception as e:
            self.metrics.record_error("item_processing_error")
            self.logger.exception(f"Error processing RSS feed item: {str(e)}")
            return None

    async def process_torrent_feed_item(
        self, item: Dict, feed: RSSFeed, feed_id: int, processed_info_hashes: set
    ) -> Optional[TorrentStreamData]:
        """Process a feed item containing torrent information with integrated filtering"""
        try:
            # Convert dicts to Pydantic models for attribute access
            patterns = RSSParsingPatterns(**(feed.parsing_patterns or {}))
            filters = RSSFeedFilters(**(feed.filters or {}))

            title = self.extract_value(item, patterns.title)
            if not title:
                self.logger.debug("Missing title in RSS torrent item")
                return None

            # Extract size and seeders using proper pattern extraction
            size_str = self.extract_field_with_patterns(
                item,
                patterns,
                "size",
                fallback_fields=["description", "link", "content", "title"],
            )

            seeders_str = self.extract_field_with_patterns(
                item,
                patterns,
                "seeders",
                fallback_fields=["description", "link", "content", "title"],
            )

            publish_date_str = self.extract_value(item, patterns.pubDate)
            publish_date = (
                datetime.strptime(publish_date_str, "%a, %d %b %Y %H:%M:%S %z")
                if publish_date_str
                else None
            )

            # Extract category for filtering
            category = self.extract_value(item, patterns.category)

            # Convert extracted values to proper types
            size_bytes = convert_size_to_bytes(str(size_str)) if size_str else 0
            size_mb = size_bytes / (1024 * 1024) if size_bytes > 0 else 0

            try:
                seeders = int(seeders_str) if seeders_str else 0
            except (ValueError, TypeError):
                seeders = 0

            # Apply all filters after extraction (consolidated filtering logic)
            if not self._passes_all_filters(title, size_mb, seeders, category, filters):
                return None

            parsed_data = self.parse_title_data(title)

            magnet_link = self.extract_field_with_patterns(
                item,
                patterns,
                "magnet",
                fallback_fields=["description", "link", "content", "title"],
            )

            torrent_link = self.extract_field_with_patterns(
                item,
                patterns,
                "torrent",
                fallback_fields=["description", "link", "content", "title"],
            )

            # Get episode name parser from feed patterns if available
            episode_name_parser = (feed.parsing_patterns or {}).get("episode_name_parser")

            torrent_data, is_torrent_downloaded = await self.get_torrent_data(
                torrent_link or magnet_link,
                parsed_data,
                episode_name_parser=episode_name_parser,
            )

            if not is_torrent_downloaded:
                return None

            info_hash = torrent_data.get("info_hash")
            if not info_hash:
                self.logger.debug(f"Could not extract info hash from: {title}")
                return None

            # Check if already processed in this run
            if info_hash in processed_info_hashes:
                self.logger.debug(f"Torrent already processed in this run: {title}")
                self.metrics.record_skip("Duplicate torrent")
                return {"skip_reason": "Duplicate torrent"}

            # Check if item was already processed (Redis cache)
            if await self.is_item_processed(info_hash):
                self.logger.debug(f"Torrent already processed previously: {title}")
                self.metrics.record_skip("Already processed")
                return {"skip_reason": "Already processed"}

            torrent_data.update(
                {
                    "seeders": seeders,
                    "created_at": publish_date,
                    "source": feed.source or f"RSS Feed: {feed.name}",
                    "total_size": size_bytes,
                }
            )

            # Determine if this is a movie or series
            is_series = bool(parsed_data.get("seasons") or parsed_data.get("episodes"))

            # Determine catalog assignment
            catalog_ids = []

            # Use catalog detection if enabled
            if feed.auto_detect_catalog:
                detected_catalogs = self.detect_catalog_from_content(
                    item, feed, parsed_data
                )
                if detected_catalogs:
                    # Filter detected catalogs to only include valid ones from CATALOG_DATA
                    valid_catalogs = [
                        cat for cat in detected_catalogs if cat in CATALOG_DATA
                    ]
                    catalog_ids = valid_catalogs if valid_catalogs else []
                    self.logger.debug(
                        f"Auto-detected catalogs for '{title}': {catalog_ids}"
                    )

            # Always add appropriate RSS feed catalog
            rss_catalog = "rss_feed_series" if is_series else "rss_feed_movies"
            final_catalogs = catalog_ids + [rss_catalog]
            torrent_data["catalog"] = final_catalogs

            # Get or create metadata
            metadata = {
                "title": parsed_data.get("title", title),
                "year": parsed_data.get("year"),
                "catalogs": catalog_ids,
            }

            async with get_background_session() as session:
                metadata_result = await sql_crud.get_or_create_metadata(
                    session,
                    metadata,
                    "series" if is_series else "movie",
                    is_search_imdb_title=True,
                    is_imdb_only=False,
                )

            if not metadata_result:
                self.logger.warning(f"Failed to create metadata for: {title}")
                return None

            # Handle different return formats from get_or_create_metadata
            if isinstance(metadata_result, dict):
                meta_id = metadata_result.get("id")
                # Convert dict to metadata object for process_stream
                metadata_cls = (
                    MetadataData if is_series else MetadataData
                )
                metadata_obj = metadata_cls(
                    id=meta_id,
                    title=metadata_result.get("title"),
                    year=metadata_result.get("year"),
                    type="series" if is_series else "movie",
                )
            else:
                # If it's a document object, use it directly
                metadata_obj = metadata_result

            # Create a fake RSS item data structure for process_stream
            rss_item_data = {
                "info_hash": info_hash,
                "torrent_name": torrent_data.get("torrent_name"),
                "announce_list": torrent_data.get("announce_list", []),
                "total_size": torrent_data.get("total_size", 0),
                "largest_file": torrent_data.get("largest_file", {}),
                "languages": torrent_data.get("languages", []),
                "resolution": torrent_data.get("resolution"),
                "codec": torrent_data.get("codec"),
                "quality": torrent_data.get("quality"),
                "audio": torrent_data.get("audio"),
                "hdr": torrent_data.get("hdr"),
                "source": torrent_data.get("source"),
                "uploader": torrent_data.get("uploader"),
                "catalog": final_catalogs,
                "seeders": torrent_data.get("seeders"),
                "created_at": torrent_data.get("created_at"),
                "file_data": torrent_data.get("file_data", []),
            }

            # Use process_stream method instead of custom methods
            stream = await self.process_stream(
                rss_item_data,
                metadata_obj,
                "series" if is_series else "movie",
                processed_info_hashes,
            )

            if stream:
                # Add to processed_info_hashes to avoid processing again in this run
                processed_info_hashes.add(info_hash)
                self.logger.info(
                    f"Successfully created torrent stream: {info_hash} for {title}"
                )
                return stream
            else:
                self.logger.warning(
                    f"Failed to create stream for: {info_hash} for {title}"
                )
                return None
        except (httpx.TimeoutException, httpx.ConnectTimeout):
            self.metrics.record_error("network_timeout")
            self.logger.warning("Network timeout processing RSS torrent feed item")
            return None
        except Exception as e:
            self.metrics.record_error("torrent_processing_error")
            self.logger.exception(f"Error processing RSS torrent feed item: {str(e)}")
            return None

    def _passes_all_filters(
        self,
        title: str,
        size_mb: float,
        seeders: int,
        category: str,
        filters: RSSFeedFilters,
    ) -> bool:
        """Consolidated filtering logic that checks all filters at once"""

        # Title filter (inclusion)
        if filters.title_filter:
            try:
                if not re.search(filters.title_filter, title, re.IGNORECASE):
                    self.logger.debug(f"Item '{title}' doesn't match title filter")
                    self.metrics.record_skip("Title filter")
                    return False
            except re.error:
                self.logger.warning(
                    f"Invalid title filter regex: {filters.title_filter}"
                )

        # Title exclude filter
        if filters.title_exclude_filter:
            try:
                if re.search(filters.title_exclude_filter, title, re.IGNORECASE):
                    self.logger.debug(f"Item '{title}' matches exclude filter")
                    self.metrics.record_skip("Title exclude filter")
                    return False
            except re.error:
                self.logger.warning(
                    f"Invalid title exclude filter regex: {filters.title_exclude_filter}"
                )

        # Size filters
        if filters.min_size_mb and size_mb > 0 and size_mb < filters.min_size_mb:
            self.logger.debug(
                f"Item '{title}' too small: {size_mb}MB < {filters.min_size_mb}MB"
            )
            self.metrics.record_skip("Size too small")
            return False

        if filters.max_size_mb and size_mb > 0 and size_mb > filters.max_size_mb:
            self.logger.debug(
                f"Item '{title}' too large: {size_mb}MB > {filters.max_size_mb}MB"
            )
            self.metrics.record_skip("Size too large")
            return False

        # Seeders filter
        if filters.min_seeders and seeders > 0 and seeders < filters.min_seeders:
            self.logger.debug(
                f"Item '{title}' has too few seeders: {seeders} < {filters.min_seeders}"
            )
            self.metrics.record_skip("Too few seeders")
            return False

        # Category filter
        if (
            filters.category_filter
            and category
            and str(category) not in filters.category_filter
        ):
            self.logger.debug(
                f"Item '{title}' category '{category}' not in allowed list"
            )
            self.metrics.record_skip("Category not allowed")
            return False

        return True

    def extract_field_with_patterns(
        self,
        item: Dict,
        patterns: RSSParsingPatterns,
        field_name: str,
        fallback_fields: List[str] = None,
    ) -> Any:
        """
        Extract a field using both direct path and regex patterns with proper group handling.

        Args:
            item: The RSS item data
            patterns: The parsing patterns from the feed
            field_name: The base field name (e.g., 'magnet', 'size', 'seeders')
            fallback_fields: List of fields to search in if direct path fails (for regex)

        Returns:
            Extracted value or None
        """
        # Try direct path extraction first
        direct_path = getattr(patterns, field_name)
        if direct_path:
            value = self.extract_value(item, direct_path)
            if value:
                return value

        # Try regex extraction if direct path failed or no direct path provided
        regex_pattern = getattr(patterns, f"{field_name}_regex")
        if regex_pattern:
            regex_group = getattr(patterns, f"{field_name}_regex_group", 1)

            # If we have a direct path, try regex on that field first
            if direct_path:
                direct_value = self.extract_value(item, direct_path)
                if direct_value:
                    try:
                        match = re.search(regex_pattern, str(direct_value))
                        if match:
                            return (
                                match.group(regex_group)
                                if regex_group <= len(match.groups())
                                else match.group(0)
                            )
                    except (re.error, IndexError, AttributeError):
                        pass

            # Try regex on fallback fields
            if fallback_fields:
                for fallback_field in fallback_fields:
                    field_value = self.extract_value(
                        item, getattr(patterns, fallback_field, fallback_field)
                    )
                    if field_value:
                        try:
                            match = re.search(regex_pattern, str(field_value))
                            if match:
                                return (
                                    match.group(regex_group)
                                    if regex_group <= len(match.groups())
                                    else match.group(0)
                                )
                        except (re.error, IndexError, AttributeError):
                            continue

        return None

    async def update_feed_last_scraped(self, feed_id: int) -> None:
        """Update the lastScraped timestamp for a feed"""
        try:
            async with get_background_session() as session:
                await sql_crud.update_rss_feed(session, feed_id, {"last_scraped": datetime.now()})
        except Exception as e:
            self.logger.error(f"Error updating feed last_scraped timestamp: {str(e)}")

    async def update_feed_metrics(
        self,
        feed_id: int,
        items_found: int,
        items_processed: int,
        items_skipped: int,
        errors: int,
        duration: float,
        skip_reasons: dict,
    ) -> None:
        """Update the scraping metrics for a feed"""
        try:
            metrics = {
                "total_items_found": items_found,
                "total_items_processed": items_processed,
                "total_items_skipped": items_skipped,
                "total_errors": errors,
                "last_scrape_duration": duration,
                "items_processed_last_run": items_processed,
                "items_skipped_last_run": items_skipped,
                "errors_last_run": errors,
                "skip_reasons": skip_reasons,
            }
            async with get_background_session() as session:
                await sql_crud.update_rss_feed_metrics(session, feed_id, metrics)
        except Exception as e:
            self.logger.warning(f"Failed to update feed metrics for {feed_id}: {e}")

    async def process_all_feeds(self) -> Dict:
        """Process all active RSS feeds"""
        try:
            # Get all active feeds
            async with get_background_session() as session:
                feeds = list(await sql_crud.list_rss_feeds(session, active_only=True))

            if not feeds:
                return {
                    "success": True,
                    "message": "No active feeds found",
                    "results": {},
                }

            self.metrics.start()

            results = {}
            processed_count = 0
            error_count = 0

            for feed in feeds:
                self.logger.info(f"Processing RSS feed: {feed.name} ({feed.url})")
                try:
                    # Use _scrape_and_parse directly to process each feed
                    streams = await self._scrape_and_parse(feed)

                    results[str(feed.id)] = {
                        "feed_id": str(feed.id),
                        "name": feed.name,
                        "success": True,
                        "processed": len(streams),
                        "errors": 0,
                    }
                    processed_count += len(streams)
                except Exception as e:
                    error_count += 1
                    self.logger.exception(f"Error processing feed {feed.name}: {e}")
                    results[str(feed.id)] = {
                        "feed_id": str(feed.id),
                        "name": feed.name,
                        "success": False,
                        "message": str(e),
                        "processed": 0,
                        "errors": 1,
                    }

            self.metrics.stop()
            self.metrics.log_summary(self.logger)

            return {
                "success": True,
                "message": f"Processed {processed_count} items with {error_count} errors from {len(feeds)} feeds",
                "results": results,
            }
        except Exception as e:
            self.logger.exception(f"Error processing RSS feeds: {str(e)}")
            return {
                "success": False,
                "message": f"Error processing feeds: {str(e)}",
                "results": {},
            }
        finally:
            await self.http_client.aclose()

    async def cleanup(self):
        """Cleanup resources"""
        if hasattr(self, "http_client") and self.http_client:
            await self.http_client.aclose()

    # Required abstract methods for process_stream compatibility
    def get_title(self, item: dict) -> str:
        """Get title from RSS item data"""
        return item.get("torrent_name", "")

    def get_info_hash(self, item: dict) -> str:
        """Get info hash from RSS item data"""
        return item.get("info_hash", "")

    def get_guid(self, item: dict) -> str:
        """Get GUID from RSS item data"""
        return item.get("info_hash", "")

    def get_imdb_id(self, item: dict) -> str | None:
        """Get IMDB ID from RSS item data (not typically available in RSS)"""
        return None

    def get_category_ids(self, item: dict) -> list[int]:
        """Get category IDs from RSS item data (not typically used)"""
        return []

    def get_magent_link(self, item: dict) -> str:
        """Get magnet link from RSS item data (not used in this context)"""
        return ""

    def get_download_link(self, item: dict) -> str:
        """Get download link from RSS item data (not used in this context)"""
        return ""

    def get_info_url(self, item: dict) -> str:
        """Get info URL from RSS item data (not used in this context)"""
        return ""

    def get_indexer(self, item: dict) -> str:
        """Get indexer/source from RSS item data"""
        return item.get("source", "RSS Feed")

    def get_torrent_type(self, item: dict) -> TorrentType:
        """Get torrent type (default to public for RSS)"""
        return TorrentType.PUBLIC

    def get_created_at(self, item: dict) -> datetime:
        """Get creation date from RSS item data"""
        return item.get("created_at", datetime.now())

    def get_seeders(self, item: dict) -> int:
        """Get seeders from RSS item data"""
        return item.get("seeders", 0)

    async def parse_indexer_data(
        self, indexer_data: dict, catalog_type: str, parsed_data: dict
    ) -> dict:
        """Parse RSS-specific data and return processed data"""
        # For RSS scraper, we already have the processed data
        info_hash = indexer_data.get("info_hash")
        if not info_hash:
            return None

        # Update parsed_data with RSS-specific information
        parsed_data.update(
            {
                "info_hash": info_hash.lower(),
                "announce_list": indexer_data.get("announce_list", []),
                "total_size": indexer_data.get("total_size", 0),
                "torrent_name": indexer_data.get("torrent_name", ""),
                "largest_file": indexer_data.get("largest_file", {}),
                "file_data": indexer_data.get("file_data", []),
                "seeders": indexer_data.get("seeders", 0),
                "created_at": indexer_data.get("created_at", datetime.now()),
                "source": indexer_data.get("source", "RSS Feed"),
                "uploader": indexer_data.get("uploader"),
                "catalog": indexer_data.get("catalog", []),
                "languages": indexer_data.get("languages", []),
                "resolution": indexer_data.get("resolution"),
                "codec": indexer_data.get("codec"),
                "quality": indexer_data.get("quality"),
                "audio": indexer_data.get("audio"),
                "hdr": indexer_data.get("hdr"),
            }
        )

        return parsed_data

    def detect_feed_patterns(self, sample_item: Dict) -> Dict[str, Any]:
        """
        Analyze a sample RSS item to detect common field patterns.
        Returns suggested field mappings for parsing patterns.
        """
        patterns = {}

        # Common field mappings to check
        field_mappings = {
            "title": ["title", "name"],
            "description": ["description", "summary", "content"],
            "pubDate": ["pubDate", "published", "date", "updated"],
            "category": ["category", "categories", "genre"],
            "size": ["size", "length"],
            "seeders": ["seeders", "seeds"],
            "leechers": ["leechers", "peers"],
            "uploader": ["uploader", "author", "creator"],
            "magnet": ["magnet", "magnet_link", "magnet_url"],
            "torrent": ["torrent", "torrent_link", "torrent_url"],
        }

        # Check each field mapping
        for field, possible_keys in field_mappings.items():
            for key in possible_keys:
                value = self.extract_value(sample_item, key)
                if value:
                    patterns[field] = key
                    break

        # Look for magnet links in common fields
        for field in ["description", "link", "content", "summary", "enclosure.@url"]:
            value = self.extract_value(sample_item, field)
            if value and "magnet:" in str(value):
                # Try to extract magnet with regex
                magnet_match = re.search(r'magnet:\?[^\s<>"]+', str(value))
                if magnet_match:
                    patterns["magnet"] = field
                    patterns["magnet_regex"] = r'magnet:\?[^\s<>"]+'
                    break

        # Look for torrent links
        for field in ["description", "link", "content", "summary", "enclosure.@url"]:
            value = self.extract_value(sample_item, field)
            if value and (".torrent" in str(value) or "download" in str(value).lower()):
                patterns["torrent"] = field
                break

        return patterns

    def detect_catalog_from_content(
        self, item: Dict, feed: RSSFeed, parsed_data: Dict
    ) -> List[str]:
        """
        Auto-detect catalog based on content analysis using parsed data.
        Returns list of catalog IDs that this item should be assigned to.
        """
        catalogs = []

        # Get text content for analysis - convert dict to Pydantic model
        patterns = RSSParsingPatterns(**(feed.parsing_patterns or {}))
        title = str(self.extract_value(item, patterns.title) or "")
        description = str(self.extract_value(item, patterns.description) or "")
        category = str(self.extract_value(item, patterns.category) or "")

        # Combine all text for analysis
        content_text = f"{title} {description} {category}"

        # First check custom catalog patterns if defined
        catalog_patterns = feed.catalog_patterns or []
        for pattern in catalog_patterns:
            if not pattern.enabled:
                continue

            regex_pattern = pattern.regex
            if not regex_pattern:
                continue

            try:
                # Determine regex flags
                flags = 0 if pattern.case_sensitive else re.IGNORECASE

                # Test the pattern against content
                if re.search(regex_pattern, content_text, flags):
                    target_catalogs = pattern.target_catalogs
                    catalogs.extend(target_catalogs)
                    self.logger.debug(
                        f"Pattern '{pattern.name}' matched, adding catalogs: {target_catalogs}"
                    )
            except re.error as e:
                self.logger.warning(
                    f"Invalid regex pattern '{regex_pattern}' in pattern '{pattern.name}': {e}"
                )

        # If custom patterns matched, return those results
        if catalogs:
            return list(set(catalogs))

        # Use parsed data for intelligent catalog detection
        quality = parsed_data.get("quality", "").upper()
        languages = parsed_data.get("languages", [])
        is_series = bool(parsed_data.get("seasons") or parsed_data.get("episodes"))
        content_lower = content_text.lower()

        # Sports detection (high priority) - use specific sport catalogs
        sports_detection = {
            # American Football
            "american_football": ["nfl", "american football", "super bowl"],
            # Baseball
            "baseball": ["mlb", "baseball", "world series"],
            # Basketball
            "basketball": ["nba", "basketball"],
            # Football (Soccer)
            "football": [
                "football",
                "soccer",
                "fifa",
                "uefa",
                "premier league",
                "champions league",
                "world cup",
                "efl",
            ],
            # Formula Racing
            "formula_racing": ["formula", "f1", "grand prix", "f2", "f3"],
            # Hockey
            "hockey": ["nhl", "hockey", "stanley cup"],
            # MotoGP Racing
            "motogp_racing": ["motogp", "moto gp", "motorcycle", "racing"],
            # Rugby
            "rugby": ["rugby", "afl", "australian football", "nrl"],
            # Fighting (WWE, UFC)
            "fighting": [
                "ufc",
                "wwe",
                "wrestling",
                "boxing",
                "mma",
                "raw",
                "smackdown",
                "wrestlemania",
            ],
        }

        # Check for specific sports
        for sport_catalog, keywords in sports_detection.items():
            if any(keyword in content_lower for keyword in keywords):
                return [sport_catalog]  # Specific sport catalog

        # General sports fallback
        general_sports_keywords = [
            "sport",
            "sports",
            "match",
            "game",
            "vs",
            "versus",
            "highlights",
            "replay",
            "tournament",
            "championship",
            "league",
            "espn",
            "sky sports",
            "bt sport",
            "bein",
            "fox sports",
            "nbc sports",
            "match of the day",
            "motd",
        ]

        if any(keyword in content_lower for keyword in general_sports_keywords):
            return ["other_sports"]

        # Map language and content type to catalogs
        if is_series:
            catalogs = [f"{lang.lower()}_series" for lang in languages]
            return catalogs

        else:
            tc_qualities = [
                "CAM",
                "TELESYNC",
                "TELECINE",
                "SCR",
                "SCREENER",
                "WORKPRINT",
                "TC",
                "TS",
            ]
            quality_value = (
                "tcrip"
                if any(tc_qual in quality for tc_qual in tc_qualities)
                else "hdrip"
            )
            catalogs = [f"{lang.lower()}_{quality_value}" for lang in languages]
            return catalogs


@dramatiq.actor(time_limit=60 * 60 * 1000, priority=5, queue_name="scrapy")
@minimum_run_interval(hours=settings.rss_feed_scrape_interval_hour)
async def run_rss_feed_scraper(**kwargs):
    """Scheduled task to run RSS feed scraper"""
    logger.info("Running RSS feed scraper")
    scraper = RssScraper()
    result = await scraper.process_all_feeds()

    total_processed = sum(
        r.get("processed", 0) for r in result.get("results", {}).values()
    )
    total_errors = sum(r.get("errors", 0) for r in result.get("results", {}).values())

    logger.info(
        f"RSS feed scraper completed. "
        f"Processed {total_processed} items with {total_errors} errors"
    )
    return result
