from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from db.enums import MediaType, NudityStatus, TorrentType


def create_string_list_validator(attribute_name: str = "name") -> Callable:
    """
    Creates a validator function for converting various inputs to a list of strings.

    Args:
        attribute_name (str): The attribute to extract from dict/object (default: 'name')

    Returns:
        Callable: A validator function that converts input to List[str]
    """

    def validator(v: Any) -> list[str]:
        if not v:
            return []

        if isinstance(v, list):
            return [
                # Handle dictionaries
                (
                    item.get(attribute_name)
                    if isinstance(item, dict)
                    # Handle objects
                    else (
                        getattr(item, attribute_name)
                        if hasattr(item, attribute_name)
                        # Handle direct strings
                        else str(item)
                    )
                )
                for item in v
            ]

        if isinstance(v, str):
            return [v]

        raise ValueError(
            f"Invalid input type. Expected list of strings, dicts with '{attribute_name}' key, "
            f"or objects with '{attribute_name}' attribute"
        )

    return validator


class BasePydanticModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BaseMediaData(BasePydanticModel):
    """Base model for common metadata fields"""

    id: str
    title: str
    type: str
    year: int | None = None
    poster: str | None = None
    is_poster_working: bool = True
    is_add_title_to_poster: bool = False
    background: str | None = None
    description: str | None = None
    runtime: str | None = None
    website: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    # Common relationship fields
    genres: list[str] = Field(default_factory=list)
    catalogs: list[str] = Field(default_factory=list)
    alternate_titles: list[str] = Field(default_factory=list)

    # Validators using the helper function
    _validate_genres = field_validator("genres", mode="before")(create_string_list_validator())
    _validate_catalogs = field_validator("catalogs", mode="before")(create_string_list_validator())
    _validate_alternate_titles = field_validator("alternate_titles", mode="before")(
        create_string_list_validator("title")
    )


class MovieData(BasePydanticModel):
    """Movie metadata data model"""

    id: str
    base_metadata: BaseMediaData | None = None
    type: MediaType = MediaType.MOVIE
    imdb_rating: float | None = None
    tmdb_rating: float | None = None
    parent_guide_nudity_status: NudityStatus = NudityStatus.UNKNOWN
    stars: list[str] = Field(default_factory=list)
    parental_certificates: list[str] = Field(default_factory=list)

    _validate_stars = field_validator("stars", mode="before")(create_string_list_validator())
    _validate_certificates = field_validator("parental_certificates", mode="before")(create_string_list_validator())


class SeriesEpisodeData(BasePydanticModel):
    """Series episode data model"""

    episode_number: int
    title: str
    overview: str | None = None
    released: datetime | None = None
    imdb_rating: float | None = None
    thumbnail: str | None = None


class SeriesSeasonData(BasePydanticModel):
    """Series season data model"""

    season_number: int
    episodes: list[SeriesEpisodeData] = []


class SeriesData(BasePydanticModel):
    """Series metadata data model"""

    id: str
    base_metadata: BaseMediaData | None = None
    type: MediaType = MediaType.SERIES
    end_year: int | None = None
    imdb_rating: float | None = None
    tmdb_rating: float | None = None
    parent_guide_nudity_status: NudityStatus = NudityStatus.UNKNOWN
    stars: list[str] = Field(default_factory=list)
    parental_certificates: list[str] = Field(default_factory=list)

    seasons: list[SeriesSeasonData] = []

    # Validators using the helper function
    _validate_stars = field_validator("stars", mode="before")(create_string_list_validator())
    _validate_certificates = field_validator("parental_certificates", mode="before")(create_string_list_validator())


class TVData(BasePydanticModel):
    """TV metadata data model"""

    id: str
    base_metadata: BaseMediaData | None = None
    type: MediaType = MediaType.TV
    country: str | None = None
    tv_language: str | None = None
    logo: str | None = None


class EpisodeFileData(BasePydanticModel):
    """Episode file data model"""

    season_number: int
    episode_number: int
    file_index: int | None = None
    filename: str | None = None
    size: int | None = None


class TorrentStreamData(BasePydanticModel):
    """Torrent stream data model"""

    id: str
    torrent_name: str
    size: int
    filename: str | None = None
    file_index: int | None = None
    source: str
    resolution: str | None = None
    codec: str | None = None
    quality: str | None = None
    audio: str | None = None
    seeders: int | None = None
    is_blocked: bool = False
    torrent_type: TorrentType = TorrentType.PUBLIC
    uploader: str | None = None
    uploaded_at: datetime | None = None
    hdr: list[str] | None = None
    created_at: datetime
    updated_at: datetime | None = None

    episode_files: list[EpisodeFileData] = []


class TVStreamData(BasePydanticModel):
    """TV stream data model"""

    id: int
    name: str
    url: str | None = None
    ytId: str | None = None
    externalUrl: str | None = None
    source: str
    country: str | None = None
    is_active: bool = True
    is_blocked: bool = False
    test_failure_count: int = 0
    drm_key_id: str | None = None
    drm_key: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
