"""Media and torrent data schemas - aligned with new DB architecture.

This module provides Pydantic schemas that fully utilize the new database structure:
- MediaImage: Multi-provider images (poster, background, logo, banner, etc.)
- MediaRating: Multi-provider ratings (IMDb, TMDB, Trakt, Letterboxd, etc.)
- MediaFusionRating: Community ratings
- ProviderMetadata: Cached provider-specific metadata
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field, computed_field, model_validator

from db.schemas.stremio import StreamBehaviorHints

if TYPE_CHECKING:
    from db.models import (
        Episode as EpisodeModel,
    )
    from db.models import (
        HTTPStream as HTTPStreamModel,
    )
    from db.models import (
        Media as MediaModel,
    )
    from db.models import (
        MediaExternalID as MediaExternalIDModel,
    )
    from db.models import (
        MediaFusionRating as MediaFusionRatingModel,
    )
    from db.models import (
        MediaImage as MediaImageModel,
    )
    from db.models import (
        MediaRating as MediaRatingModel,
    )
    from db.models import (
        Season as SeasonModel,
    )
    from db.models import (
        Stream as StreamModel,
    )
    from db.models import (
        TorrentStream as TorrentStreamModel,
    )
    from db.models import (
        YouTubeStream as YouTubeStreamModel,
    )


# ============================================
# Image Schemas
# ============================================


class ImageData(BaseModel):
    """Single image from a provider."""

    url: str
    provider: str  # tmdb, fanart, tvdb, mediafusion, etc.
    image_type: str  # poster, background, logo, banner, thumb, clearart
    language: str | None = None
    width: int | None = None
    height: int | None = None
    aspect_ratio: float | None = None
    vote_average: float | None = None
    vote_count: int | None = None
    is_primary: bool = False
    display_order: int = 100


class MediaImages(BaseModel):
    """All images for a media item organized by type."""

    posters: list[ImageData] = Field(default_factory=list)
    backgrounds: list[ImageData] = Field(default_factory=list)  # Also called backdrops/fanart
    logos: list[ImageData] = Field(default_factory=list)
    banners: list[ImageData] = Field(default_factory=list)
    thumbs: list[ImageData] = Field(default_factory=list)
    cleararts: list[ImageData] = Field(default_factory=list)

    def get_primary_poster(self, language: str = None) -> str | None:
        """Get the primary poster URL, optionally filtered by language."""
        # First try primary
        for img in self.posters:
            if img.is_primary and (language is None or img.language == language):
                return img.url
        # Then by display_order
        sorted_posters = sorted(self.posters, key=lambda x: x.display_order)
        for img in sorted_posters:
            if language is None or img.language == language:
                return img.url
        # Return first available
        return self.posters[0].url if self.posters else None

    def get_primary_background(self, language: str = None) -> str | None:
        """Get the primary background URL."""
        for img in self.backgrounds:
            if img.is_primary and (language is None or img.language == language):
                return img.url
        sorted_bgs = sorted(self.backgrounds, key=lambda x: x.display_order)
        for img in sorted_bgs:
            if language is None or img.language == language:
                return img.url
        return self.backgrounds[0].url if self.backgrounds else None

    def get_primary_logo(self, language: str = None) -> str | None:
        """Get the primary logo URL."""
        for img in self.logos:
            if img.is_primary and (language is None or img.language == language):
                return img.url
        sorted_logos = sorted(self.logos, key=lambda x: x.display_order)
        for img in sorted_logos:
            if language is None or img.language == language:
                return img.url
        return self.logos[0].url if self.logos else None

    @classmethod
    def from_db(cls, media_images: list["MediaImageModel"]) -> "MediaImages":
        """Create from database MediaImage records."""
        images = cls()
        for img in media_images:
            image_data = ImageData(
                url=img.url,
                provider=img.provider.name if img.provider else "unknown",
                image_type=img.image_type,
                language=img.language,
                width=img.width,
                height=img.height,
                aspect_ratio=img.aspect_ratio,
                vote_average=img.vote_average,
                vote_count=img.vote_count,
                is_primary=img.is_primary,
                display_order=img.display_order,
            )
            if img.image_type == "poster":
                images.posters.append(image_data)
            elif img.image_type in ("background", "backdrop", "fanart"):
                images.backgrounds.append(image_data)
            elif img.image_type == "logo":
                images.logos.append(image_data)
            elif img.image_type == "banner":
                images.banners.append(image_data)
            elif img.image_type == "thumb":
                images.thumbs.append(image_data)
            elif img.image_type == "clearart":
                images.cleararts.append(image_data)
        return images


# ============================================
# Rating Schemas
# ============================================


class RatingData(BaseModel):
    """Rating from a specific provider."""

    provider: str  # imdb, tmdb, trakt, letterboxd, rottentomatoes, metacritic
    display_name: str  # IMDb, TMDB, Trakt, Letterboxd, Rotten Tomatoes, Metacritic
    rating: float  # Normalized 0-10 scale
    rating_raw: float | None = None  # Original scale (e.g., 85 for RT)
    max_rating: float = 10.0  # Provider's max scale
    votes: int | None = None
    rating_type: str | None = None  # audience, critic, fresh
    certification: str | None = None  # fresh, rotten, certified_fresh
    is_percentage: bool = False
    icon_url: str | None = None
    url: str | None = None  # Link to provider page


class MediaRatings(BaseModel):
    """All ratings for a media item from various providers."""

    ratings: list[RatingData] = Field(default_factory=list)
    mediafusion_rating: Optional["MediaFusionRatingData"] = None

    def get_rating(self, provider: str) -> RatingData | None:
        """Get rating from a specific provider."""
        for rating in self.ratings:
            if rating.provider.lower() == provider.lower():
                return rating
        return None

    @property
    def imdb(self) -> float | None:
        """IMDb rating (0-10)."""
        r = self.get_rating("imdb")
        return r.rating if r else None

    @property
    def tmdb(self) -> float | None:
        """TMDB rating (0-10)."""
        r = self.get_rating("tmdb")
        return r.rating if r else None

    @property
    def trakt(self) -> float | None:
        """Trakt rating (0-10)."""
        r = self.get_rating("trakt")
        return r.rating if r else None

    @property
    def rotten_tomatoes(self) -> int | None:
        """Rotten Tomatoes score (0-100)."""
        r = self.get_rating("rottentomatoes")
        return int(r.rating_raw) if r and r.rating_raw else None

    @property
    def metacritic(self) -> int | None:
        """Metacritic score (0-100)."""
        r = self.get_rating("metacritic")
        return int(r.rating_raw) if r and r.rating_raw else None

    @property
    def average(self) -> float | None:
        """Average of all normalized ratings."""
        if not self.ratings:
            return None
        return sum(r.rating for r in self.ratings) / len(self.ratings)

    @classmethod
    def from_db(
        cls,
        media_ratings: list["MediaRatingModel"],
        mf_rating: "MediaFusionRatingModel" = None,
    ) -> "MediaRatings":
        """Create from database MediaRating records."""
        ratings = cls()
        for r in media_ratings:
            provider = r.provider
            ratings.ratings.append(
                RatingData(
                    provider=provider.name if provider else "unknown",
                    display_name=provider.display_name if provider else "Unknown",
                    rating=r.rating,
                    rating_raw=r.rating_raw,
                    max_rating=provider.max_rating if provider else 10.0,
                    votes=r.vote_count,
                    rating_type=r.rating_type,
                    certification=r.certification,
                    is_percentage=provider.is_percentage if provider else False,
                    icon_url=provider.icon_url if provider else None,
                )
            )
        if mf_rating:
            ratings.mediafusion_rating = MediaFusionRatingData.from_db(mf_rating)
        return ratings


class MediaFusionRatingData(BaseModel):
    """MediaFusion community rating aggregate."""

    average_rating: float = 0.0  # 0-10 scale
    total_votes: int = 0
    upvotes: int = 0
    downvotes: int = 0
    five_star_count: int = 0
    four_star_count: int = 0
    three_star_count: int = 0
    two_star_count: int = 0
    one_star_count: int = 0

    @classmethod
    def from_db(cls, mf_rating: "MediaFusionRatingModel") -> "MediaFusionRatingData":
        return cls(
            average_rating=mf_rating.average_rating,
            total_votes=mf_rating.total_votes,
            upvotes=mf_rating.upvotes,
            downvotes=mf_rating.downvotes,
            five_star_count=mf_rating.five_star_count,
            four_star_count=mf_rating.four_star_count,
            three_star_count=mf_rating.three_star_count,
            two_star_count=mf_rating.two_star_count,
            one_star_count=mf_rating.one_star_count,
        )


# ============================================
# External ID Schemas
# ============================================


class ExternalIDs(BaseModel):
    """External IDs from various providers."""

    imdb_id: str | None = None  # tt1234567
    tmdb_id: str | None = None
    tvdb_id: str | None = None
    tvmaze_id: str | None = None
    mal_id: str | None = None  # MyAnimeList
    kitsu_id: str | None = None
    anilist_id: str | None = None
    anidb_id: str | None = None
    trakt_id: str | None = None
    letterboxd_id: str | None = None

    @classmethod
    def from_db(cls, external_ids: list["MediaExternalIDModel"]) -> "ExternalIDs":
        """Create ExternalIDs from a list of MediaExternalID records."""
        # Build a mapping from provider to external_id
        id_map = {ext.provider: ext.external_id for ext in external_ids}

        return cls(
            imdb_id=id_map.get("imdb"),
            tmdb_id=id_map.get("tmdb"),
            tvdb_id=id_map.get("tvdb"),
            tvmaze_id=id_map.get("tvmaze"),
            mal_id=id_map.get("mal"),
            kitsu_id=id_map.get("kitsu"),
            anilist_id=id_map.get("anilist"),
            anidb_id=id_map.get("anidb"),
            trakt_id=id_map.get("trakt"),
            letterboxd_id=id_map.get("letterboxd"),
        )


# ============================================
# Episode Schemas
# ============================================


class EpisodeData(BaseModel):
    """Episode metadata."""

    id: int
    season_number: int
    episode_number: int
    title: str
    overview: str | None = None
    air_date: date | None = None
    runtime_minutes: int | None = None
    still_url: str | None = None
    external_ids: ExternalIDs | None = None

    @classmethod
    def from_db(cls, episode: "EpisodeModel") -> "EpisodeData":
        return cls(
            id=episode.id,
            season_number=episode.season.season_number if episode.season else 0,
            episode_number=episode.episode_number,
            title=episode.title,
            overview=episode.overview,
            air_date=episode.air_date,
            runtime_minutes=episode.runtime_minutes,
            still_url=None,  # Episode model doesn't have still_url, images are in MediaImage
            external_ids=ExternalIDs(
                imdb_id=episode.imdb_id,
                tmdb_id=str(episode.tmdb_id) if episode.tmdb_id else None,
                tvdb_id=str(episode.tvdb_id) if episode.tvdb_id else None,
            )
            if any([episode.imdb_id, episode.tmdb_id, episode.tvdb_id])
            else None,
        )


class SeasonData(BaseModel):
    """Season metadata."""

    id: int
    season_number: int
    name: str | None = None
    overview: str | None = None
    air_date: date | None = None
    episode_count: int = 0
    episodes: list[EpisodeData] = Field(default_factory=list)

    @classmethod
    def from_db(cls, season: "SeasonModel") -> "SeasonData":
        return cls(
            id=season.id,
            season_number=season.season_number,
            name=season.name,
            overview=season.overview,
            air_date=season.air_date,
            episode_count=season.episode_count,
            episodes=[EpisodeData.from_db(ep) for ep in season.episodes] if season.episodes else [],
        )


# ============================================
# Media Metadata Schemas
# ============================================


class MetadataData(BaseModel):
    """Complete media metadata - fully utilizing new architecture."""

    model_config = {"extra": "allow"}

    # Core identifiers
    id: int  # Database PK
    external_id: str  # IMDb ID, TMDB ID, or mf:user:xxx
    type: str  # movie, series, tv

    # Basic info
    title: str
    original_title: str | None = None
    year: int | None = None
    release_date: date | None = None
    end_date: date | None = None  # For series
    status: str | None = None  # released, ended, etc.
    runtime_minutes: int | None = None
    description: str | None = None
    tagline: str | None = None
    adult: bool = False
    original_language: str | None = None
    popularity: float | None = None
    website: str | None = None

    # Multi-provider data
    images: MediaImages = Field(default_factory=MediaImages)
    ratings: MediaRatings = Field(default_factory=MediaRatings)
    external_ids: ExternalIDs | None = None

    # Relationships
    genres: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    aka_titles: list[str] = Field(default_factory=list)
    catalogs: list[str] = Field(default_factory=list)

    # Provider info
    primary_provider: str | None = None
    is_user_created: bool = False
    created_by_user_id: int | None = None
    is_public: bool = True

    # Aggregates
    total_streams: int = 0
    last_stream_added: datetime | None = None

    # Series-specific
    seasons: list[SeasonData] = Field(default_factory=list)
    total_seasons: int | None = None
    total_episodes: int | None = None
    network: str | None = None

    # TV-specific
    country: str | None = None
    tv_language: str | None = None

    # Computed properties for backward compatibility
    @computed_field
    @property
    def poster(self) -> str | None:
        """Primary poster URL."""
        return self.images.get_primary_poster()

    @computed_field
    @property
    def background(self) -> str | None:
        """Primary background URL."""
        return self.images.get_primary_background()

    @computed_field
    @property
    def logo(self) -> str | None:
        """Primary logo URL."""
        return self.images.get_primary_logo()

    @computed_field
    @property
    def imdb_rating(self) -> float | None:
        """IMDb rating for backward compatibility."""
        return self.ratings.imdb

    @computed_field
    @property
    def runtime(self) -> str | None:
        """Runtime as string for display."""
        return f"{self.runtime_minutes} min" if self.runtime_minutes else None

    @computed_field
    @property
    def end_year(self) -> int | None:
        """End year derived from end_date for series.

        Used by scrapers to validate year ranges for series content.
        """
        return self.end_date.year if self.end_date else None

    # ============================================
    # External ID Helper Methods for Scrapers
    # ============================================

    def get_imdb_id(self) -> str | None:
        """Get IMDb ID if available.

        Checks external_ids first, then falls back to external_id if it looks like IMDb.
        Returns ID in format 'tt1234567' or None.
        """
        if self.external_ids and self.external_ids.imdb_id:
            return self.external_ids.imdb_id
        # Fallback to external_id if it looks like IMDb
        if self.external_id and self.external_id.startswith("tt"):
            return self.external_id
        return None

    def get_tmdb_id(self) -> str | None:
        """Get TMDB ID if available."""
        if self.external_ids and self.external_ids.tmdb_id:
            return self.external_ids.tmdb_id
        # Fallback to external_id if it looks like TMDB format
        if self.external_id and self.external_id.startswith("tmdb:"):
            return self.external_id.split(":", 1)[1]
        return None

    def get_tvdb_id(self) -> str | None:
        """Get TVDB ID if available."""
        if self.external_ids and self.external_ids.tvdb_id:
            return self.external_ids.tvdb_id
        # Fallback to external_id if it looks like TVDB format
        if self.external_id and self.external_id.startswith("tvdb:"):
            return self.external_id.split(":", 1)[1]
        return None

    def get_mal_id(self) -> str | None:
        """Get MyAnimeList ID if available."""
        if self.external_ids and self.external_ids.mal_id:
            return self.external_ids.mal_id
        return None

    def get_kitsu_id(self) -> str | None:
        """Get Kitsu ID if available."""
        if self.external_ids and self.external_ids.kitsu_id:
            return self.external_ids.kitsu_id
        return None

    def get_anilist_id(self) -> str | None:
        """Get AniList ID if available."""
        if self.external_ids and self.external_ids.anilist_id:
            return self.external_ids.anilist_id
        return None

    def get_trakt_id(self) -> str | None:
        """Get Trakt ID if available."""
        if self.external_ids and self.external_ids.trakt_id:
            return self.external_ids.trakt_id
        return None

    def get_canonical_id(self) -> str:
        """Get the canonical external ID for this media.

        Priority: IMDb > TMDB > TVDB > external_id
        Used for cache keys and stream meta_id.
        """
        imdb = self.get_imdb_id()
        if imdb:
            return imdb
        tmdb = self.get_tmdb_id()
        if tmdb:
            return f"tmdb:{tmdb}"
        tvdb = self.get_tvdb_id()
        if tvdb:
            return f"tvdb:{tvdb}"
        # Fallback to external_id or database ID
        return self.external_id or f"mf:{self.id}"

    def has_imdb_id(self) -> bool:
        """Check if IMDb ID is available."""
        return self.get_imdb_id() is not None

    @classmethod
    def from_db(
        cls,
        media: "MediaModel",
        images: list["MediaImageModel"] = None,
        ratings: list["MediaRatingModel"] = None,
        mf_rating: "MediaFusionRatingModel" = None,
        external_ids: list["MediaExternalIDModel"] = None,
    ) -> "MetadataData":
        """Create from Media database model with all related data."""
        # Determine type-specific fields
        end_date = media.end_date
        total_seasons = None
        total_episodes = None
        network = None
        country = None
        tv_language = None
        seasons = []

        if media.series_metadata:
            series_meta = media.series_metadata[0] if isinstance(media.series_metadata, list) else media.series_metadata
            total_seasons = series_meta.total_seasons
            total_episodes = series_meta.total_episodes
            network = series_meta.network
            if series_meta.seasons:
                seasons = [SeasonData.from_db(s) for s in series_meta.seasons]

        if media.tv_metadata:
            tv_meta = media.tv_metadata[0] if isinstance(media.tv_metadata, list) else media.tv_metadata
            country = tv_meta.country
            tv_language = tv_meta.tv_language

        media_images = MediaImages()
        if images:
            media_images = MediaImages.from_db(images)
        elif media.images:
            media_images = MediaImages.from_db(media.images)

        media_ratings = MediaRatings()
        if ratings:
            media_ratings = MediaRatings.from_db(ratings, mf_rating)
        elif media.ratings:
            media_ratings = MediaRatings.from_db(media.ratings, media.mediafusion_rating)

        ext_ids = None
        loaded_external_ids = external_ids or media.external_ids or []
        if loaded_external_ids:
            ext_ids = ExternalIDs.from_db(loaded_external_ids)

        # Compute canonical external_id from loaded external_ids
        canonical_ext_id = f"mf:{media.id}"  # Default fallback
        if loaded_external_ids:
            # Build lookup and pick by priority
            id_by_provider = {ext.provider: ext.external_id for ext in loaded_external_ids}
            for provider in ["imdb", "tvdb", "tmdb", "mal", "kitsu"]:
                if provider in id_by_provider:
                    ext_value = id_by_provider[provider]
                    if provider == "imdb":
                        canonical_ext_id = ext_value if ext_value.startswith("tt") else f"tt{ext_value}"
                    else:
                        canonical_ext_id = f"{provider}:{ext_value}"
                    break
            else:
                # Use first available
                first = loaded_external_ids[0]
                canonical_ext_id = f"{first.provider}:{first.external_id}"

        return cls(
            id=media.id,
            external_id=canonical_ext_id,
            type=media.type.value if hasattr(media.type, "value") else str(media.type),
            title=media.title,
            original_title=media.original_title,
            year=media.year,
            release_date=media.release_date,
            end_date=end_date,
            status=media.status,
            runtime_minutes=media.runtime_minutes,
            description=media.description,
            tagline=media.tagline,
            adult=media.adult,
            original_language=media.original_language,
            popularity=media.popularity,
            website=media.website,
            images=media_images,
            ratings=media_ratings,
            external_ids=ext_ids,
            genres=[g.name for g in media.genres] if media.genres else [],
            keywords=[k.name for k in media.keywords] if media.keywords else [],
            aka_titles=[aka.title for aka in media.aka_titles] if media.aka_titles else [],
            catalogs=[c.name for c in media.catalogs] if media.catalogs else [],
            primary_provider=None,
            is_user_created=media.is_user_created,
            created_by_user_id=media.created_by_user_id,
            is_public=media.is_public,
            total_streams=media.total_streams,
            last_stream_added=media.last_stream_added,
            seasons=seasons,
            total_seasons=total_seasons,
            total_episodes=total_episodes,
            network=network,
            country=country,
            tv_language=tv_language,
        )


# ============================================
# Stream File Schemas (NEW in v5)
# ============================================


class StreamFileResponse(BaseModel):
    """File within a stream - for API responses (matches StreamFile model)."""

    id: int
    file_index: int | None = None
    filename: str
    file_path: str | None = None
    size: int | None = None
    file_type: str = "video"  # video, audio, subtitle, archive, sample, etc.


class FileMediaLinkData(BaseModel):
    """File-to-media link with episode info - matches FileMediaLink model."""

    file_id: int
    media_id: int
    season_number: int | None = None
    episode_number: int | None = None
    episode_end: int | None = None  # For multi-episode files
    link_source: str = "ptt_parser"
    confidence: float = 1.0


class StreamFileData(BaseModel):
    """File within a torrent stream - used for creating StreamFile records."""

    file_index: int = 0
    filename: str
    file_path: str | None = None
    size: int = 0
    file_type: str = "video"  # video, audio, subtitle, archive, sample, etc.

    # Episode linking (optional - for series torrents)
    season_number: int | None = None
    episode_number: int | None = None
    episode_end: int | None = None  # For multi-episode files (e.g., S01E01-E03)
    episode_title: str | None = None  # Human-readable episode name (e.g., "Free Practice 1")


class TorrentStreamData(BaseModel):
    """Torrent stream data - aligned with Stream + TorrentStream + StreamFile models (v5 schema).

    Architecture:
    - Stream: Base stream info + normalized quality attributes
    - TorrentStream: Torrent-specific data (info_hash, seeders, etc.)
    - StreamFile: Individual files within the torrent
    - FileMediaLink: Links files to media (with optional season/episode)

    Quality attributes:
    - Single-value: resolution, codec, quality, bit_depth, uploader, release_group
    - Multi-value (normalized): audio_formats, channels, hdr_formats, languages
    - Boolean flags: is_remastered, is_upscaled, is_proper, etc.

    NOTE: `catalog` is NOT stored here - catalogs belong to Media, not streams.
    Use `source` to track where the stream came from (scraper name, contribution, etc.)
    """

    model_config = {"extra": "allow"}

    # TorrentStream fields
    info_hash: str = Field(description="Torrent info_hash (40-character hex string)")
    seeders: int | None = None
    leechers: int | None = None
    torrent_type: str = "public"  # public, private, webseed
    torrent_file: bytes | None = None
    uploaded_at: datetime | None = None

    # Stream base - identifiers and source
    name: str  # Stream display name (torrent title)
    size: int  # Total size in bytes
    source: str  # Source tracker/scraper name (e.g., "BT4G", "TamilMV", "contribution")

    # Single-value quality attributes
    resolution: str | None = None  # 4k, 2160p, 1080p, 720p, 480p
    codec: str | None = None  # x264, x265, hevc, av1
    quality: str | None = None  # web-dl, bluray, cam, hdtv
    bit_depth: str | None = None  # 8-bit, 10-bit, 12-bit
    uploader: str | None = None  # MediaFusion contributor name (for user-contributed streams)
    release_group: str | None = None  # Release group from torrent name (from PTT)

    # Multi-value quality attributes (stored in normalized tables)
    audio_formats: list[str] = Field(default_factory=list)  # AAC, DTS, Atmos, TrueHD, EAC3
    channels: list[str] = Field(default_factory=list)  # 2.0, 5.1, 7.1, Atmos
    hdr_formats: list[str] = Field(default_factory=list)  # HDR10, HDR10+, Dolby Vision, HLG
    languages: list[str] = Field(default_factory=list)  # Full language names

    # Release flags (from PTT parsing)
    is_remastered: bool = False
    is_upscaled: bool = False
    is_proper: bool = False
    is_repack: bool = False
    is_extended: bool = False
    is_complete: bool = False  # Complete series/season
    is_dubbed: bool = False
    is_subbed: bool = False

    # Stream status
    is_active: bool = True
    is_blocked: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Media linking
    meta_id: str  # External ID of linked media (IMDb ID, TMDB ID, etc.)

    # Trackers
    announce_list: list[str] = Field(default_factory=list)

    # File structure (for creating StreamFile records)
    # Each file can optionally include season/episode info for series
    files: list[StreamFileData] = Field(default_factory=list)

    def __hash__(self):
        return hash(self.info_hash)

    def __eq__(self, other):
        if isinstance(other, TorrentStreamData):
            return self.info_hash == other.info_hash
        return False

    def get_video_files(self) -> list[StreamFileData]:
        """Returns video files sorted by size (largest first)."""
        return sorted(
            [f for f in self.files if f.file_type == "video"],
            key=lambda f: f.size or 0,
            reverse=True,
        )

    def get_main_file(self) -> StreamFileData | None:
        """Returns the main (largest) video file."""
        video_files = self.get_video_files()
        return video_files[0] if video_files else None

    def get_episode_files(self, season: int, episode: int) -> list[StreamFileData]:
        """Returns files matching the given season/episode."""
        return [f for f in self.files if f.season_number == season and f.episode_number == episode]

    # Alias for backwards compatibility
    get_episodes = get_episode_files

    def get_file_for_episode(self, season: int, episode: int) -> StreamFileData | None:
        """Get primary file for an episode (largest by size)."""
        matches = self.get_episode_files(season, episode)
        return sorted(matches, key=lambda f: f.size or 0, reverse=True)[0] if matches else None

    @classmethod
    def from_db(
        cls,
        torrent: "TorrentStreamModel",
        stream: "StreamModel" = None,
        media: "MediaModel" = None,
    ) -> "TorrentStreamData":
        """Create from database models with normalized quality attributes.

        Uses v5 architecture:
        - StreamFile for file structure
        - FileMediaLink for file-to-media linking with optional episode info
        """
        if stream is None:
            stream = torrent.stream

        meta_id = ""
        if media:
            meta_id = f"mf:{media.id}"
        elif stream and stream.files:
            for f in stream.files:
                if f.media_links:
                    meta_id = f"mf:{f.media_links[0].media_id}"
                    break

        # Build StreamFileData list from StreamFile + FileMediaLink
        files: list[StreamFileData] = []
        if stream and stream.files:
            for f in stream.files:
                file_type = "video"
                if hasattr(f.file_type, "value"):
                    file_type = f.file_type.value
                elif f.file_type:
                    file_type = str(f.file_type)

                season_number = None
                episode_number = None
                episode_end = None
                if f.media_links:
                    for link in f.media_links:
                        if link.season_number is not None:
                            season_number = link.season_number
                            episode_number = link.episode_number
                            episode_end = link.episode_end
                            break  # Use first link with episode info

                files.append(
                    StreamFileData(
                        file_index=f.file_index or 0,
                        filename=f.filename,
                        file_path=f.file_path,
                        size=f.size or 0,
                        file_type=file_type,
                        season_number=season_number,
                        episode_number=episode_number,
                        episode_end=episode_end,
                    )
                )

        return cls(
            info_hash=torrent.info_hash,
            seeders=torrent.seeders,
            leechers=torrent.leechers,
            torrent_type=torrent.torrent_type.value if torrent.torrent_type else "public",
            torrent_file=torrent.torrent_file,
            uploaded_at=torrent.uploaded_at,
            name=stream.name if stream else "",
            size=torrent.total_size or 0,
            source=stream.source if stream else "unknown",
            resolution=stream.resolution,
            codec=stream.codec,
            quality=stream.quality,
            bit_depth=stream.bit_depth,
            uploader=stream.uploader,
            release_group=stream.release_group,
            # Multi-value attributes from normalized tables
            audio_formats=[af.name for af in stream.audio_formats] if stream and stream.audio_formats else [],
            channels=[ch.name for ch in stream.channels] if stream and stream.channels else [],
            hdr_formats=[hf.name for hf in stream.hdr_formats] if stream and stream.hdr_formats else [],
            languages=[lang.name for lang in stream.languages] if stream and stream.languages else [],
            # Release flags
            is_remastered=stream.is_remastered if stream else False,
            is_upscaled=stream.is_upscaled if stream else False,
            is_proper=stream.is_proper if stream else False,
            is_repack=stream.is_repack if stream else False,
            is_extended=stream.is_extended if stream else False,
            is_complete=stream.is_complete if stream else False,
            is_dubbed=stream.is_dubbed if stream else False,
            is_subbed=stream.is_subbed if stream else False,
            # Status
            is_active=stream.is_active if stream else True,
            is_blocked=stream.is_blocked if stream else False,
            created_at=stream.created_at,
            updated_at=stream.updated_at,
            meta_id=meta_id,
            announce_list=[t.url for t in torrent.trackers] if torrent.trackers else [],
            files=files,
        )


class TorrentStreamsList(BaseModel):
    """List of torrent streams."""

    streams: list[TorrentStreamData]


class UsenetStreamData(BaseModel):
    """Usenet/NZB stream data - aligned with Stream + UsenetStream models.

    Architecture:
    - Stream: Base stream info + normalized quality attributes
    - UsenetStream: Usenet-specific data (nzb_guid, indexer, etc.)
    - StreamFile: Individual files within the NZB (similar to torrent)
    - FileMediaLink: Links files to media (with optional season/episode)

    Quality attributes:
    - Single-value: resolution, codec, quality, bit_depth, uploader, release_group
    - Multi-value (normalized): audio_formats, channels, hdr_formats, languages
    - Boolean flags: is_remastered, is_upscaled, is_proper, etc.
    """

    model_config = {"extra": "allow"}

    # UsenetStream fields
    nzb_guid: str = Field(description="Unique NZB identifier from indexer")
    nzb_url: str | None = None  # URL to fetch NZB file
    size: int  # Total size in bytes
    indexer: str  # Indexer source name
    group_name: str | None = None  # Usenet group (e.g., alt.binaries.movies)
    poster: str | None = None  # Usenet poster/uploader name
    files_count: int | None = None
    parts_count: int | None = None
    posted_at: datetime | None = None
    is_passworded: bool = False
    grabs: int | None = None  # Download count from indexer

    # Stream base - identifiers and source
    name: str  # Stream display name (NZB title)
    source: str  # Source indexer name (e.g., "NZBgeek", "NZBFinder")

    # Single-value quality attributes
    resolution: str | None = None  # 4k, 2160p, 1080p, 720p, 480p
    codec: str | None = None  # x264, x265, hevc, av1
    quality: str | None = None  # web-dl, bluray, cam, hdtv
    bit_depth: str | None = None  # 8-bit, 10-bit, 12-bit
    uploader: str | None = None  # MediaFusion contributor name (for user-contributed streams)
    release_group: str | None = None  # Release group from NZB name (from PTT)

    # Multi-value quality attributes (stored in normalized tables)
    audio_formats: list[str] = Field(default_factory=list)  # AAC, DTS, Atmos, TrueHD, EAC3
    channels: list[str] = Field(default_factory=list)  # 2.0, 5.1, 7.1, Atmos
    hdr_formats: list[str] = Field(default_factory=list)  # HDR10, HDR10+, Dolby Vision, HLG
    languages: list[str] = Field(default_factory=list)  # Full language names

    # Release flags (from PTT parsing)
    is_remastered: bool = False
    is_upscaled: bool = False
    is_proper: bool = False
    is_repack: bool = False
    is_extended: bool = False
    is_complete: bool = False  # Complete series/season
    is_dubbed: bool = False
    is_subbed: bool = False

    # Stream status
    is_active: bool = True
    is_blocked: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Media linking
    meta_id: str  # External ID of linked media (IMDb ID, TMDB ID, etc.)

    # File structure (for creating StreamFile records)
    # Each file can optionally include season/episode info for series
    files: list[StreamFileData] = Field(default_factory=list)

    def __hash__(self):
        return hash(self.nzb_guid)

    def __eq__(self, other):
        if isinstance(other, UsenetStreamData):
            return self.nzb_guid == other.nzb_guid
        return False

    def get_video_files(self) -> list[StreamFileData]:
        """Returns video files sorted by size (largest first)."""
        return sorted(
            [f for f in self.files if f.file_type == "video"],
            key=lambda f: f.size or 0,
            reverse=True,
        )

    def get_main_file(self) -> StreamFileData | None:
        """Returns the main (largest) video file."""
        video_files = self.get_video_files()
        return video_files[0] if video_files else None

    def get_episode_files(self, season: int, episode: int) -> list[StreamFileData]:
        """Returns files matching the given season/episode."""
        return [f for f in self.files if f.season_number == season and f.episode_number == episode]

    def get_file_for_episode(self, season: int, episode: int) -> StreamFileData | None:
        """Get primary file for an episode (largest by size)."""
        matches = self.get_episode_files(season, episode)
        return sorted(matches, key=lambda f: f.size or 0, reverse=True)[0] if matches else None

    @classmethod
    def from_db(cls, usenet_stream) -> "UsenetStreamData":
        """Create UsenetStreamData from database UsenetStream model.

        Args:
            usenet_stream: UsenetStream model instance with loaded relationships

        Returns:
            UsenetStreamData Pydantic model
        """
        stream = usenet_stream.stream

        # Extract multi-value attributes from relationships
        languages = [lang.name for lang in stream.languages] if stream.languages else []
        audio_formats = [af.name for af in stream.audio_formats] if stream.audio_formats else []
        channels = [ch.name for ch in stream.channels] if stream.channels else []
        hdr_formats = [hdr.name for hdr in stream.hdr_formats] if stream.hdr_formats else []

        # Extract files
        files = []
        if stream.files:
            for f in stream.files:
                file_data = StreamFileData(
                    file_index=f.file_index,
                    filename=f.filename,
                    size=f.size,
                    file_type=f.file_type.value if hasattr(f.file_type, "value") else str(f.file_type),
                )
                # Get episode info from file media links
                if f.media_links:
                    for link in f.media_links:
                        if link.season_number is not None:
                            file_data.season_number = link.season_number
                        if link.episode_number is not None:
                            file_data.episode_number = link.episode_number
                        break  # Use first link
                files.append(file_data)

        # Get meta_id - Stream doesn't have direct media_links relationship
        # The relationship is through StreamMediaLink table, but we don't need meta_id
        # for playback since we already have the nzb_guid
        meta_id = ""

        return cls(
            nzb_guid=usenet_stream.nzb_guid,
            nzb_url=usenet_stream.nzb_url,
            size=usenet_stream.size,
            indexer=usenet_stream.indexer,
            group_name=usenet_stream.group_name,
            poster=usenet_stream.uploader,
            files_count=usenet_stream.files_count,
            parts_count=usenet_stream.parts_count,
            posted_at=usenet_stream.posted_at,
            is_passworded=usenet_stream.is_passworded,
            name=stream.name,
            source=stream.source,
            resolution=stream.resolution,
            codec=stream.codec,
            quality=stream.quality,
            bit_depth=stream.bit_depth,
            uploader=stream.uploader_user.username if stream.uploader_user else None,
            audio_formats=audio_formats,
            channels=channels,
            hdr_formats=hdr_formats,
            languages=languages,
            is_remastered=stream.is_remastered,
            is_upscaled=stream.is_upscaled,
            is_proper=stream.is_proper,
            is_repack=stream.is_repack,
            is_extended=stream.is_extended,
            is_complete=stream.is_complete,
            is_dubbed=stream.is_dubbed,
            is_subbed=stream.is_subbed,
            is_active=stream.is_active,
            is_blocked=stream.is_blocked,
            created_at=stream.created_at,
            updated_at=stream.updated_at,
            meta_id=meta_id,
            files=files,
        )


class UsenetStreamsList(BaseModel):
    """List of Usenet streams."""

    streams: list[UsenetStreamData]


class TelegramStreamData(BaseModel):
    """Telegram stream data - aligned with Stream + TelegramStream models.

    Architecture:
    - Stream: Base stream info + normalized quality attributes
    - TelegramStream: Telegram-specific data (chat_id, message_id, file_id, etc.)
    - StreamFile: Individual files (if applicable)
    - FileMediaLink: Links files to media (with optional season/episode)

    Telegram streams can contain:
    - Direct video files uploaded to channels/groups
    - Magnet links shared in messages (converted to TorrentStream)
    - NZB links shared in messages (converted to UsenetStream)

    Quality attributes (same as TorrentStreamData):
    - Single-value: resolution, codec, quality, bit_depth, uploader, release_group
    - Multi-value (normalized): audio_formats, channels, hdr_formats, languages
    - Boolean flags: is_remastered, is_upscaled, is_proper, etc.
    """

    model_config = {"extra": "allow"}

    # Primary identifier for URL generation (preferred over chat_id/message_id)
    telegram_stream_id: int | None = None

    # TelegramStream fields
    chat_id: str = Field(description="Telegram channel/group ID")
    chat_username: str | None = None  # Channel @username (without @)
    message_id: int = Field(description="Message ID containing the file")
    file_id: str | None = None  # Telegram file_id for Bot API (bot-specific)
    file_unique_id: str | None = None  # Universal file identifier (same across all bots)
    file_name: str | None = None  # Original filename
    mime_type: str | None = None  # MIME type (video/mp4, etc.)
    size: int | None = None  # File size in bytes
    posted_at: datetime | None = None  # When the message was posted

    # Caption/message text (for metadata extraction)
    caption: str | None = None  # Message caption (may contain IMDb ID, title, etc.)

    # Stream base - identifiers and source
    name: str  # Stream display name (derived from filename or caption)
    source: str = "telegram"  # Always "telegram" for Telegram streams

    # Single-value quality attributes (parsed from filename/caption)
    resolution: str | None = None  # 4k, 2160p, 1080p, 720p, 480p
    codec: str | None = None  # x264, x265, hevc, av1
    quality: str | None = None  # web-dl, bluray, cam, hdtv
    bit_depth: str | None = None  # 8-bit, 10-bit, 12-bit
    uploader: str | None = None  # Channel name or username
    release_group: str | None = None  # Release group from filename (from PTT)

    # Multi-value quality attributes (stored in normalized tables)
    audio_formats: list[str] = Field(default_factory=list)  # AAC, DTS, Atmos, TrueHD, EAC3
    channels: list[str] = Field(default_factory=list)  # 2.0, 5.1, 7.1, Atmos
    hdr_formats: list[str] = Field(default_factory=list)  # HDR10, HDR10+, Dolby Vision, HLG
    languages: list[str] = Field(default_factory=list)  # Full language names

    # Release flags (from PTT parsing)
    is_remastered: bool = False
    is_upscaled: bool = False
    is_proper: bool = False
    is_repack: bool = False
    is_extended: bool = False
    is_complete: bool = False  # Complete series/season
    is_dubbed: bool = False
    is_subbed: bool = False

    # Stream status
    is_active: bool = True
    is_blocked: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Media linking
    meta_id: str = ""  # External ID of linked media (IMDb ID, TMDB ID, etc.)

    # For series - episode info (parsed from filename/caption)
    season_number: int | None = None
    episode_number: int | None = None
    episode_end: int | None = None  # For multi-episode files

    def __hash__(self):
        return hash((self.chat_id, self.message_id))

    def __eq__(self, other):
        if isinstance(other, TelegramStreamData):
            return self.chat_id == other.chat_id and self.message_id == other.message_id
        return False

    @property
    def unique_id(self) -> str:
        """Unique identifier for this Telegram stream."""
        return f"{self.chat_id}:{self.message_id}"

    @classmethod
    def from_db(cls, telegram_stream, stream=None, media=None) -> "TelegramStreamData":
        """Create from database TelegramStream model.

        Args:
            telegram_stream: TelegramStream model instance
            stream: Optional Stream model (will use telegram_stream.stream if not provided)
            media: Optional Media model for meta_id

        Returns:
            TelegramStreamData Pydantic model
        """
        if stream is None:
            stream = telegram_stream.stream

        meta_id = ""
        if media:
            meta_id = f"mf:{media.id}"
        elif stream and stream.media_links:
            first_link = stream.media_links[0]
            if first_link.media:
                meta_id = f"mf:{first_link.media.id}"

        season_number = None
        episode_number = None
        episode_end = None
        if stream and stream.files:
            for f in stream.files:
                if f.media_links:
                    for link in f.media_links:
                        if link.season_number is not None:
                            season_number = link.season_number
                            episode_number = link.episode_number
                            episode_end = link.episode_end
                            break
                    if season_number is not None:
                        break

        return cls(
            telegram_stream_id=telegram_stream.id,  # For URL generation
            chat_id=telegram_stream.chat_id,
            chat_username=telegram_stream.chat_username,
            message_id=telegram_stream.message_id,
            file_id=telegram_stream.file_id,
            file_unique_id=telegram_stream.file_unique_id,
            file_name=telegram_stream.file_name,
            mime_type=telegram_stream.mime_type,
            size=telegram_stream.size,
            posted_at=telegram_stream.posted_at,
            name=stream.name if stream else telegram_stream.file_name or "Unknown",
            source=stream.source if stream else "telegram",
            resolution=stream.resolution if stream else None,
            codec=stream.codec if stream else None,
            quality=stream.quality if stream else None,
            bit_depth=stream.bit_depth if stream else None,
            uploader=stream.uploader if stream else None,
            release_group=stream.release_group if stream else None,
            audio_formats=[af.name for af in stream.audio_formats] if stream and stream.audio_formats else [],
            channels=[ch.name for ch in stream.channels] if stream and stream.channels else [],
            hdr_formats=[hf.name for hf in stream.hdr_formats] if stream and stream.hdr_formats else [],
            languages=[lang.name for lang in stream.languages] if stream and stream.languages else [],
            is_remastered=stream.is_remastered if stream else False,
            is_upscaled=stream.is_upscaled if stream else False,
            is_proper=stream.is_proper if stream else False,
            is_repack=stream.is_repack if stream else False,
            is_extended=stream.is_extended if stream else False,
            is_complete=stream.is_complete if stream else False,
            is_dubbed=stream.is_dubbed if stream else False,
            is_subbed=stream.is_subbed if stream else False,
            is_active=stream.is_active if stream else True,
            is_blocked=stream.is_blocked if stream else False,
            created_at=stream.created_at if stream else None,
            updated_at=stream.updated_at if stream else None,
            meta_id=meta_id,
            season_number=season_number,
            episode_number=episode_number,
            episode_end=episode_end,
        )


class TelegramStreamsList(BaseModel):
    """List of Telegram streams."""

    streams: list[TelegramStreamData]


class HTTPStreamData(BaseModel):
    """HTTP stream data for direct URL streams (M3U imports, etc.).

    Similar to TorrentStreamData but for direct HTTP streams.
    Used for streams imported from M3U playlists (movies, series).
    """

    model_config = {"extra": "allow"}

    # Stream identifiers
    stream_id: int
    url: str
    name: str
    source: str

    # HTTP-specific fields
    format: str | None = None  # mp4, mkv, hls, dash
    size: int | None = None  # File size if known
    bitrate_kbps: int | None = None
    headers: dict | None = None  # Custom headers for playback

    # Quality attributes (same as TorrentStreamData)
    resolution: str | None = None
    codec: str | None = None
    quality: str | None = None
    languages: list[str] = Field(default_factory=list)

    # Visibility
    is_public: bool = True
    uploader_user_id: int | None = None

    # Status
    is_active: bool = True
    is_blocked: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Media linking
    meta_id: str = ""

    # For series - episode info
    season_number: int | None = None
    episode_number: int | None = None

    @classmethod
    def from_db(
        cls,
        http_stream: "HTTPStreamModel",
        stream: "StreamModel" = None,
        media: "MediaModel" = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> "HTTPStreamData":
        """Create from database models."""
        if stream is None:
            stream = http_stream.stream

        meta_id = ""
        if media:
            meta_id = f"mf:{media.id}"

        return cls(
            stream_id=stream.id if stream else http_stream.stream_id,
            url=http_stream.url,
            name=stream.name if stream else "",
            source=stream.source if stream else "m3u",
            format=http_stream.format,
            size=http_stream.size,
            bitrate_kbps=http_stream.bitrate_kbps,
            headers=http_stream.headers,
            resolution=stream.resolution if stream else None,
            codec=stream.codec if stream else None,
            quality=stream.quality if stream else None,
            languages=[lang.name for lang in stream.languages] if stream and stream.languages else [],
            is_public=stream.is_public if stream else True,
            uploader_user_id=stream.uploader_user_id if stream else None,
            is_active=stream.is_active if stream else True,
            is_blocked=stream.is_blocked if stream else False,
            created_at=stream.created_at,
            updated_at=stream.updated_at,
            meta_id=meta_id,
            season_number=season,
            episode_number=episode,
        )


# ============================================
# YouTube Stream Schemas
# ============================================


class YouTubeStreamData(BaseModel):
    """YouTube stream data for movie/series content."""

    model_config = {"extra": "allow"}

    stream_id: int
    video_id: str
    name: str
    source: str

    # Optional quality attributes
    resolution: str | None = None
    quality: str | None = None
    codec: str | None = None
    languages: list[str] = Field(default_factory=list)

    # Required by AnyStreamData protocol (sort key, etc.)
    size: int | None = None
    created_at: datetime | None = None
    meta_id: str = ""

    @classmethod
    def from_db(
        cls,
        yt_stream: "YouTubeStreamModel",
        stream: "StreamModel" = None,
        media: "MediaModel" = None,
    ) -> "YouTubeStreamData":
        """Create from database models."""
        if stream is None:
            stream = yt_stream.stream

        meta_id = ""
        if media:
            meta_id = f"mf:{media.id}"

        return cls(
            stream_id=stream.id if stream else yt_stream.stream_id,
            video_id=yt_stream.video_id,
            name=stream.name if stream else "",
            source=stream.source if stream else "youtube",
            resolution=stream.resolution if stream else None,
            quality=stream.quality if stream else None,
            codec=stream.codec if stream else None,
            languages=[lang.name for lang in stream.languages] if stream and stream.languages else [],
            created_at=stream.created_at if stream else None,
            meta_id=meta_id,
        )


# ============================================
# TV/Live Stream Schemas
# ============================================


class TVStreams(BaseModel):
    """TV stream data for live channels."""

    name: str
    url: str | None = None
    ytId: str | None = None
    source: str
    country: str | None = None
    behaviorHints: StreamBehaviorHints | None = None
    drm_key_id: str | None = None
    drm_key: str | None = None

    @model_validator(mode="after")
    def validate_url_or_yt_id(self) -> "TVStreams":
        if not self.url and not self.ytId:
            raise ValueError("Either url or ytId must be present")
        return self


class TVMetaData(BaseModel):
    """TV channel metadata for input."""

    title: str
    poster: str | None = None
    background: str | None = None
    country: str | None = None
    tv_language: str | None = None
    logo: str | None = None
    genres: list[str] = Field(default_factory=list)
    streams: list[TVStreams]
    namespace: str = Field(default="mediafusion")


# ============================================
# Search/Projection Schemas
# ============================================


class MetaIdProjection(BaseModel):
    """Projection for metadata ID lookup."""

    id: str = Field(alias="_id")
    type: str


class MetaSearchProjection(BaseModel):
    """Projection for metadata search results."""

    id: str = Field(alias="_id")
    title: str
    aka_titles: list[str] | None = Field(default_factory=list)


class TVMetaProjection(BaseModel):
    """Projection for TV metadata."""

    id: str = Field(alias="_id")
    title: str


class KnownFile(BaseModel):
    """File information for known files in a torrent."""

    size: int
    filename: str


class SeriesEpisodeData(BaseModel):
    """Series episode metadata (legacy schema for scrapers)."""

    season_number: int
    episode_number: int
    title: str | None = None
    overview: str | None = None
    released: datetime | None = None
    thumbnail: str | None = None

    @model_validator(mode="after")
    def validate_title(self):
        if not self.title:
            self.title = f"Episode {self.episode_number}"
        return self


class CatalogStats(BaseModel):
    """Catalog statistics for metadata."""

    catalog: str
    total_streams: int = 0
    last_stream_added: datetime | None = None


class MediaFusionEventsMetaData(BaseModel):
    """Events metadata stored in Redis."""

    id: str
    title: str
    description: str | None = None
    poster: str | None = None
    background: str | None = None
    logo: str | None = None
    website: str | None = None
    country: str | None = None
    genres: list[str] = Field(default_factory=list)
    year: int | None = None
    is_add_title_to_poster: bool = False
    event_start_timestamp: int | None = None
    streams: list[TVStreams] = Field(default_factory=list)
