"""
Database CRUD operations package.

This module provides organized access to CRUD operations by domain.

Usage:
    from db import crud
    media = await crud.get_media_by_id(session, 1)
    await crud.add_media_image(session, media_id=1, provider_id=1, ...)
"""

# Base query builders
from db.crud.base import (
    CatalogBaseQueryBuilder,
    CatalogQueryBuilder,
    SearchQueryBuilder,
)

# Catalog operations (Stremio API)
from db.crud.catalog import (
    get_catalog_meta_list,
    get_mdblist_meta_list,
    search_metadata,
)

# Contribution operations (voting, suggestions)
from db.crud.contributions import (
    # Contributions
    create_contribution,
    # Suggestions
    create_metadata_suggestion,
    create_stream_suggestion,
    get_contribution,
    # Settings
    get_contribution_settings,
    get_pending_contributions,
    get_pending_metadata_suggestions,
    get_stream_vote_count,
    get_user_contributions,
    get_user_stream_vote,
    remove_stream_vote,
    update_contribution_settings,
    update_contribution_status,
    # Metadata votes
    vote_on_metadata,
    # Stream votes
    vote_on_stream,
)

# Media operations
from db.crud.media import (
    add_aka_title,
    add_catalogs_to_media,
    add_external_id,
    # Media relationship links
    add_genres_to_media,
    add_media_image,
    create_episode,
    create_media,
    create_movie_metadata,
    create_season,
    create_series_metadata,
    decrement_stream_count,
    delete_media,
    get_all_external_ids_batch,
    get_all_external_ids_dict,
    # External ID functions (with Redis caching)
    get_canonical_external_id,
    get_canonical_external_ids_batch,
    # Episode CRUD
    get_episode,
    get_episodes_for_season,
    get_media_by_external_id,
    get_media_by_external_id_full,
    # Media CRUD
    get_media_by_id,
    get_media_by_title_year,
    get_media_count,
    # Multi-provider images
    get_media_images,
    # Multi-provider ratings
    get_media_ratings,
    # Comprehensive loaders
    get_media_with_all_relations,
    get_mediafusion_rating,
    get_metadata_counts,
    get_metadata_provider,
    # Movie metadata
    get_movie_metadata,
    get_or_create_episode,
    get_or_create_metadata_provider,
    get_or_create_season,
    get_primary_image,
    get_rating_by_provider,
    # Provider CRUD
    get_rating_provider,
    # Season CRUD
    get_season,
    get_seasons_for_series,
    # Series metadata
    get_series_metadata,
    increment_stream_count,
    invalidate_external_id_cache,
    resolve_external_id,
    search_media,
    update_media,
    update_mediafusion_rating,
    upsert_media_rating,
)
from db.crud.providers import (
    add_media_image as providers_add_media_image,
)

# Provider operations (MetadataProvider, RatingProvider, Images, Ratings)
from db.crud.providers import (
    add_or_update_rating,
    apply_multi_provider_metadata,
    create_provider,
    create_rating_provider,
    get_all_providers,
    get_all_rating_providers,
    # Images
    get_images_for_media,
    get_or_create_provider,
    get_or_create_rating_provider,
    # Metadata providers
    get_provider_by_id,
    get_provider_by_name,
    get_provider_metadata,
    # Rating providers
    get_rating_provider_by_name,
    # Ratings
    get_ratings_for_media,
    set_primary_image,
    # Provider metadata
    store_provider_metadata,
    update_provider_metadata,
    vote_on_media,
)
from db.crud.providers import (
    get_mediafusion_rating as providers_get_mediafusion_rating,
)

# Reference data operations (Genre, Catalog, Language, etc.)
from db.crud.reference import (
    get_all_genres_by_type,
    get_genres,
    get_or_create_audio_channel,
    get_or_create_audio_format,
    get_or_create_catalog,
    get_or_create_genre,
    get_or_create_hdr_format,
    get_or_create_language,
    get_or_create_parental_certificate,
)

# Scraper helper operations
from db.crud.scraper_helpers import (
    block_torrent_stream,
    bulk_update_user_rss_feed_status,
    create_user_rss_feed,
    delete_metadata,
    delete_torrent_stream,
    delete_tv_stream,
    delete_user_rss_feed,
    # Scheduler helpers
    fetch_last_run,
    find_tv_channel_by_title,
    get_all_tv_streams_paginated,
    # Metadata helpers
    get_metadata_by_id,
    get_movie_data_by_id,
    get_or_create_metadata,
    get_series_data_by_id,
    # Stream helpers
    get_stream_by_info_hash,
    get_tv_data_by_id,
    get_tv_metadata_not_working_posters,
    get_tv_streams_by_meta_id,
    get_user_rss_feed,
    get_user_rss_feed_by_url,
    list_all_active_user_rss_feeds,
    list_all_user_rss_feeds_with_users,
    list_user_rss_feeds,
    migrate_torrent_streams,
    # TV helpers
    save_tv_channel_metadata,
    store_new_torrent_streams,
    store_new_usenet_streams,
    update_meta_stream,
    update_metadata,
    update_single_imdb_metadata,
    update_tv_metadata_poster,
    update_tv_stream_status,
    # RSS helpers
    update_user_rss_feed,
    update_user_rss_feed_by_uuid,
    update_user_rss_feed_metrics,
)

# Stream service operations (Stremio API)
from db.crud.stream_services import (
    get_event_streams,
    get_movie_streams,
    get_series_streams,
    get_tv_streams_formatted,
)

# Stream operations
from db.crud.streams import (
    # Stream files (NEW in v5 - replaces TorrentFile)
    add_files_to_stream,
    # Language links
    add_languages_to_stream,
    # AceStream streams
    create_acestream_stream,
    # External link streams
    create_external_link_stream,
    # HTTP streams
    create_http_stream,
    create_torrent_stream,
    create_tracker,
    # Usenet streams
    create_usenet_stream,
    # YouTube streams
    create_youtube_stream,
    delete_file_media_links_for_stream,
    delete_files_for_stream,
    delete_stream,
    delete_torrent_by_info_hash,
    delete_usenet_stream_by_guid,
    # AceStream lookups
    get_acestream_by_content_id,
    get_acestream_by_identifier,
    get_acestream_by_info_hash,
    get_file_media_links_for_stream,
    get_files_for_episode,
    get_files_for_stream,
    get_http_stream_by_url_for_media,
    get_or_create_tracker,
    # Base stream
    get_stream_by_id,
    get_streams_for_media,
    # Torrent streams
    get_torrent_by_info_hash,
    # Metrics
    get_torrent_count,
    get_torrents_by_source,
    get_torrents_by_uploader,
    # Trackers
    get_tracker_by_url,
    # Usenet streams
    get_usenet_stream_by_guid,
    get_usenet_streams_for_media,
    get_weekly_top_uploaders,
    get_working_trackers,
    # Telegram streams
    create_telegram_stream,
    get_telegram_stream_by_chat_message,
    get_telegram_stream_by_file_id,
    get_telegram_stream_by_file_unique_id,
    get_telegram_streams_for_media,
    telegram_stream_exists,
    update_telegram_stream_file_id,
    # Telegram user forwards (per-user forwarded copies for MediaFlow)
    create_telegram_user_forward,
    delete_telegram_user_forwards_for_stream,
    delete_telegram_user_forwards_for_user,
    get_telegram_user_forward,
    # File media links (NEW in v5 - replaces StreamEpisodeFile)
    link_file_to_media,
    link_stream_to_media,
    unlink_stream_from_media,
    update_stream_files,
    update_torrent_seeders,
    update_usenet_stream,
    update_torrent_stream,
    update_tracker_status,
)

# User content operations (catalogs, library)
from db.crud.user_content import (
    # Catalog items
    add_item_to_catalog,
    # Library
    add_to_library,
    create_catalog,
    delete_catalog,
    # User catalog CRUD
    get_catalog_by_id,
    get_catalog_by_uuid,
    get_catalog_items,
    get_catalogs_for_user,
    get_favorites,
    get_library_item,
    get_public_catalogs,
    get_subscribed_catalogs,
    get_watchlist,
    is_subscribed,
    remove_from_library,
    remove_item_from_catalog,
    reorder_catalog_items,
    # Subscriptions
    subscribe_to_catalog,
    unsubscribe_from_catalog,
    update_catalog,
    update_library_item,
)

# User operations
from db.crud.users import (
    # Downloads (via WatchHistory)
    add_download,
    # Watch history
    add_watch_history,
    clear_watch_history,
    create_profile,
    create_user,
    delete_profile,
    delete_user,
    get_default_profile,
    get_downloads,
    get_playback_stats,
    # Profile CRUD
    get_profile_by_id,
    get_profile_by_uuid,
    get_profiles_for_user,
    get_user_by_email,
    # User CRUD
    get_user_by_id,
    get_user_by_username,
    get_user_by_uuid,
    get_watch_history,
    increment_contribution_points,
    # Playback tracking
    track_playback,
    update_last_login,
    update_profile,
    update_user,
)

__all__ = [
    # Base
    "CatalogBaseQueryBuilder",
    "CatalogQueryBuilder",
    "SearchQueryBuilder",
    # Reference
    "get_or_create_genre",
    "get_or_create_catalog",
    "get_or_create_parental_certificate",
    "get_or_create_language",
    "get_or_create_audio_format",
    "get_or_create_audio_channel",
    "get_or_create_hdr_format",
    "get_genres",
    "get_all_genres_by_type",
    # Media
    "get_media_by_id",
    "get_metadata_counts",
    "get_media_by_external_id",
    "resolve_external_id",
    "get_media_by_title_year",
    "create_media",
    "update_media",
    "delete_media",
    "increment_stream_count",
    "decrement_stream_count",
    "search_media",
    "get_media_count",
    "get_movie_metadata",
    "create_movie_metadata",
    "get_series_metadata",
    "create_series_metadata",
    "get_season",
    "get_seasons_for_series",
    "create_season",
    "get_or_create_season",
    "get_episode",
    "get_episodes_for_season",
    "create_episode",
    "get_or_create_episode",
    "add_genres_to_media",
    "add_catalogs_to_media",
    "add_aka_title",
    # Multi-provider images
    "get_media_images",
    "add_media_image",
    "get_primary_image",
    # Multi-provider ratings
    "get_media_ratings",
    "get_rating_by_provider",
    "upsert_media_rating",
    "get_mediafusion_rating",
    "update_mediafusion_rating",
    # Comprehensive loaders
    "get_media_with_all_relations",
    "get_media_by_external_id_full",
    # Provider CRUD
    "get_rating_provider",
    "get_or_create_rating_provider",
    "get_metadata_provider",
    "get_or_create_metadata_provider",
    # External ID functions
    "get_canonical_external_id",
    "get_canonical_external_ids_batch",
    "invalidate_external_id_cache",
    "add_external_id",
    "get_all_external_ids_dict",
    "get_all_external_ids_batch",
    # Streams
    "get_stream_by_id",
    "get_streams_for_media",
    "delete_stream",
    "link_stream_to_media",
    "unlink_stream_from_media",
    "get_torrent_by_info_hash",
    "create_torrent_stream",
    "update_torrent_seeders",
    "update_torrent_stream",
    "update_stream_files",
    "delete_torrent_by_info_hash",
    # Usenet streams
    "get_usenet_stream_by_guid",
    "get_usenet_streams_for_media",
    "create_usenet_stream",
    "update_usenet_stream",
    "delete_usenet_stream_by_guid",
    "create_http_stream",
    "get_http_stream_by_url_for_media",
    "create_youtube_stream",
    "create_external_link_stream",
    # AceStream streams
    "create_acestream_stream",
    "get_acestream_by_content_id",
    "get_acestream_by_info_hash",
    "get_acestream_by_identifier",
    "get_tracker_by_url",
    "create_tracker",
    "get_or_create_tracker",
    "update_tracker_status",
    "get_working_trackers",
    # Metrics
    "get_torrent_count",
    "get_torrents_by_source",
    "get_torrents_by_uploader",
    "get_weekly_top_uploaders",
    # Stream files (NEW in v5)
    "add_files_to_stream",
    "get_files_for_stream",
    "delete_files_for_stream",
    # File media links (NEW in v5)
    "link_file_to_media",
    "get_file_media_links_for_stream",
    "get_files_for_episode",
    "delete_file_media_links_for_stream",
    "add_languages_to_stream",
    # Users
    "get_user_by_id",
    "get_user_by_uuid",
    "get_user_by_email",
    "get_user_by_username",
    "create_user",
    "update_user",
    "update_last_login",
    "delete_user",
    "increment_contribution_points",
    "get_profile_by_id",
    "get_profile_by_uuid",
    "get_profiles_for_user",
    "get_default_profile",
    "create_profile",
    "update_profile",
    "delete_profile",
    "add_watch_history",
    "get_watch_history",
    "clear_watch_history",
    "add_download",
    "get_downloads",
    "track_playback",
    "get_playback_stats",
    # User content
    "get_catalog_by_id",
    "get_catalog_by_uuid",
    "get_catalogs_for_user",
    "get_public_catalogs",
    "create_catalog",
    "update_catalog",
    "delete_catalog",
    "add_item_to_catalog",
    "remove_item_from_catalog",
    "get_catalog_items",
    "reorder_catalog_items",
    "subscribe_to_catalog",
    "unsubscribe_from_catalog",
    "get_subscribed_catalogs",
    "is_subscribed",
    "add_to_library",
    "get_library_item",
    "update_library_item",
    "remove_from_library",
    "get_favorites",
    "get_watchlist",
    # Contributions
    "vote_on_stream",
    "remove_stream_vote",
    "get_user_stream_vote",
    "get_stream_vote_count",
    "vote_on_metadata",
    "create_contribution",
    "get_contribution",
    "get_pending_contributions",
    "update_contribution_status",
    "get_user_contributions",
    "create_metadata_suggestion",
    "get_pending_metadata_suggestions",
    "create_stream_suggestion",
    "get_contribution_settings",
    "update_contribution_settings",
    # Providers (from providers.py)
    "get_provider_by_id",
    "get_provider_by_name",
    "get_all_providers",
    "create_provider",
    "get_or_create_provider",
    "get_rating_provider_by_name",
    "get_all_rating_providers",
    "create_rating_provider",
    "get_images_for_media",
    "set_primary_image",
    "get_ratings_for_media",
    "add_or_update_rating",
    "vote_on_media",
    "store_provider_metadata",
    "get_provider_metadata",
    "update_provider_metadata",
    "apply_multi_provider_metadata",
    "providers_add_media_image",
    "providers_get_mediafusion_rating",
    # Scraper helpers
    "fetch_last_run",
    "get_metadata_by_id",
    "get_movie_data_by_id",
    "get_series_data_by_id",
    "get_tv_data_by_id",
    "get_or_create_metadata",
    "update_metadata",
    "delete_metadata",
    "update_meta_stream",
    "update_single_imdb_metadata",
    "get_stream_by_info_hash",
    "store_new_torrent_streams",
    "store_new_usenet_streams",
    "delete_torrent_stream",
    "block_torrent_stream",
    "migrate_torrent_streams",
    "save_tv_channel_metadata",
    "find_tv_channel_by_title",
    "get_all_tv_streams_paginated",
    "get_tv_streams_by_meta_id",
    "update_tv_stream_status",
    "delete_tv_stream",
    "get_tv_metadata_not_working_posters",
    "update_tv_metadata_poster",
    "update_user_rss_feed",
    "update_user_rss_feed_metrics",
    "list_all_active_user_rss_feeds",
    "list_user_rss_feeds",
    "list_all_user_rss_feeds_with_users",
    "get_user_rss_feed",
    "get_user_rss_feed_by_url",
    "create_user_rss_feed",
    "update_user_rss_feed_by_uuid",
    "delete_user_rss_feed",
    "bulk_update_user_rss_feed_status",
    # Telegram streams
    "create_telegram_stream",
    "get_telegram_stream_by_chat_message",
    "get_telegram_stream_by_file_id",
    "get_telegram_stream_by_file_unique_id",
    "get_telegram_streams_for_media",
    "telegram_stream_exists",
    "update_telegram_stream_file_id",
    # Telegram user forwards
    "create_telegram_user_forward",
    "delete_telegram_user_forwards_for_stream",
    "delete_telegram_user_forwards_for_user",
    "get_telegram_user_forward",
    # Stream services (Stremio API)
    "get_movie_streams",
    "get_series_streams",
    "get_tv_streams_formatted",
    "get_event_streams",
    # Catalog operations (Stremio API)
    "get_catalog_meta_list",
    "get_mdblist_meta_list",
    "search_metadata",
]
