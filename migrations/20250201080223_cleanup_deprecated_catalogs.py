from typing import List

from beanie import Document, free_fall_migration
from pydantic import Field

SUPPORTED_CATALOGS = {
    "american_football",
    "arabic_movies",
    "arabic_series",
    "baseball",
    "basketball",
    "contribution_stream",
    "english_hdrip",
    "english_series",
    "english_tcrip",
    "fighting",
    "football",
    "formula_racing",
    "hindi_dubbed",
    "hindi_hdrip",
    "hindi_old",
    "hindi_series",
    "hindi_tcrip",
    "hockey",
    "kannada_dubbed",
    "kannada_hdrip",
    "kannada_old",
    "kannada_series",
    "kannada_tcrip",
    "live_sport_events",
    "live_tv",
    "malayalam_dubbed",
    "malayalam_hdrip",
    "malayalam_old",
    "malayalam_series",
    "malayalam_tcrip",
    "mediafusion_search_movies",
    "mediafusion_search_series",
    "mediafusion_search_tv",
    "motogp_racing",
    "other_sports",
    "prowlarr_movies",
    "prowlarr_series",
    "rugby",
    "tamil_dubbed",
    "tamil_hdrip",
    "tamil_old",
    "tamil_series",
    "tamil_tcrip",
    "telugu_dubbed",
    "telugu_hdrip",
    "telugu_old",
    "telugu_series",
    "telugu_tcrip",
    "tgx_movie",
    "tgx_series",
}


class TorrentStreams(Document):
    meta_id: str
    catalog: List[str] = Field(default_factory=list)

    class Settings:
        name = "TorrentStreams"


class MediaFusionMetaData(Document):
    catalogs: List[str] = Field(default_factory=list)

    class Settings:
        name = "MediaFusionMetaData"


class Forward:
    @free_fall_migration(document_models=[TorrentStreams, MediaFusionMetaData])
    async def cleanup_catalogs(self, session):
        """Clean up deprecated catalogs and fix catalog field name"""
        all_catalogs = await TorrentStreams.distinct("catalog")
        print(f"Found {len(all_catalogs)} catalogs in torrent streams")
        deprecated_catalogs = set(all_catalogs) - SUPPORTED_CATALOGS
        torrent_collection = TorrentStreams.get_motor_collection()
        metadata_collection = MediaFusionMetaData.get_motor_collection()

        print(f"Found {len(deprecated_catalogs)} deprecated catalogs. Cleaning up...")

        # Step 1: Remove deprecated catalogs from metadata
        await metadata_collection.update_many(
            {"catalogs": {"$in": list(deprecated_catalogs)}},
            {"$pull": {"catalogs": {"$in": list(deprecated_catalogs)}}},
        )

        # Step 2: Remove deprecated catalogs from torrent streams
        await torrent_collection.update_many(
            {"catalog": {"$in": list(deprecated_catalogs)}},
            {"$pull": {"catalog": {"$in": list(deprecated_catalogs)}}},
        )

        print("Cleanup completed successfully")


class Backward:
    pass
