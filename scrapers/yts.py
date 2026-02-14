from datetime import timedelta
from typing import Any

import PTT

from db.schemas import MetadataData, TorrentStreamData
from scrapers.base_scraper import BaseScraper
from utils.runtime_const import YTS_SEARCH_TTL


class YTSScraper(BaseScraper):
    cache_key_prefix = "yts"
    yts_url = "https://yts.lt"

    def __init__(self):
        super().__init__(cache_key_prefix=self.cache_key_prefix, logger_name=__name__)

    @BaseScraper.cache(ttl=YTS_SEARCH_TTL)
    @BaseScraper.rate_limit(calls=2, period=timedelta(seconds=1))
    async def _scrape_and_parse(
        self,
        user_data,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[TorrentStreamData]:
        # YTS is only for movies
        if catalog_type != "movie":
            self.metrics.record_skip("Not a movie")
            return []

        # YTS requires IMDb ID
        imdb_id = metadata.get_imdb_id()
        if not imdb_id:
            self.metrics.record_skip("No IMDb ID available")
            self.logger.debug(f"Skipping YTS for {metadata.title} - no IMDb ID")
            return []

        try:
            response = await self.make_request(
                f"{self.yts_url}/api/v2/movie_details.json",
                params={"imdb_id": imdb_id},
                timeout=15,
            )

            data = response.json()

            if not self.validate_response(data):
                self.metrics.record_error("invalid_response")
                return []

            movie = data.get("data", {}).get("movie")
            if not movie:
                self.metrics.record_skip("Movie not found")
                return []

            torrents = movie.get("torrents", [])
            if not torrents:
                self.metrics.record_skip("No torrents found")
                return []

            self.metrics.record_found_items(len(torrents))
            self.logger.info(
                f"Found {len(torrents)} torrents for {metadata.title} ({metadata.year}) with IMDB ID {imdb_id}"
            )

            return await self.parse_movie_torrents(
                processed_info_hashes=set(),
                metadata=metadata,
                movie=movie,
            )

        except Exception as e:
            self.metrics.record_error("movie_search_error")
            self.logger.error(f"Error searching movie: {e}")
            return []

    def validate_response(self, response: dict[str, Any]) -> bool:
        return (
            isinstance(response, dict)
            and response.get("status") == "ok"
            and "data" in response
            and isinstance(response["data"], dict)
        )

    async def parse_movie_torrents(
        self,
        processed_info_hashes: set[str],
        metadata: MetadataData,
        movie: dict[str, Any],
    ) -> list[TorrentStreamData]:
        streams = []

        for torrent in movie.get("torrents", []):
            try:
                stream = await self.process_torrent(movie, torrent, metadata, processed_info_hashes)
                if stream:
                    streams.append(stream)

            except Exception as e:
                self.metrics.record_error("torrent_processing_error")
                self.logger.exception(f"Error processing torrent: {e}")
                continue

        return streams

    async def process_torrent(
        self,
        movie: dict[str, Any],
        torrent: dict[str, Any],
        metadata: MetadataData,
        processed_info_hashes: set[str],
    ) -> TorrentStreamData | None:
        try:
            # Skip if we've already processed this info hash
            info_hash = torrent.get("hash", "").lower()
            if not info_hash or info_hash in processed_info_hashes:
                self.metrics.record_skip("Duplicate info_hash")
                return None

            # Construct a standardized torrent name
            torrent_title = (
                f"{movie['title']} ({movie['year']}) "
                f"{torrent['quality']} {torrent.get('type', '')} {torrent['video_codec']} "
                f"{torrent['audio_channels']} ({torrent['size']}) [{movie['language']}] YTS"
            ).strip()

            # Construct a standardized torrent name
            parsed_data = {
                "title": movie.get("title"),
                "year": movie.get("year"),
            }
            if not self.validate_title_and_year(
                parsed_data,
                metadata,
                "movie",
                torrent_title,
            ):
                return None

            # Parse using PTT for consistent quality attributes
            ptt_parsed = PTT.parse_title(torrent_title, True)

            stream = TorrentStreamData(
                info_hash=info_hash,
                meta_id=metadata.get_canonical_id(),
                name=torrent_title,
                size=int(torrent.get("size_bytes", 0)),
                source="YTS",
                seeders=int(torrent.get("seeds", 0)),
                announce_list=[],
                created_at=torrent.get("date_uploaded"),
                # Single-value quality attributes
                resolution=torrent["quality"],
                codec=torrent.get("codec") or ptt_parsed.get("codec"),
                quality=torrent["quality"],
                bit_depth=ptt_parsed.get("bit_depth"),
                release_group=ptt_parsed.get("group"),
                # Multi-value quality attributes
                audio_formats=ptt_parsed.get("audio", []) if isinstance(ptt_parsed.get("audio"), list) else [],
                channels=ptt_parsed.get("channels", []) if isinstance(ptt_parsed.get("channels"), list) else [],
                hdr_formats=ptt_parsed.get("hdr", []) if isinstance(ptt_parsed.get("hdr"), list) else [],
                languages=PTT.parse.translate_langs([movie.get("language")]),
            )

            # Record metrics
            self.metrics.record_processed_item()
            self.metrics.record_quality(stream.quality)
            self.metrics.record_source(stream.source)

            processed_info_hashes.add(info_hash)
            return stream

        except Exception as e:
            self.metrics.record_error("stream_processing_error")
            self.logger.exception(f"Error creating stream: {e}")
            return None
