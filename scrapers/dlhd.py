import logging
import random
from datetime import datetime, timedelta
from typing import Optional, List

import httpx
import humanize
import pytz
from dateutil import parser as date_parser

from db.config import settings
from db.models import MediaFusionTVMetaData, TVStreams, MediaFusionEventsMetaData
from db.redis_database import REDIS_ASYNC_CLIENT
from utils import crypto
from utils.config import config_manager
from utils.runtime_const import SPORTS_ARTIFACTS


class DLHDScheduleService:
    def __init__(
        self, start_within_next_hours: int = 1, started_within_hours_ago: int = 6
    ):
        self.name = "dlhd"
        self.schedule_url = config_manager.get_scraper_config(self.name, "schedule_url")
        self.m3u8_base_url = config_manager.get_scraper_config(
            self.name, "m3u8_base_url"
        )
        self.server_lookup_url = config_manager.get_scraper_config(
            self.name, "server_lookup_url"
        )
        self.referer = config_manager.get_scraper_config(self.name, "referer")
        self.gmt = pytz.timezone("Etc/GMT")
        self.start_within_next_hours = start_within_next_hours
        self.started_within_hours_ago = started_within_hours_ago
        self.category_map = config_manager.get_scraper_config(
            self.name, "category_mapping"
        )
        self.last_scrape_key = "dlhd:last_scrape_time"
        self._category_max_times = {}

    async def get_last_scrape_time(self) -> Optional[float]:
        """Get the timestamp of the last schedule scrape"""
        last_scrape = await REDIS_ASYNC_CLIENT.get(self.last_scrape_key)
        return float(last_scrape) if last_scrape else None

    async def update_last_scrape_time(self):
        """Update the timestamp of the last schedule scrape"""
        current_time = datetime.now(tz=self.gmt).timestamp()
        await REDIS_ASYNC_CLIENT.set(self.last_scrape_key, str(current_time))

    async def should_refresh_schedule(self) -> bool:
        """
        Check if schedule should be refreshed based on last scrape time
        Returns True if last scrape was more than half of start_within_next_hours ago
        """
        last_scrape = await self.get_last_scrape_time()
        if not last_scrape:
            return True

        current_time = datetime.now(tz=self.gmt).timestamp()
        refresh_threshold = (
            self.start_within_next_hours * 3600
        ) / 2  # Half of the window in seconds
        return (current_time - last_scrape) > refresh_threshold

    def format_event_time(self, event_timestamp: int) -> str:
        """Format event time in specified timezone"""
        event_time = datetime.fromtimestamp(event_timestamp)
        return humanize.naturaltime(event_time)

    async def fetch_and_parse_schedule(self) -> dict:
        """Fetch and parse the schedule data"""
        logging.info("Fetching fresh schedule data from DLHD")
        try:
            async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as client:
                response = await client.get(self.schedule_url)
                if response.status_code == 200:
                    return response.json()
                logging.error(f"Failed to fetch schedule: {response.status_code}")
        except Exception as e:
            logging.error(f"Error fetching schedule: {e}")
        return {}

    def create_event_id(self, title: str) -> str:
        """Create a unique event ID"""
        return f"mfdlhd{crypto.get_text_hash(title)}"

    def is_event_in_timewindow(self, event_datetime: datetime) -> bool:
        """Check if event is within the configured time window"""
        current_time = datetime.now(tz=self.gmt)
        time_difference = event_datetime - current_time

        # For future events
        if time_difference > timedelta(0):
            return time_difference <= timedelta(hours=self.start_within_next_hours)

        # For past events
        return time_difference >= timedelta(hours=-self.started_within_hours_ago)

    def create_event_metadata(
        self, event: dict, date_str: str, mapped_category: str
    ) -> Optional[MediaFusionEventsMetaData]:
        """
        Create event metadata from schedule data with corrected date handling.
        All times are handled in UK time (GMT) to match the schedule format.

        The function implements a "high water mark" algorithm for detecting day rollovers:
        1. For each category, we track the maximum time seen so far in the sequence
        2. If a new time is less than the max time seen, it's considered next day's event
        3. If a new time is equal or greater than max time, it updates the max time
        """
        try:
            # Parse the schedule date and ensure it's in GMT
            uk_date = date_parser.parse(date_str.split(" - ")[0]).date()

            # Parse event time and combine with date in GMT
            event_time = datetime.strptime(event["time"], "%H:%M").time()
            event_datetime = datetime.combine(uk_date, event_time)
            event_datetime_gmt = self.gmt.localize(event_datetime)

            should_roll_to_next_day = False

            # Get the maximum time seen for this category
            max_time = self._category_max_times.get(mapped_category)

            if max_time is not None:
                if event_time < max_time:
                    # If current time is less than max time seen, it's a next day event
                    should_roll_to_next_day = True
                    logging.debug(
                        f"GMT time rollover in {mapped_category}: Current time {event_time} < Max time {max_time}"
                    )
                else:
                    # Update max time if current time is equal or greater
                    self._category_max_times[mapped_category] = max(
                        max_time, event_time
                    )
                    logging.debug(
                        f"Updated max time for {mapped_category} to {self._category_max_times[mapped_category]}"
                    )
            else:
                # First event in the sequence
                self._category_max_times[mapped_category] = event_time

            # Adjust the date if needed
            if should_roll_to_next_day:
                event_datetime_gmt += timedelta(days=1)
                logging.info(
                    f"Adjusted event date for {event['event']} "
                    f"from {uk_date} to {event_datetime_gmt.date()} GMT due to high water mark {max_time}"
                )

            # Check if event is within our time window
            if not self.is_event_in_timewindow(event_datetime_gmt):
                return None

            event_start_timestamp = int(event_datetime_gmt.timestamp())
            meta_id = self.create_event_id(event["event"])

            return MediaFusionEventsMetaData(
                id=meta_id,
                title=event["event"],
                description="",
                genres=[mapped_category],
                event_start_timestamp=event_start_timestamp,
                poster=random.choice(SPORTS_ARTIFACTS[mapped_category]["poster"]),
                background=random.choice(
                    SPORTS_ARTIFACTS[mapped_category]["background"]
                ),
                logo=random.choice(SPORTS_ARTIFACTS[mapped_category]["logo"]),
                is_add_title_to_poster=True,
                streams=[],
            )
        except Exception as e:
            logging.error(f"Error creating event metadata: {str(e)}")
            return None

    async def find_tv_channel(
        self, channel_name: str
    ) -> Optional[MediaFusionTVMetaData]:
        """Find TV channel in database"""
        return await MediaFusionTVMetaData.find_one({"title": channel_name})

    def create_stream(
        self,
        server_key: str,
        channel_id: str,
        channel_name: str,
        meta_id: str,
        server_type: str,
    ) -> TVStreams:
        """Create a new stream for a channel"""
        url = self.m3u8_base_url.format(
            server_key=server_key,
            server_type=server_type,
            channel_id=channel_id,
        )

        return TVStreams(
            meta_id=meta_id,
            name=channel_name,
            source="DaddyLiveHD",
            url=url,
            behaviorHints={
                "notWebReady": True,
                "proxyHeaders": {
                    "request": {
                        "Referer": self.referer,
                        "Origin": self.referer.rstrip("/"),
                    },
                    "response": {
                        "Content-Type": "application/vnd.apple.mpegurl",
                    },
                },
            },
        )

    async def _process_single_channel(
        self,
        client: httpx.AsyncClient,
        channel: dict,
        meta_id: str,
        is_sd: bool,
        stats: dict,
    ) -> Optional[list[TVStreams]]:
        """Process a single channel and create/find its streams"""
        try:
            channel_name = channel.get("channel_name")
            channel_id = channel.get("channel_id")

            if not channel_name or not channel_id:
                logging.warning(f"Skipping channel with missing data: {channel}")
                stats["failed"] += 1
                return None

            tv_channel = await self.find_tv_channel(channel_name)
            if (
                tv_channel and not is_sd
            ):  # Only look for existing streams for HD channels
                try:
                    channel_streams = await TVStreams.find(
                        {
                            "meta_id": tv_channel.id,
                            "namespaces": "mediafusion",
                            "is_working": True,
                        }
                    ).to_list(None)

                    if channel_streams:
                        stats["found"] += len(channel_streams)
                        return channel_streams

                except Exception as e:
                    logging.error(
                        f"Error finding existing streams for channel {channel_name}: {str(e)}"
                    )

            server_type = "bet" if is_sd else "premium"
            server_url = self.server_lookup_url.format(
                server_type=server_type, channel_id=channel_id
            )

            server_response = await client.get(
                server_url,
                headers={
                    "Referer": f"{self.referer}premiumtv/daddylivehd.php?id={channel_id}"
                },
            )

            if (
                server_response.status_code != 200
                or server_response.headers.get("Content-Type") != "application/json"
            ):
                logging.error(
                    f"Failed to fetch server data for channel: {channel_name} ({channel_id})"
                )
                stats["failed"] += 1
                return None

            server_data = server_response.json()
            server_key = server_data.get("server_key")
            if not server_key:
                logging.error(
                    f"Failed to find server key for channel: {channel_name} ({channel_id})"
                )
                stats["failed"] += 1
                return None

            # Create new stream if no existing streams found or if it's SD
            channel_name += (
                "\n⚠️ Stream only available during the event" if is_sd else ""
            )
            try:
                stream = self.create_stream(
                    server_key, channel_id, channel_name, meta_id, server_type
                )
                stats["created"] += 1
                return [stream]
            except Exception as e:
                logging.error(
                    f"Error creating stream for channel {channel_name}: {str(e)}"
                )
                stats["failed"] += 1
                return None

        except Exception as e:
            logging.error(f"Error processing channel: {str(e)}")
            stats["failed"] += 1
            return None

    async def _process_channel_list(
        self, channels: list | dict, meta_id: str, is_sd: bool, stats: dict
    ) -> List[TVStreams]:
        """Process a list or dictionary of channels"""
        streams = []

        if not channels:  # Early return if channels is empty
            return streams
        client = httpx.AsyncClient(proxy=settings.requests_proxy_url)

        try:
            if isinstance(channels, dict):
                channel_items = channels.values()
            elif isinstance(channels, list):
                channel_items = channels
            else:
                logging.warning(f"Unexpected channels format: {type(channels)}")
                return streams

            for channel in channel_items:
                channel_streams = await self._process_single_channel(
                    client, channel, meta_id, is_sd, stats
                )
                if channel_streams:
                    streams.extend(channel_streams)

        except Exception as e:
            logging.error(f"Error processing channel list: {str(e)}")

        await client.aclose()
        return streams

    async def get_event_streams(self, event: dict, meta_id: str) -> list[TVStreams]:
        """Get all available streams for an event"""
        stats = {
            "found": 0,  # Number of existing streams found
            "created": 0,  # Number of new streams created
            "failed": 0,  # Number of failed attempts
        }

        streams = []

        # Process main channels (HD)
        if "channels" in event:
            hd_streams = await self._process_channel_list(
                event["channels"], meta_id, is_sd=False, stats=stats
            )
            streams.extend(hd_streams)

        # Process SD channels
        if "channels2" in event:
            sd_streams = await self._process_channel_list(
                event["channels2"], meta_id, is_sd=True, stats=stats
            )
            streams.extend(sd_streams)

        if stats["failed"] > 0:
            logging.warning(
                "Event %s: %d channels failed to process", meta_id, stats["failed"]
            )

        logging.debug(
            "Event %s: Found %s existing streams, created %s new streams, failed %s streams",
            meta_id,
            stats["found"],
            stats["created"],
            stats["failed"],
        )

        return streams

    async def cache_event(self, event: MediaFusionEventsMetaData):
        """Cache single event in Redis with appropriate TTL"""
        event_key = f"event:{event.id}"
        event_json = event.model_dump_json(exclude_none=True)

        # Calculate TTL: time until event start + buffer period after start
        current_time = datetime.now(tz=self.gmt)
        event_time = datetime.fromtimestamp(event.event_start_timestamp, self.gmt)

        if event_time > current_time:
            # Future event: TTL = time until event + buffer after start
            ttl = int(
                (event_time - current_time).total_seconds()
                + self.started_within_hours_ago * 3600
            )
        else:
            # Past event: TTL = remaining time in the window
            ttl = int(
                (
                    event_time
                    + timedelta(hours=self.started_within_hours_ago)
                    - current_time
                ).total_seconds()
            )

        # Ensure minimum TTL of 1 hour
        ttl = max(3600, ttl)

        await REDIS_ASYNC_CLIENT.set(event_key, event_json, ex=ttl)
        await REDIS_ASYNC_CLIENT.zadd(
            "events:all", {event_key: event.event_start_timestamp}
        )
        for genre in event.genres:
            await REDIS_ASYNC_CLIENT.zadd(
                f"events:genre:{genre}", {event_key: event.event_start_timestamp}
            )

    async def parse_and_cache_schedule(self) -> List[MediaFusionEventsMetaData]:
        """Parse schedule and cache it in Redis"""
        schedule_data = await self.fetch_and_parse_schedule()
        if not schedule_data:
            return []

        events_metadata = []
        processed_count = 0
        skipped_count = 0

        for date_section, sports in schedule_data.items():
            self._category_max_times = {}

            for sport, events in sports.items():
                mapped_category = self.category_map.get(sport, "Other Sports")
                for event in events:
                    metadata = self.create_event_metadata(
                        event, date_section, mapped_category
                    )
                    if not metadata:
                        skipped_count += 1
                        continue

                    metadata.streams = await self.get_event_streams(event, metadata.id)
                    await self.cache_event(metadata)

                    metadata.poster = (
                        f"{settings.poster_host_url}/poster/events/{metadata.id}.jpg"
                    )
                    events_metadata.append(metadata)
                    processed_count += 1

        logging.info(
            f"Processed {processed_count} events, skipped {skipped_count} events outside time window"
        )

        # Update last scrape time after successful processing
        await self.update_last_scrape_time()
        return events_metadata

    async def get_scheduled_events(
        self,
        force_refresh: bool = False,
        genre: Optional[str] = None,
        skip: int = 0,
        limit: int = 25,
    ) -> List[MediaFusionEventsMetaData]:
        """Get scheduled events with pagination and genre filtering"""
        events = []
        current_time = datetime.now(tz=self.gmt)
        min_time = int(
            (current_time - timedelta(hours=self.started_within_hours_ago)).timestamp()
        )
        max_time = int(
            (current_time + timedelta(hours=self.start_within_next_hours)).timestamp()
        )

        # Check if we need to refresh based on last scrape time
        should_refresh = await self.should_refresh_schedule()

        if not force_refresh and not should_refresh:
            cache_key = f"events:all" if not genre else f"events:genre:{genre}"
            event_keys = await REDIS_ASYNC_CLIENT.zrevrangebyscore(
                cache_key, max_time, min_time, start=skip, num=limit
            )

            if not event_keys and genre:
                logging.info(
                    "No events found in cache for the specified time window, pagination or genre"
                )
                return []

            logging.info(f"Found {len(event_keys)} events in cache")
            for event_key in event_keys:
                event_json = await REDIS_ASYNC_CLIENT.get(event_key)
                if event_json:
                    event = MediaFusionEventsMetaData.model_validate_json(event_json)
                    event.poster = (
                        f"{settings.poster_host_url}/poster/events/{event.id}.jpg"
                    )
                    event.description = f"🎬 {event.title} - ⏰ {self.format_event_time(event.event_start_timestamp)}"
                    events.append(event)
                else:
                    await REDIS_ASYNC_CLIENT.zrem(cache_key, event_key)

            if events or genre:
                # return cached events if found or genre is specified
                return events

        # If we need to refresh or don't have enough cached events, fetch new data
        all_events = await self.parse_and_cache_schedule()
        filtered_events = [
            event for event in all_events if not genre or genre in event.genres
        ]
        filtered_events.sort(key=lambda x: x.event_start_timestamp, reverse=True)

        start_idx = min(skip, len(filtered_events))
        end_idx = min(start_idx + limit, len(filtered_events))
        events = filtered_events[start_idx:end_idx]

        for event in events:
            event.poster = f"{settings.poster_host_url}/poster/events/{event.id}.jpg"
            event.description = f"🎬 {event.title} - ⏰ {self.format_event_time(event.event_start_timestamp)}"

        return events


dlhd_schedule_service = DLHDScheduleService()
