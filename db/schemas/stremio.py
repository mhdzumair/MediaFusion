"""Stremio addon schemas for catalog and stream responses."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PosterData(BaseModel):
    """Data required for poster generation."""

    id: str
    poster: str
    title: str
    imdb_rating: float | None = None
    is_add_title_to_poster: bool = False


class Catalog(BaseModel):
    """Stremio catalog definition."""

    id: str
    name: str
    type: str


class Video(BaseModel):
    """Stremio video (episode) definition."""

    id: str
    title: str
    released: str | None = None
    season: int | None = None
    episode: int | None = None
    thumbnail: str | None = None


class Meta(BaseModel):
    """Stremio metadata object."""

    id: str = Field(alias="_id")
    name: str = Field(alias="title")
    type: str = Field(default="movie")
    poster: str | None = None
    background: str | None = None
    videos: list[Video] | None = None
    country: str | None = None
    language: str | None = Field(None, alias="tv_language")
    logo: str | None = None
    genres: list[str] | None = None
    description: str | None = None
    runtime: str | None = None
    website: str | None = None
    imdbRating: str | float | None = Field(None, alias="imdb_rating")
    releaseInfo: str | int | None = Field(None, alias="year")
    cast: list[str] | None = Field(None, alias="stars")

    @model_validator(mode="after")
    def parse_meta(self) -> "Meta":
        if self.releaseInfo:
            self.releaseInfo = f"{self.releaseInfo}-" if self.type == "series" else str(self.releaseInfo)
        if self.imdbRating:
            self.imdbRating = str(self.imdbRating)
        return self


class MetaItem(BaseModel):
    """Wrapper for a single meta object."""

    meta: Meta


class Metas(BaseModel):
    """Collection of meta objects."""

    metas: list[Meta] = Field(default_factory=list)


class StreamBehaviorHints(BaseModel):
    """Stremio stream behavior hints."""

    notWebReady: bool | None = None
    bingeGroup: str | None = None
    proxyHeaders: dict[Literal["request", "response"], dict] | None = None
    filename: str | None = None
    videoSize: int | None = None


class Stream(BaseModel):
    """Stremio stream object."""

    name: str
    description: str
    infoHash: str | None = None
    fileIdx: int | None = None
    url: str | None = None
    nzbUrl: str | None = None  # Direct NZB URL for Stremio v5 native NNTP streaming
    ytId: str | None = None
    externalUrl: str | None = None
    behaviorHints: StreamBehaviorHints | None = None
    sources: list[str] | None = None


class Streams(BaseModel):
    """Collection of streams."""

    streams: list[Stream] | None = Field(default_factory=list)


class RichStreamMetadata(BaseModel):
    """Rich metadata for frontend display - supplements the Stremio Stream format."""

    id: str  # stream_id - used as stream ID for voting/editing
    info_hash: str
    name: str  # Stream display name (torrent title)

    # Quality attributes (normalized from separate tables)
    resolution: str | None = None  # 4k, 1080p, 720p, 480p
    quality: str | None = None  # web-dl, bluray, cam, hdtv
    codec: str | None = None  # x264, x265, hevc, av1
    bit_depth: str | None = None  # 8bit, 10bit, 12bit

    # Multi-value quality attributes (from normalized tables)
    audio_formats: list[str] = Field(default_factory=list)  # AAC, DTS, Atmos, TrueHD
    channels: list[str] = Field(default_factory=list)  # 2.0, 5.1, 7.1
    hdr_formats: list[str] = Field(default_factory=list)  # HDR10, HDR10+, Dolby Vision
    languages: list[str] = Field(default_factory=list)

    # Release flags
    is_remastered: bool = False
    is_upscaled: bool = False
    is_proper: bool = False
    is_repack: bool = False
    is_extended: bool = False
    is_complete: bool = False
    is_dubbed: bool = False
    is_subbed: bool = False

    # Stream info
    source: str
    size: int | None = None  # size in bytes
    size_display: str | None = None  # formatted size (e.g., "2.5 GB")
    seeders: int | None = None
    uploader: str | None = None
    uploaded_at: str | None = None
    cached: bool = False


class RichStream(BaseModel):
    """Combined stream data for both Stremio addon and frontend API."""

    # Stremio-compatible stream (for addon endpoint)
    stream: Stream
    # Rich metadata for frontend (for catalog API)
    metadata: RichStreamMetadata
