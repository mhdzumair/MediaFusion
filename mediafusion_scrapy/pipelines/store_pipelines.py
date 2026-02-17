import asyncio
import hashlib
import logging

from scrapy import signals
from scrapy.exceptions import DropItem
from sqlmodel import select

from db import crud
from db.database import get_async_session
from db.enums import MediaType
from db.models import Media
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import TVMetaData


class QueueBasedPipeline:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.processing_task = None

    async def init(self):
        self.processing_task = asyncio.create_task(self.process_queue())

    async def close(self):
        await self.queue.join()
        self.processing_task.cancel()

    @classmethod
    def from_crawler(cls, crawler):
        p = cls()
        crawler.signals.connect(p.init, signal=signals.spider_opened)
        crawler.signals.connect(p.close, signal=signals.spider_closed)
        return p

    async def process_item(self, item):
        # Instead of processing the item directly, add it to the queue
        await self.queue.put(item)
        return item

    async def process_queue(self):
        logging.info("Starting processing queue")
        while True:
            item = await self.queue.get()
            try:
                await self.parse_item(item)
            except Exception as e:
                logging.error(f"Error processing item: {e}", exc_info=True)
            finally:
                self.queue.task_done()

    async def parse_item(self, item):
        raise NotImplementedError


class EventSeriesStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = REDIS_ASYNC_CLIENT

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def parse_item(self, item):
        if "title" not in item:
            logging.warning(f"title not found in item: {item}")
            raise DropItem(f"title not found in item: {item}")

        async for session in get_async_session():
            title_key = f"{item['title']}_{item['year']}"
            prefix = item.get("catalog", ["event"])[0]
            stable_id = f"{prefix}:{hashlib.md5(title_key.encode()).hexdigest()[:16]}"

            metadata = {
                "id": stable_id,
                "title": item["title"],
                "year": item["year"],
                "poster": item.get("poster"),
                "background": item.get("background"),
                "is_poster_working": bool(item.get("poster")),
                "is_add_title_to_poster": item.get("is_add_title_to_poster", False),
            }

            series_result = await crud.get_or_create_metadata(session, metadata, "series", is_search_imdb_title=False)

            if not series_result:
                raise DropItem(f"Failed to create series metadata for: {item['title']}")

            meta_id = stable_id
            logging.info("Using series %s with id %s (db pk %s)", item["title"], meta_id, series_result.id)

            # Check if torrent stream exists
            existing_stream = await crud.get_stream_by_info_hash(session, item["info_hash"])

            if not existing_stream:
                # Prepare episode files (items may be dicts or StreamFileData objects)
                episode_files = []
                if item.get("episodes"):
                    for ep in item["episodes"]:
                        if isinstance(ep, dict):
                            episode_files.append(
                                {
                                    "season_number": ep.get("season_number", 1),
                                    "episode_number": ep.get("episode_number", 1),
                                    "filename": ep.get("filename"),
                                    "size": ep.get("size"),
                                    "file_index": ep.get("file_index"),
                                    "episode_title": ep.get("episode_title"),
                                }
                            )
                        else:
                            episode_files.append(
                                {
                                    "season_number": getattr(ep, "season_number", 1),
                                    "episode_number": getattr(ep, "episode_number", 1),
                                    "filename": getattr(ep, "filename", ""),
                                    "size": getattr(ep, "size", 0),
                                    "file_index": getattr(ep, "file_index", 0),
                                    "episode_title": getattr(ep, "episode_title", None),
                                }
                            )

                # Create torrent stream
                stream_data = {
                    "id": item["info_hash"],
                    "meta_id": meta_id,
                    "torrent_name": item["torrent_name"],
                    "announce_urls": item.get("announce_list", []),
                    "size": item["total_size"],
                    "languages": item.get("languages", []),
                    "resolution": item.get("resolution"),
                    "codec": item.get("codec"),
                    "quality": item.get("quality"),
                    "audio": item.get("audio"),
                    "hdr": item.get("hdr"),
                    "source": item["source"],
                    "uploader": item.get("uploader"),
                    "catalogs": item.get("catalog", []),
                    "created_at": item.get("created_at"),
                    "seeders": item.get("seeders"),
                    "files": episode_files,
                }

                await crud.store_new_torrent_streams(session, [stream_data])
                logging.info(
                    "Added torrent stream %s for series %s",
                    item["torrent_name"],
                    item["title"],
                )

            await session.commit()

        await self.redis.sadd(item["scraped_info_hash_key"], item["info_hash"])

        return item


class TVStorePipeline(QueueBasedPipeline):
    async def parse_item(self, item):
        if "title" not in item:
            logging.warning(f"title not found in item: {item}")
            raise DropItem(f"title not found in item: {item}")

        tv_metadata = TVMetaData.model_validate(item)
        async for session in get_async_session():
            await crud.save_tv_channel_metadata(session, tv_metadata)
        return item


class MovieStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = REDIS_ASYNC_CLIENT
        # Cache of (title_lower, year) -> media_id for items without external IDs.
        # Prevents duplicate media creation when multiple torrents for the same
        # movie are processed concurrently in the queue.
        self._title_media_cache: dict[tuple[str, int | None], int] = {}

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def _get_or_create_media(self, session, item):
        """Get or create media, handling items with or without external IDs."""
        imdb_id = item.get("imdb_id")
        if imdb_id:
            metadata = {
                "id": imdb_id,
                "title": item["title"],
                "year": item.get("year"),
                "poster": item.get("poster"),
                "background": item.get("background"),
                "is_add_title_to_poster": item.get("is_add_title_to_poster", False),
                "catalogs": item.get("catalog", []),
            }
            return await crud.get_or_create_metadata(
                session,
                metadata,
                "movie",
                is_search_imdb_title=item.get("is_search_imdb_title", True),
            )

        # No external ID — check in-memory cache first to avoid race-condition
        # duplicates when multiple torrents for the same title arrive concurrently.
        cache_key = (item["title"].lower(), item.get("year"))
        cached_id = self._title_media_cache.get(cache_key)
        if cached_id:
            result = await session.exec(select(Media).where(Media.id == cached_id))
            media = result.first()
            if media:
                return media

        # Search by title/year in the database
        media = await crud.get_media_by_title_year(session, item["title"], item.get("year"), MediaType.MOVIE)
        if media:
            self._title_media_cache[cache_key] = media.id
            return media

        # Create new media with a synthetic mf external ID
        metadata = {
            "title": item["title"],
            "year": item.get("year"),
            "poster": item.get("poster"),
            "background": item.get("background"),
            "is_add_title_to_poster": item.get("is_add_title_to_poster", False),
            "catalogs": item.get("catalog", []),
        }
        metadata["id"] = f"mf_tmp_{item['title']}_{item.get('year', 'unknown')}"
        media = await crud.get_or_create_metadata(session, metadata, "movie", is_search_imdb_title=False)
        if media:
            self._title_media_cache[cache_key] = media.id
        return media

    async def parse_item(self, item):
        if "title" not in item:
            return item

        if item.get("type") != "movie":
            return item

        async for session in get_async_session():
            media = await self._get_or_create_media(session, item)

            if not media:
                raise DropItem(f"Failed to create movie metadata for: {item['title']}")

            meta_id = f"mf:{media.id}"
            logging.info("Using movie %s with id %s", item["title"], meta_id)

            # Apply full provider metadata (description, cast, crew, genres,
            # certification, etc.) if available from MetadataSearchPipeline.
            # This avoids needing a separate "Refresh All" step later.
            provider_metadata = item.get("_provider_metadata")
            if provider_metadata:
                try:
                    # update_provider_metadata creates MediaExternalID rows and
                    # ProviderMetadata records for each provider (IMDB, TMDB, etc.)
                    for provider_name, data in provider_metadata.items():
                        if data:
                            await crud.update_provider_metadata(session, media.id, provider_name, data)
                    # apply_multi_provider_metadata updates the canonical Media fields
                    # (description, cast, crew, genres, images, etc.)
                    await crud.apply_multi_provider_metadata(session, media.id, provider_metadata, "movie")
                    logging.info("Applied full metadata for movie %s", item["title"])
                except Exception as e:
                    logging.warning("Failed to apply full metadata for %s: %s", item["title"], e)

            # Check if torrent stream already exists
            existing_stream = await crud.get_stream_by_info_hash(session, item["info_hash"])
            if existing_stream:
                logging.info("Torrent stream already exists: %s", item["torrent_name"])
                await session.commit()
                return item

            stream_data = {
                "id": item["info_hash"],
                "meta_id": meta_id,
                "torrent_name": item["torrent_name"],
                "announce_urls": item.get("announce_list", []),
                "size": item.get("total_size", 0),
                "total_size": item.get("total_size", 0),
                "languages": item.get("languages", []),
                "resolution": item.get("resolution"),
                "codec": item.get("codec"),
                "quality": item.get("quality"),
                "audio": item.get("audio"),
                "hdr": item.get("hdr"),
                "source": item.get("source", ""),
                "uploader": item.get("uploader"),
                "catalogs": item.get("catalog", []),
                "created_at": item.get("created_at"),
                "seeders": item.get("seeders"),
                "torrent_file": item.get("torrent_file"),
                "files": item.get("file_data", []),
            }

            await crud.store_new_torrent_streams(session, [stream_data])
            await session.commit()
            logging.info("Added torrent stream %s for movie %s", item["torrent_name"], item["title"])

        return item


class SeriesStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = REDIS_ASYNC_CLIENT
        self._title_media_cache: dict[tuple[str, int | None], int] = {}

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def _get_or_create_media(self, session, item):
        """Get or create media, handling items with or without external IDs."""
        imdb_id = item.get("imdb_id")
        if imdb_id:
            metadata = {
                "id": imdb_id,
                "title": item["title"],
                "year": item.get("year"),
                "poster": item.get("poster"),
                "background": item.get("background"),
                "is_add_title_to_poster": item.get("is_add_title_to_poster", False),
                "catalogs": item.get("catalog", []),
            }
            return await crud.get_or_create_metadata(
                session,
                metadata,
                "series",
                is_search_imdb_title=item.get("is_search_imdb_title", True),
            )

        cache_key = (item["title"].lower(), item.get("year"))
        cached_id = self._title_media_cache.get(cache_key)
        if cached_id:
            result = await session.exec(select(Media).where(Media.id == cached_id))
            media = result.first()
            if media:
                return media

        media = await crud.get_media_by_title_year(session, item["title"], item.get("year"), MediaType.SERIES)
        if media:
            self._title_media_cache[cache_key] = media.id
            return media

        metadata = {
            "title": item["title"],
            "year": item.get("year"),
            "poster": item.get("poster"),
            "background": item.get("background"),
            "is_add_title_to_poster": item.get("is_add_title_to_poster", False),
            "catalogs": item.get("catalog", []),
        }
        metadata["id"] = f"mf_tmp_{item['title']}_{item.get('year', 'unknown')}"
        media = await crud.get_or_create_metadata(session, metadata, "series", is_search_imdb_title=False)
        if media:
            self._title_media_cache[cache_key] = media.id
        return media

    async def parse_item(self, item):
        if "title" not in item:
            return item

        if item.get("type") != "series":
            return item

        async for session in get_async_session():
            media = await self._get_or_create_media(session, item)

            if not media:
                raise DropItem(f"Failed to create series metadata for: {item['title']}")

            meta_id = f"mf:{media.id}"
            logging.info("Using series %s with id %s", item["title"], meta_id)

            # Apply full provider metadata (description, cast, crew, genres,
            # certification, etc.) if available from MetadataSearchPipeline.
            provider_metadata = item.get("_provider_metadata")
            if provider_metadata:
                try:
                    for provider_name, data in provider_metadata.items():
                        if data:
                            await crud.update_provider_metadata(session, media.id, provider_name, data)
                    await crud.apply_multi_provider_metadata(session, media.id, provider_metadata, "series")
                    logging.info("Applied full metadata for series %s", item["title"])
                except Exception as e:
                    logging.warning("Failed to apply full metadata for %s: %s", item["title"], e)

            # Check if torrent stream already exists
            existing_stream = await crud.get_stream_by_info_hash(session, item["info_hash"])
            if existing_stream:
                logging.info("Torrent stream already exists: %s", item["torrent_name"])
                await session.commit()
                return item

            stream_data = {
                "id": item["info_hash"],
                "meta_id": meta_id,
                "torrent_name": item["torrent_name"],
                "announce_urls": item.get("announce_list", []),
                "size": item.get("total_size", 0),
                "total_size": item.get("total_size", 0),
                "languages": item.get("languages", []),
                "resolution": item.get("resolution"),
                "codec": item.get("codec"),
                "quality": item.get("quality"),
                "audio": item.get("audio"),
                "hdr": item.get("hdr"),
                "source": item.get("source", ""),
                "uploader": item.get("uploader"),
                "catalogs": item.get("catalog", []),
                "created_at": item.get("created_at"),
                "seeders": item.get("seeders"),
                "torrent_file": item.get("torrent_file"),
                "files": item.get("file_data", []),
                "season": item.get("seasons", [None])[0] if item.get("seasons") else None,
                "episode": item.get("episodes", [None])[0] if item.get("episodes") else None,
            }

            await crud.store_new_torrent_streams(session, [stream_data])
            await session.commit()
            logging.info("Added torrent stream %s for series %s", item["torrent_name"], item["title"])

        if "scraped_info_hash_key" in item:
            await self.redis.sadd(item["scraped_info_hash_key"], item["info_hash"])
        return item


class LiveEventStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = REDIS_ASYNC_CLIENT

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def parse_item(self, item):
        if "title" not in item:
            raise DropItem(f"name not found in item: {item}")

        async for session in get_async_session():
            await crud.save_events_data(session, item)
        if "scraped_info_hash_key" in item:
            await self.redis.sadd(item["scraped_info_hash_key"], item["info_hash"])
        return item
