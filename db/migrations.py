import asyncio
import logging
from datetime import datetime

from PTT import parse_title
from pymongo import ASCENDING

from db import database
from db.crud import update_meta_stream, update_metadata
from db.models import (
    TorrentStreams,
    MediaFusionSeriesMetaData,
    TVStreams,
    MediaFusionMetaData,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def migrate_series_streams():
    """
    Fetch series metadata IDs, retrieve TorrentStreams with empty episode lists,
    parse titles using PTT.parse_title, and create episodes based on this information.
    """
    logger.info("Starting Series Streams Migration...")

    meta_collection = MediaFusionSeriesMetaData.get_motor_collection()
    stream_collection = TorrentStreams.get_motor_collection()

    try:
        # Fetch all series metadata IDs
        series_ids = await meta_collection.find({"type": "series"}).distinct("_id")
        if not series_ids:
            logger.info("No series metadata IDs found.")
            return
        count = await stream_collection.count_documents(
            {"meta_id": {"$in": series_ids}, "episode_files": {"$size": 0}}
        )
        logger.info(f"Found {count} TorrentStreams with empty episode lists.")
        # Find TorrentStreams with empty episode lists
        async for stream in stream_collection.find(
            {"meta_id": {"$in": series_ids}, "episode_files": {"$size": 0}}
        ):
            stream_id = stream["_id"]
            title = stream.get("torrent_name", "")

            # Parse the torrent title
            parsed_data = parse_title(title)
            if not parsed_data:
                logger.warning(f"Failed to parse title for stream ID: {stream_id}")
                continue

            # Create episode details based on parsed data
            seasons = parsed_data.get("seasons")
            episodes = parsed_data.get("episodes")

            if len(seasons) == 1 and episodes:
                episode_details = [
                    {
                        "season_number": seasons[0],
                        "episode_number": episode,
                    }
                    for episode in episodes
                ]
            elif len(seasons) == 1 and not episodes:
                episode_details = [
                    {
                        "season_number": seasons[0],
                        "episode_number": 1,
                    }
                ]
            elif len(seasons) > 1:
                episode_details = [
                    {
                        "season_number": season,
                        "episode_number": 1,
                    }
                    for season in seasons
                ]
            elif episodes:
                episode_details = [
                    {
                        "season_number": 1,
                        "episode_number": episode,
                    }
                    for episode in episodes
                ]
            elif parsed_data.get("date") and stream["meta_id"].startswith("tt"):
                episode_date = datetime.strptime(parsed_data["date"], "%Y-%m-%d")
                metadata = await MediaFusionSeriesMetaData.get(stream["meta_id"])
                episode = [
                    episode
                    for episode in metadata.episodes
                    if episode.released
                    and episode.released.date() == episode_date.date()
                ]
                if not metadata.episodes or (
                    not episode and metadata.episodes[0].thumbnail
                ):
                    await update_metadata([metadata.id], "series")
                    metadata = await MediaFusionSeriesMetaData.get(stream["meta_id"])
                    episode = [
                        episode
                        for episode in metadata.episodes
                        if episode.released
                        and episode.released.date() == episode_date.date()
                    ]
                if not episode:
                    logger.warning(
                        f"No episode found by {episode_date.date()} date for {parsed_data.get('title')} ({metadata.id}), {stream_id}"
                    )
                    continue
                imdb_episode = episode[0]

                logger.info(
                    f"Episode found by {episode_date} date for {parsed_data.get('title')} ({metadata.id})"
                )
                episode_details = [
                    {
                        "season_number": imdb_episode.season_number,
                        "episode_number": imdb_episode.episode_number,
                        "released": imdb_episode.released,
                        "title": imdb_episode.title,
                    }
                ]
            elif stream["meta_id"].startswith("tt"):
                episode_details = [
                    {
                        "season_number": 1,
                        "episode_number": 1,
                    }
                ]
            else:
                logger.warning(
                    f"Failed to identify episode details for stream ID: {stream_id}. Deleting stream '{title}'"
                )
                await TorrentStreams.find({"_id": stream_id}).delete()
                continue

            # Update the TorrentStream with new episode details
            await stream_collection.update_one(
                {"_id": stream_id},
                {"$set": {"episode_files": [episode_details]}},
            )

            logger.info(f"Updated stream ID: {stream_id} with parsed episode details.")

        logger.info("Series Streams Migration completed successfully.")
    except Exception as e:
        logger.error(f"Error during Series Streams Migration: {e}")
        raise


async def cleanup_duplicate_metadata():
    """
    Migration script to clean up duplicate metadata and update related stream records.
    Modified version for standalone MongoDB (no transactions).
    """
    logging.info("Starting duplicate metadata cleanup migration")

    # Find all duplicate metadata with mf prefix
    pipeline = [
        {"$match": {"_id": {"$regex": "^mf"}}},
        {
            "$group": {
                "_id": {"title": "$title", "year": "$year", "type": "$type"},
                "count": {"$sum": 1},
                "docs": {
                    "$push": {
                        "id": "$_id",
                        "total_streams": {"$ifNull": ["$total_streams", 0]},
                        "last_updated_at": "$last_updated_at",
                    }
                },
            }
        },
        {"$match": {"count": {"$gt": 1}}},
    ]

    duplicate_groups = (
        await MediaFusionMetaData.get_motor_collection()
        .aggregate(pipeline)
        .to_list(None)
    )

    logging.info(f"Found {len(duplicate_groups)} groups of duplicates")

    for group in duplicate_groups:
        group_key = group["_id"]
        docs = group["docs"]

        # Sort by total_streams (desc) and last_updated_at (desc)
        sorted_docs = sorted(
            docs,
            key=lambda x: (
                x.get("total_streams", 0),
                x.get("last_updated_at", datetime.min),
            ),
            reverse=True,
        )

        # Keep the first one (most streams/latest update)
        keep_id = sorted_docs[0]["id"]
        remove_ids = [doc["id"] for doc in sorted_docs[1:]]

        logging.info(f"Processing group: {group_key}")
        logging.info(f"Keeping: {keep_id}")
        logging.info(f"Removing: {remove_ids}")

        # Update streams based on content type
        content_type = group_key["type"]

        try:
            # Update TorrentStreams for movies and series
            if content_type in ["movie", "series"]:
                result = await TorrentStreams.get_motor_collection().update_many(
                    {"meta_id": {"$in": remove_ids}}, {"$set": {"meta_id": keep_id}}
                )
                logging.info(f"Updated {result.modified_count} torrent streams")

            # Update TVStreams for tv content
            elif content_type == "tv":
                result = await TVStreams.get_motor_collection().update_many(
                    {"meta_id": {"$in": remove_ids}}, {"$set": {"meta_id": keep_id}}
                )
                logging.info(f"Updated {result.modified_count} TV streams")

            # Update streams count for the kept document
            update_data = await update_meta_stream(keep_id)

            # Remove duplicate metadata last
            result = await MediaFusionMetaData.get_motor_collection().delete_many(
                {"_id": {"$in": remove_ids}}
            )
            logging.info(f"Removed {result.deleted_count} duplicate metadata records")

            logging.info(
                f"Updated stream count for {keep_id}: {update_data['total_streams']}"
            )

        except Exception as e:
            logging.error(f"Error processing group {group_key}: {str(e)}")
            continue


async def migrate_custom_flag():
    """Migration script to set is_custom field for existing documents."""
    # Update all documents with mf prefix to have is_custom = True
    await MediaFusionMetaData.get_motor_collection().update_many(
        {"_id": {"$regex": "^mf"}}, {"$set": {"is_custom": True}}
    )

    # Update all other documents to have is_custom = False
    await MediaFusionMetaData.get_motor_collection().update_many(
        {"_id": {"$not": {"$regex": "^mf"}}}, {"$set": {"is_custom": False}}
    )
    await MediaFusionMetaData.get_motor_collection().create_index(
        [("title", ASCENDING), ("year", ASCENDING), ("type", ASCENDING)],
        unique=True,
        partialFilterExpression={"is_custom": True},
        name="unique_title_year_type_for_mf_id",
    )
    logging.info("Migration completed: is_custom field has been set for all documents")


async def main():
    logger.info("Starting migration")
    await database.init()  # allow_index_dropping=True)
    # await cleanup_duplicate_metadata()
    # await migrate_custom_flag()

    await migrate_series_streams()
    logger.info("Migration completed successfully")


if __name__ == "__main__":
    asyncio.run(main())
