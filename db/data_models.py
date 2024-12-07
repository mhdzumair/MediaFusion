from datetime import datetime
from typing import List, Optional, Callable, Any
from pydantic import BaseModel, Field, field_validator, ConfigDict

from db.enums import MediaType, NudityStatus, IndexerType


def create_string_list_validator(attribute_name: str = "name") -> Callable:
    """
    Creates a validator function for converting various inputs to a list of strings.

    Args:
        attribute_name (str): The attribute to extract from dict/object (default: 'name')

    Returns:
        Callable: A validator function that converts input to List[str]
    """

    def validator(v: Any) -> List[str]:
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
    year: Optional[int] = None
    poster: Optional[str] = None
    is_poster_working: bool = True
    is_add_title_to_poster: bool = False
    background: Optional[str] = None
    description: Optional[str] = None
    runtime: Optional[str] = None
    website: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    # Common relationship fields
    genres: List[str] = Field(default_factory=list)
    catalogs: List[str] = Field(default_factory=list)
    alternate_titles: List[str] = Field(default_factory=list)

    # Validators using the helper function
    _validate_genres = field_validator("genres", mode="before")(
        create_string_list_validator()
    )
    _validate_catalogs = field_validator("catalogs", mode="before")(
        create_string_list_validator()
    )
    _validate_alternate_titles = field_validator("alternate_titles", mode="before")(
        create_string_list_validator("title")
    )


class MovieData(BasePydanticModel):
    """Movie metadata data model"""

    id: str
    base_metadata: BaseMediaData | None = None
    type: MediaType = MediaType.MOVIE
    imdb_rating: Optional[float] = None
    parent_guide_nudity_status: NudityStatus = NudityStatus.UNKNOWN
    stars: List[str] = Field(default_factory=list)
    parental_certificates: List[str] = Field(default_factory=list)

    _validate_stars = field_validator("stars", mode="before")(
        create_string_list_validator()
    )
    _validate_certificates = field_validator("parental_certificates", mode="before")(
        create_string_list_validator()
    )


class SeriesEpisodeData(BasePydanticModel):
    """Series episode data model"""

    episode_number: int
    title: str
    overview: Optional[str] = None
    released: Optional[datetime] = None
    imdb_rating: Optional[float] = None
    thumbnail: Optional[str] = None


class SeriesSeasonData(BasePydanticModel):
    """Series season data model"""

    season_number: int
    episodes: List[SeriesEpisodeData] = []


class SeriesData(BasePydanticModel):
    """Series metadata data model"""

    id: str
    base_metadata: BaseMediaData | None = None
    type: MediaType = MediaType.SERIES
    end_year: Optional[int] = None
    imdb_rating: Optional[float] = None
    parent_guide_nudity_status: NudityStatus = NudityStatus.UNKNOWN
    stars: List[str] = Field(default_factory=list)
    parental_certificates: List[str] = Field(default_factory=list)

    seasons: List[SeriesSeasonData] = []

    # Validators using the helper function
    _validate_stars = field_validator("stars", mode="before")(
        create_string_list_validator()
    )
    _validate_certificates = field_validator("parental_certificates", mode="before")(
        create_string_list_validator()
    )


class TVData(BasePydanticModel):
    """TV metadata data model"""

    id: str
    base_metadata: BaseMediaData | None = None
    type: MediaType = MediaType.TV
    country: Optional[str] = None
    tv_language: Optional[str] = None
    logo: Optional[str] = None


class EpisodeFileData(BasePydanticModel):
    """Episode file data model"""

    season_number: int
    episode_number: int
    file_index: Optional[int] = None
    filename: Optional[str] = None
    size: Optional[int] = None


class TorrentStreamData(BasePydanticModel):
    """Torrent stream data model"""

    id: str
    torrent_name: str
    size: int
    filename: Optional[str] = None
    file_index: Optional[int] = None
    source: str
    resolution: Optional[str] = None
    codec: Optional[str] = None
    quality: Optional[str] = None
    audio: Optional[str] = None
    seeders: Optional[int] = None
    is_blocked: bool = False
    indexer_flag: IndexerType = IndexerType.FREELEACH
    created_at: datetime
    updated_at: Optional[datetime] = None

    episode_files: List[EpisodeFileData] = []


class TVStreamData(BasePydanticModel):
    """TV stream data model"""

    id: int
    name: str
    url: Optional[str] = None
    ytId: Optional[str] = None
    externalUrl: Optional[str] = None
    source: str
    country: Optional[str] = None
    is_working: bool = True
    test_failure_count: int = 0
    drm_key_id: Optional[str] = None
    drm_key: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
