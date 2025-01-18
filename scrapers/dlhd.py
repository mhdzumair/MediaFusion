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
        self.m3u8_sd_base_url = config_manager.get_scraper_config(
            self.name, "m3u8_sd_base_url"
        )
        self.referer = config_manager.get_scraper_config(self.name, "referer")
        self.gmt = pytz.timezone("Etc/GMT")
        self.start_within_next_hours = start_within_next_hours
        self.started_within_hours_ago = started_within_hours_ago
        self.category_map = config_manager.get_scraper_config(
            self.name, "category_mapping"
        )

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
        """Create event metadata from schedule data"""
        event_date = date_parser.parse(date_str.split(" - ")[0]).date()
        time = datetime.strptime(event["time"], "%H:%M").time()
        aware_datetime = datetime.combine(event_date, time).replace(tzinfo=self.gmt)

        if not self.is_event_in_timewindow(aware_datetime):
            return None

        event_start_timestamp = int(aware_datetime.timestamp())
        meta_id = self.create_event_id(event["event"])

        return MediaFusionEventsMetaData(
            id=meta_id,
            title=event["event"],
            description="",
            genres=[mapped_category],
            event_start_timestamp=event_start_timestamp,
            poster=random.choice(SPORTS_ARTIFACTS[mapped_category]["poster"]),
            background=random.choice(SPORTS_ARTIFACTS[mapped_category]["background"]),
            logo=random.choice(SPORTS_ARTIFACTS[mapped_category]["logo"]),
            is_add_title_to_poster=True,
            streams=[],
        )

    async def find_tv_channel(
        self, channel_name: str
    ) -> Optional[MediaFusionTVMetaData]:
        """Find TV channel in database"""
        return await MediaFusionTVMetaData.find_one({"title": channel_name})

    def create_stream(
        self, channel_id: str, channel_name: str, meta_id: str, is_sd: bool = False
    ) -> TVStreams:
        """Create a new stream for a channel"""
        if is_sd:
            url = self.m3u8_sd_base_url.format(channel_id=channel_id)
        else:
            url = self.m3u8_base_url.format(channel_id=channel_id)
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

    async def get_event_streams(self, event: dict, meta_id: str) -> list[TVStreams]:
        """Get all available streams for an event"""
        streams = []
        found_streams = 0
        created_streams = 0

        async def process_channel(channel):
            nonlocal found_streams, created_streams
            tv_channel = await self.find_tv_channel(channel["channel_name"])
            if tv_channel:
                channel_streams = await TVStreams.find(
                    {
                        "meta_id": tv_channel.id,
                        "namespaces": "mediafusion",
                        "is_working": True,
                    }
                ).to_list(None)
                found_streams += len(channel_streams)
                streams.extend(channel_streams)
                if not channel_streams:
                    stream = self.create_stream(
                        channel["channel_id"], channel["channel_name"], meta_id
                    )
                    created_streams += 1
                    streams.append(stream)
            else:
                stream = self.create_stream(
                    channel["channel_id"], channel["channel_name"], meta_id
                )
                created_streams += 1
                streams.append(stream)

        # Process main channels
        if "channels" in event:
            for channel in event["channels"]:
                await process_channel(channel)

        # Process SD channels
        if "channels2" in event:
            for channel in event["channels2"]:
                stream = self.create_stream(
                    channel["channel_id"], channel["channel_name"], meta_id, is_sd=True
                )
                created_streams += 1
                streams.append(stream)

        logging.debug(
            "Event %s: Found %s existing streams, created %s new streams",
            meta_id,
            found_streams,
            created_streams,
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

        if not force_refresh:
            cache_key = f"events:all" if not genre else f"events:genre:{genre}"
            event_keys = await REDIS_ASYNC_CLIENT.zrevrangebyscore(
                cache_key, max_time, min_time, start=skip, num=limit
            )

            if event_keys:
                logging.info(f"Found {len(event_keys)} events in cache")
                for event_key in event_keys:
                    event_json = await REDIS_ASYNC_CLIENT.get(event_key)
                    if event_json:
                        event = MediaFusionEventsMetaData.model_validate_json(
                            event_json
                        )
                        event.poster = (
                            f"{settings.poster_host_url}/poster/events/{event.id}.jpg"
                        )
                        event.description = f"🎬 {event.title} - ⏰ {self.format_event_time(event.event_start_timestamp)}"
                        events.append(event)
                    else:
                        await REDIS_ASYNC_CLIENT.zrem(cache_key, event_key)

                if len(events) == limit and not force_refresh:
                    return events

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
