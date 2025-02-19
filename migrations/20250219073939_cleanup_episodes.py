from datetime import datetime, timezone
from typing import List, Optional, Tuple
from dataclasses import dataclass

from beanie import Document, free_fall_migration
from pydantic import BaseModel, Field
import PTT
from tqdm.asyncio import tqdm
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"
)
logger = logging.getLogger(__name__)


class EpisodeFile(BaseModel):
    season_number: int
    episode_number: int
    filename: Optional[str] = None


class TorrentStreams(Document):
    id: str
    meta_id: str
    torrent_name: str
    size: int
    episode_files: List[EpisodeFile] = Field(default_factory=list)
    updated_at: datetime | None = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    class Settings:
        name = "TorrentStreams"


@dataclass
class EpisodeDetail:
    season_number: int
    episode_number: int

    def to_tuple(self) -> Tuple[int, int]:
        return (self.season_number, self.episode_number)

    def to_episode_file(self) -> EpisodeFile:
        return EpisodeFile(
            season_number=self.season_number, episode_number=self.episode_number
        )


class EpisodeProcessor:
    def __init__(self, stream: TorrentStreams):
        self.stream = stream
        self.parsed_title = PTT.parse_title(stream.torrent_name)

    def _parse_seasons_episodes(self) -> Tuple[List[int], List[int]]:
        seasons = self.parsed_title.get("seasons", [])
        episodes = self.parsed_title.get("episodes", [])

        if not seasons and not episodes:
            seasons = [1]

        return seasons, episodes

    def generate_episode_details(self) -> List[EpisodeDetail]:
        seasons, episodes = self._parse_seasons_episodes()

        if len(seasons) == 1 and episodes:
            return [EpisodeDetail(seasons[0], episode) for episode in episodes]
        elif len(seasons) == 1:
            return [EpisodeDetail(seasons[0], 1)]
        elif len(seasons) > 1:
            return [EpisodeDetail(season, 1) for season in seasons]
        elif episodes:
            return [EpisodeDetail(1, episode) for episode in episodes]
        else:
            return [EpisodeDetail(1, 1)]

    def process_episodes(self) -> List[EpisodeFile]:
        existing_episodes = self.stream.episode_files
        episode_details = self.generate_episode_details()

        # Early return if no episode details generated
        if not episode_details:
            return existing_episodes

        exist_any_filename = any(ep.filename for ep in existing_episodes)

        if exist_any_filename:
            return self._process_with_existing_filenames(
                existing_episodes, episode_details
            )
        else:
            return self._process_without_filenames(existing_episodes, episode_details)

    def _process_with_existing_filenames(
        self, existing_episodes: List[EpisodeFile], episode_details: List[EpisodeDetail]
    ) -> List[EpisodeFile]:
        cleaned_episodes = [ep for ep in existing_episodes if ep.filename]
        cleaned_episodes_keys = {
            (ep.season_number, ep.episode_number) for ep in cleaned_episodes
        }

        # Log removed episodes
        removed_episodes = {
            (ep.season_number, ep.episode_number)
            for ep in existing_episodes
            if not ep.filename
        }
        if removed_episodes:
            logger.debug(
                f"Removed empty filename episodes for stream {self.stream.id} "
                f"with {removed_episodes}"
            )

        # Add missing episodes
        new_episodes = [
            detail.to_episode_file()
            for detail in episode_details
            if detail.to_tuple() not in cleaned_episodes_keys
        ]
        cleaned_episodes.extend(new_episodes)
        return cleaned_episodes

    def _process_without_filenames(
        self, existing_episodes: List[EpisodeFile], episode_details: List[EpisodeDetail]
    ) -> List[EpisodeFile]:
        existing_episodes_keys = {
            (ep.season_number, ep.episode_number) for ep in existing_episodes
        }

        new_episodes = [detail.to_episode_file() for detail in episode_details]

        # Log changes
        changes = [
            detail
            for detail in episode_details
            if detail.to_tuple() not in existing_episodes_keys
        ]
        if changes:
            logger.debug(f"Torrent {self.stream.id} Change episode details: {changes}")

        return new_episodes


class Forward:
    @free_fall_migration(document_models=[TorrentStreams])
    async def cleanup_episodes(self, session):
        query = {
            "episode_files": {
                "$elemMatch": {
                    "season_number": {"$exists": True},
                    "$or": [{"filename": None}, {"filename": {"$exists": False}}],
                }
            },
        }

        streams = TorrentStreams.find(query)
        count = await streams.count()
        logger.info(f"Found {count} streams with empty filename episodes")

        with tqdm(total=count, desc="Processing streams") as pbar:
            async for stream in streams:
                try:
                    processor = EpisodeProcessor(stream)
                    cleaned_episodes = processor.process_episodes()

                    # Skip update if no changes
                    if cleaned_episodes == stream.episode_files:
                        pbar.update(1)
                        continue

                    stream.episode_files = cleaned_episodes
                    new_keys = [
                        (ep.season_number, ep.episode_number)
                        for ep in stream.episode_files
                    ]

                    logger.debug(
                        f"Updated episodes for stream {stream.id} '{stream.torrent_name}' "
                        f"with {new_keys}"
                    )

                    stream.updated_at = datetime.now(tz=timezone.utc)
                    await stream.save()

                except Exception as e:
                    logger.error(
                        f"Error processing stream {stream.id}: {str(e)}", exc_info=True
                    )
                    continue

                pbar.update(1)

        logger.info("Migration completed successfully")


class Backward:

    pass
