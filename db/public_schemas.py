from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator, ConfigDict

from db.config import settings


class Catalog(BaseModel):
    id: str
    name: str
    type: str


class Video(BaseModel):
    id: str
    title: str
    released: str | None = None
    description: str | None = None
    thumbnail: str | None = None
    season: int | None = None
    episode: int | None = None


class Meta(BaseModel):
    id: str
    name: str = Field(alias="title")
    type: str
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
    end_year: int | None = Field(None, exclude=True)

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def parse_meta(self) -> "Meta":
        if self.releaseInfo:
            if self.type == "series":
                # For series: "2020-2023" or "2020-" (ongoing)
                self.releaseInfo = f"{self.releaseInfo}-{self.end_year if self.end_year else ''}"
            else:
                self.releaseInfo = str(self.releaseInfo)
        if self.imdbRating:
            self.imdbRating = str(self.imdbRating)
        if self.poster is None:
            self.poster = f"{settings.poster_host_url}/poster/{self.type}/{self.id}.jpg"

        return self


class MetaItem(BaseModel):
    meta: Meta


class Metas(BaseModel):
    metas: list[Meta] = Field(default_factory=list)


class StreamBehaviorHints(BaseModel):
    notWebReady: Optional[bool] = None
    bingeGroup: Optional[str] = None
    proxyHeaders: Optional[dict[Literal["request", "response"], dict]] = None
    filename: Optional[str] = None
    videoSize: Optional[int] = None


class Stream(BaseModel):
    name: str
    description: str
    infoHash: str | None = None
    fileIdx: int | None = None
    url: str | None = None
    ytId: str | None = None
    externalUrl: str | None = None
    behaviorHints: StreamBehaviorHints | None = None
    sources: list[str] | None = None


class Streams(BaseModel):
    streams: Optional[list[Stream]] = Field(default_factory=list)
