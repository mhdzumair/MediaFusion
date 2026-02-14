"""
User Metadata API endpoints for creating and managing user-created content.
Supports creating movies, series, seasons, and episodes.
"""

import hashlib
import logging
from datetime import date, datetime
from typing import Literal

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlalchemy import delete as sa_delete
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth, require_role
from db.enums import UserRole
from db.crud.media import add_external_id, get_media_by_external_id, get_media_by_id
from db.crud.providers import get_or_create_provider
from db.crud.reference import get_or_create_catalog, get_or_create_genre, get_or_create_parental_certificate
from db.database import get_async_session
from db.enums import MediaType, NudityStatus
from db.models import (
    Catalog,
    Episode,
    Genre,
    Media,
    MediaCatalogLink,
    MediaExternalID,
    MediaGenreLink,
    MediaImage,
    MediaParentalCertificateLink,
    MovieMetadata,
    Season,
    SeriesMetadata,
    StreamMediaLink,
    User,
)
from db.models.cast_crew import MediaCast, MediaCrew, Person
from db.models.reference import AkaTitle, ParentalCertificate
from db.models.streams import FileMediaLink
from db.models.providers import EpisodeImage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/metadata/user", tags=["User Metadata"])


# ============================================
# Pydantic Schemas
# ============================================


class UserEpisodeCreate(BaseModel):
    """Request to create an episode."""

    episode_number: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=500)
    overview: str | None = None
    air_date: date | None = None
    runtime_minutes: int | None = Field(None, ge=1)


class UserSeasonCreate(BaseModel):
    """Request to create a season with episodes."""

    season_number: int = Field(..., ge=0)  # 0 for specials
    name: str | None = None
    overview: str | None = None
    air_date: date | None = None
    episodes: list[UserEpisodeCreate] = Field(default_factory=list)


class UserMediaCreate(BaseModel):
    """Request to create user metadata (movie, series, or tv)."""

    type: Literal["movie", "series", "tv"]
    title: str = Field(..., min_length=1, max_length=500)
    year: int | None = Field(None, ge=1800, le=2100)
    description: str | None = None
    poster_url: str | None = None
    background_url: str | None = None
    genres: list[str] | None = None
    catalogs: list[str] | None = None
    external_ids: dict[str, str] | None = Field(None, description="External IDs like {'imdb': 'tt123', 'tmdb': '456'}")
    is_public: bool = True
    runtime_minutes: int | None = Field(None, ge=1)  # For movies
    # For series - optional inline season/episode creation
    seasons: list[UserSeasonCreate] | None = None


class UserMediaUpdate(BaseModel):
    """Request to update user metadata."""

    title: str | None = Field(None, min_length=1, max_length=500)
    original_title: str | None = Field(None, max_length=500)
    year: int | None = Field(None, ge=1800, le=2100)
    description: str | None = None
    tagline: str | None = None
    poster_url: str | None = None
    background_url: str | None = None
    logo_url: str | None = None
    genres: list[str] | None = None
    catalogs: list[str] | None = None
    is_public: bool | None = None
    runtime_minutes: int | None = Field(None, ge=1)
    release_date: date | None = None
    status: str | None = Field(None, max_length=50)
    website: str | None = None
    original_language: str | None = Field(None, max_length=10)
    nudity_status: str | None = Field(None, description="One of: None, Mild, Moderate, Severe, Unknown, Disable")
    aka_titles: list[str] | None = Field(None, description="Alternative titles for the media")
    cast: list[str] | None = Field(None, description="Cast member names")
    directors: list[str] | None = Field(None, description="Director names")
    writers: list[str] | None = Field(None, description="Writer names")
    parental_certificate: str | None = Field(None, description="Parental rating/certificate")
    external_ids: dict[str, str] | None = Field(None, description="External IDs like {'imdb': 'tt123', 'tmdb': '456'}")


class EpisodeResponse(BaseModel):
    """Episode response."""

    id: int
    episode_number: int
    title: str
    overview: str | None = None
    air_date: date | None = None
    runtime_minutes: int | None = None
    is_user_created: bool = False
    is_user_addition: bool = False

    class Config:
        from_attributes = True


class SeasonResponse(BaseModel):
    """Season response."""

    id: int
    season_number: int
    name: str | None = None
    overview: str | None = None
    air_date: date | None = None
    episode_count: int = 0
    episodes: list[EpisodeResponse] = []

    class Config:
        from_attributes = True


class UserMediaResponse(BaseModel):
    """Response for user-created metadata."""

    id: int
    type: str
    title: str
    original_title: str | None = None
    year: int | None = None
    description: str | None = None
    tagline: str | None = None
    poster_url: str | None = None
    background_url: str | None = None
    logo_url: str | None = None
    genres: list[str] = []
    catalogs: list[str] = []
    external_ids: dict[str, str] = {}
    is_public: bool = True
    is_user_created: bool = True
    created_by_user_id: int | None = None
    total_streams: int = 0
    created_at: datetime
    updated_at: datetime | None = None
    runtime_minutes: int | None = None
    release_date: date | None = None
    status: str | None = None
    website: str | None = None
    original_language: str | None = None
    nudity_status: str | None = None
    aka_titles: list[str] = []
    cast: list[str] = []
    directors: list[str] = []
    writers: list[str] = []
    parental_certificate: str | None = None
    # For series
    total_seasons: int | None = None
    total_episodes: int | None = None
    seasons: list[SeasonResponse] | None = None

    class Config:
        from_attributes = True


class UserMediaListResponse(BaseModel):
    """Response for listing user metadata."""

    items: list[UserMediaResponse]
    total: int
    page: int
    per_page: int
    pages: int


class SeasonAddRequest(BaseModel):
    """Request to add a season to a series."""

    season_number: int = Field(..., ge=0)
    name: str | None = None
    overview: str | None = None
    air_date: date | None = None
    episodes: list[UserEpisodeCreate] = Field(default_factory=list)


class EpisodeAddRequest(BaseModel):
    """Request to add episodes to a season."""

    season_number: int = Field(..., ge=0)
    episodes: list[UserEpisodeCreate] = Field(..., min_length=1)


class EpisodeUpdateRequest(BaseModel):
    """Request to update an episode."""

    title: str | None = Field(None, min_length=1, max_length=500)
    overview: str | None = None
    air_date: date | None = None
    runtime_minutes: int | None = Field(None, ge=1)


class ImportFromExternalRequest(BaseModel):
    """Request to import metadata from an external provider."""

    provider: Literal["imdb", "tmdb", "tvdb", "mal", "kitsu"] = Field(
        ..., description="External provider to import from"
    )
    external_id: str = Field(..., description="External ID (e.g., tt1234567 for IMDb, 12345 for TMDB)")
    media_type: Literal["movie", "series", "tv"]
    is_public: bool = True


class ImportPreviewResponse(BaseModel):
    """Preview of metadata to be imported."""

    provider: str
    external_id: str
    title: str
    year: int | None = None
    description: str | None = None
    poster: str | None = None
    background: str | None = None
    genres: list[str] = []
    runtime: str | None = None
    imdb_id: str | None = None
    tmdb_id: str | None = None
    tvdb_id: str | int | None = None
    mal_id: str | int | None = None
    kitsu_id: str | int | None = None


# ============================================
# Helper Functions
# ============================================


def generate_user_external_id(title: str, user_id: int) -> str:
    """Generate a unique external ID for user-created content."""
    hash_input = f"{title.lower()}_{user_id}_{datetime.now(pytz.UTC).isoformat()}"
    hash_value = hashlib.md5(hash_input.encode()).hexdigest()[:12]
    return f"mf_user_{hash_value}"


async def get_media_images(session: AsyncSession, media_id: int) -> tuple[str | None, str | None, str | None]:
    """Get poster, background, and logo URLs for a media item."""
    result = await session.exec(select(MediaImage).where(MediaImage.media_id == media_id))
    images = result.all()

    poster_url = None
    background_url = None
    logo_url = None

    for img in images:
        if img.image_type == "poster" and not poster_url:
            poster_url = img.url
        elif img.image_type in ("backdrop", "background") and not background_url:
            background_url = img.url
        elif img.image_type == "logo" and not logo_url:
            logo_url = img.url

    return poster_url, background_url, logo_url


async def _get_or_create_person(session: AsyncSession, name: str) -> Person:
    """Get or create a person by name."""
    result = await session.exec(select(Person).where(Person.name == name))
    person = result.first()
    if not person:
        person = Person(name=name)
        session.add(person)
        await session.flush()
    return person


async def format_media_response(
    session: AsyncSession,
    media: Media,
    include_seasons: bool = False,
) -> UserMediaResponse:
    """Format a Media object into a UserMediaResponse."""
    # Get external IDs
    ext_ids_result = await session.exec(select(MediaExternalID).where(MediaExternalID.media_id == media.id))
    ext_ids = {ext.provider: ext.external_id for ext in ext_ids_result.all()}

    # Get images
    poster_url, background_url, logo_url = await get_media_images(session, media.id)

    # Get genres
    genres_result = await session.exec(
        select(Genre)
        .join(MediaGenreLink, MediaGenreLink.genre_id == Genre.id)
        .where(MediaGenreLink.media_id == media.id)
    )
    genres = [g.name for g in genres_result.all()]

    # Get catalogs
    catalogs_result = await session.exec(
        select(Catalog)
        .join(MediaCatalogLink, MediaCatalogLink.catalog_id == Catalog.id)
        .where(MediaCatalogLink.media_id == media.id)
    )
    catalogs = [c.name for c in catalogs_result.all()]

    # Get AKA titles
    aka_result = await session.exec(select(AkaTitle).where(AkaTitle.media_id == media.id))
    aka_titles = [aka.title for aka in aka_result.all()]

    # Get cast
    cast_result = await session.exec(
        select(Person)
        .join(MediaCast, MediaCast.person_id == Person.id)
        .where(MediaCast.media_id == media.id)
        .order_by(MediaCast.display_order)
    )
    cast = [p.name for p in cast_result.all()]

    # Get directors
    directors_result = await session.exec(
        select(Person)
        .join(MediaCrew, MediaCrew.person_id == Person.id)
        .where(
            MediaCrew.media_id == media.id,
            MediaCrew.department == "Directing",
        )
    )
    directors = [p.name for p in directors_result.all()]

    # Get writers
    writers_result = await session.exec(
        select(Person)
        .join(MediaCrew, MediaCrew.person_id == Person.id)
        .where(
            MediaCrew.media_id == media.id,
            MediaCrew.department == "Writing",
        )
    )
    writers = [p.name for p in writers_result.all()]

    # Get parental certificate via media link table
    parental_certificate = None
    cert_result = await session.exec(
        select(ParentalCertificate)
        .join(
            MediaParentalCertificateLink,
            MediaParentalCertificateLink.certificate_id == ParentalCertificate.id,
        )
        .where(MediaParentalCertificateLink.media_id == media.id)
        .limit(1)
    )
    cert = cert_result.first()
    if cert:
        parental_certificate = cert.name

    # Build response
    response = UserMediaResponse(
        id=media.id,
        type=media.type.value.lower(),
        title=media.title,
        original_title=media.original_title,
        year=media.year,
        description=media.description,
        tagline=media.tagline,
        poster_url=poster_url,
        background_url=background_url,
        logo_url=logo_url,
        genres=genres,
        catalogs=catalogs,
        external_ids=ext_ids,
        is_public=media.is_public,
        is_user_created=media.is_user_created,
        created_by_user_id=media.created_by_user_id,
        total_streams=media.total_streams,
        created_at=media.created_at,
        updated_at=media.updated_at,
        runtime_minutes=media.runtime_minutes,
        release_date=media.release_date,
        status=media.status,
        website=media.website,
        original_language=media.original_language,
        nudity_status=media.nudity_status.value if media.nudity_status else None,
        aka_titles=aka_titles,
        cast=cast,
        directors=directors,
        writers=writers,
        parental_certificate=parental_certificate,
    )

    # Add series-specific data
    if media.type == MediaType.SERIES and include_seasons:
        series_result = await session.exec(select(SeriesMetadata).where(SeriesMetadata.media_id == media.id))
        series_meta = series_result.first()

        if series_meta:
            response.total_seasons = series_meta.total_seasons
            response.total_episodes = series_meta.total_episodes

            # Load seasons with episodes
            seasons_result = await session.exec(
                select(Season)
                .where(Season.series_id == series_meta.id)
                .order_by(Season.season_number)
                .options(selectinload(Season.episodes))
            )
            seasons = seasons_result.all()

            response.seasons = []
            for season in seasons:
                episodes = [
                    EpisodeResponse(
                        id=ep.id,
                        episode_number=ep.episode_number,
                        title=ep.title,
                        overview=ep.overview,
                        air_date=ep.air_date,
                        runtime_minutes=ep.runtime_minutes,
                        is_user_created=ep.is_user_created,
                        is_user_addition=ep.is_user_addition,
                    )
                    for ep in sorted(season.episodes, key=lambda e: e.episode_number)
                ]
                response.seasons.append(
                    SeasonResponse(
                        id=season.id,
                        season_number=season.season_number,
                        name=season.name,
                        overview=season.overview,
                        air_date=season.air_date,
                        episode_count=season.episode_count,
                        episodes=episodes,
                    )
                )

    return response


# ============================================
# API Endpoints
# ============================================


@router.post("", response_model=UserMediaResponse, status_code=status.HTTP_201_CREATED)
async def create_user_metadata(
    request: UserMediaCreate,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Create user-generated metadata (movie, series, or tv).

    For series, you can optionally include seasons and episodes inline.
    """
    # Determine media type
    if request.type == "movie":
        media_type = MediaType.MOVIE
    elif request.type == "series":
        media_type = MediaType.SERIES
    else:
        media_type = MediaType.TV

    # Generate external ID
    external_id = generate_user_external_id(request.title, user.id)

    # Create base media record
    media = Media(
        type=media_type,
        title=request.title,
        year=request.year,
        description=request.description,
        runtime_minutes=request.runtime_minutes,
        is_user_created=True,
        created_by_user_id=user.id,
        is_public=request.is_public,
    )
    session.add(media)
    await session.flush()

    # Add external ID to MediaExternalID table
    await add_external_id(session, media.id, "mediafusion", external_id)

    # Add additional external IDs if provided
    if request.external_ids:
        for provider, ext_id in request.external_ids.items():
            if provider != "mediafusion":
                await add_external_id(session, media.id, provider, ext_id)

    # Add genres
    if request.genres:
        for genre_name in request.genres:
            genre = await get_or_create_genre(session, genre_name)
            link = MediaGenreLink(media_id=media.id, genre_id=genre.id)
            session.add(link)

    # Add catalogs
    if request.catalogs:
        for catalog_name in request.catalogs:
            catalog = await get_or_create_catalog(session, catalog_name)
            link = MediaCatalogLink(media_id=media.id, catalog_id=catalog.id)
            session.add(link)

    # Add images
    if request.poster_url:
        poster = MediaImage(
            media_id=media.id,
            image_type="poster",
            url=request.poster_url,
            provider="user",
        )
        session.add(poster)

    if request.background_url:
        background = MediaImage(
            media_id=media.id,
            image_type="backdrop",
            url=request.background_url,
            provider="user",
        )
        session.add(background)

    # Create type-specific metadata
    if media_type == MediaType.MOVIE:
        movie_meta = MovieMetadata(media_id=media.id)
        session.add(movie_meta)
    elif media_type == MediaType.SERIES:
        # Series
        total_episodes = 0
        if request.seasons:
            for season_data in request.seasons:
                total_episodes += len(season_data.episodes)

        series_meta = SeriesMetadata(
            media_id=media.id,
            total_seasons=len(request.seasons) if request.seasons else 0,
            total_episodes=total_episodes,
        )
        session.add(series_meta)
        await session.flush()

        # Create seasons and episodes if provided
        if request.seasons:
            for season_data in request.seasons:
                season = Season(
                    series_id=series_meta.id,
                    season_number=season_data.season_number,
                    name=season_data.name,
                    overview=season_data.overview,
                    air_date=season_data.air_date,
                    episode_count=len(season_data.episodes),
                )
                session.add(season)
                await session.flush()

                # Create episodes
                for ep_data in season_data.episodes:
                    episode = Episode(
                        season_id=season.id,
                        episode_number=ep_data.episode_number,
                        title=ep_data.title,
                        overview=ep_data.overview,
                        air_date=ep_data.air_date,
                        runtime_minutes=ep_data.runtime_minutes,
                        is_user_created=True,
                        created_by_user_id=user.id,
                    )
                    session.add(episode)
    # TV type doesn't need additional metadata tables (similar to movie but for live TV)

    await session.commit()
    await session.refresh(media)

    logger.info(f"User {user.id} created {media_type.value} metadata: {media.title} (id={media.id})")

    return await format_media_response(session, media, include_seasons=True)


@router.get("", response_model=UserMediaListResponse)
async def list_user_metadata(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    type: Literal["movie", "series", "tv", "all"] = "all",
    search: str | None = Query(None, min_length=2),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    List user-created metadata for the current user.
    """
    # Base query for user's content
    query = select(Media).where(
        Media.created_by_user_id == user.id,
        Media.is_user_created == True,
    )

    # Filter by type
    if type == "movie":
        query = query.where(Media.type == MediaType.MOVIE)
    elif type == "series":
        query = query.where(Media.type == MediaType.SERIES)
    elif type == "tv":
        query = query.where(Media.type == MediaType.TV)

    # Search filter
    if search:
        query = query.where(Media.title.ilike(f"%{search}%"))

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Paginate
    query = query.order_by(Media.created_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await session.exec(query)
    media_items = result.all()

    # Format responses
    items = []
    for media in media_items:
        items.append(await format_media_response(session, media, include_seasons=False))

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    return UserMediaListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.get("/{media_id}", response_model=UserMediaResponse)
async def get_user_metadata(
    media_id: int,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get details of user-created metadata.
    """
    media = await get_media_by_id(session, media_id)

    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    # Check ownership or public access
    if media.created_by_user_id != user.id and not media.is_public:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this metadata",
        )

    return await format_media_response(session, media, include_seasons=True)


@router.put("/{media_id}", response_model=UserMediaResponse)
async def update_user_metadata(
    media_id: int,
    request: UserMediaUpdate,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Update user-created metadata.
    Only the owner can update their metadata.
    """
    media = await get_media_by_id(session, media_id)

    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    # Check ownership
    if media.created_by_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own metadata",
        )

    # Update basic fields
    if request.title is not None:
        media.title = request.title
    if request.year is not None:
        media.year = request.year
    if request.description is not None:
        media.description = request.description
    if request.is_public is not None:
        media.is_public = request.is_public
    if request.runtime_minutes is not None:
        media.runtime_minutes = request.runtime_minutes

    media.updated_at = datetime.now(pytz.UTC)

    # Update additional basic fields
    if request.original_title is not None:
        media.original_title = request.original_title if request.original_title else None
    if request.tagline is not None:
        media.tagline = request.tagline if request.tagline else None
    if request.release_date is not None:
        media.release_date = request.release_date
    if request.status is not None:
        media.status = request.status if request.status else None
    if request.website is not None:
        media.website = request.website if request.website else None
    if request.original_language is not None:
        media.original_language = request.original_language if request.original_language else None
    if request.nudity_status is not None:
        try:
            media.nudity_status = NudityStatus(request.nudity_status)
        except ValueError:
            pass  # Keep existing value if invalid

    # Update images
    if request.poster_url is not None:
        # Remove existing poster
        await session.exec(
            sa_delete(MediaImage).where(
                MediaImage.media_id == media_id,
                MediaImage.image_type == "poster",
            )
        )
        # Add new poster if provided
        if request.poster_url:
            user_provider = await get_or_create_provider(session, "user")
            poster = MediaImage(
                media_id=media.id,
                provider_id=user_provider.id,
                image_type="poster",
                url=request.poster_url,
                is_primary=True,
            )
            session.add(poster)

    if request.background_url is not None:
        # Remove existing background
        await session.exec(
            sa_delete(MediaImage).where(
                MediaImage.media_id == media_id,
                MediaImage.image_type == "backdrop",
            )
        )
        # Add new background if provided
        if request.background_url:
            user_provider = await get_or_create_provider(session, "user")
            background = MediaImage(
                media_id=media.id,
                provider_id=user_provider.id,
                image_type="backdrop",
                url=request.background_url,
            )
            session.add(background)

    # Update genres
    if request.genres is not None:
        # Remove existing genre links
        await session.exec(sa_delete(MediaGenreLink).where(MediaGenreLink.media_id == media_id))
        # Add new genres
        for genre_name in request.genres:
            genre = await get_or_create_genre(session, genre_name)
            link = MediaGenreLink(media_id=media.id, genre_id=genre.id)
            session.add(link)

    # Update catalogs
    if request.catalogs is not None:
        await session.exec(sa_delete(MediaCatalogLink).where(MediaCatalogLink.media_id == media_id))
        for catalog_name in request.catalogs:
            catalog = await get_or_create_catalog(session, catalog_name)
            link = MediaCatalogLink(media_id=media.id, catalog_id=catalog.id)
            session.add(link)

    # Update logo image
    if request.logo_url is not None:
        await session.exec(
            sa_delete(MediaImage).where(
                MediaImage.media_id == media_id,
                MediaImage.image_type == "logo",
            )
        )
        if request.logo_url:
            user_provider = await get_or_create_provider(session, "user")
            logo = MediaImage(
                media_id=media.id,
                provider_id=user_provider.id,
                image_type="logo",
                url=request.logo_url,
            )
            session.add(logo)

    # Update external IDs
    if request.external_ids is not None:
        for provider, ext_id in request.external_ids.items():
            if ext_id:
                await add_external_id(session, media.id, provider, ext_id)

    # Update AKA titles
    if request.aka_titles is not None:
        await session.exec(sa_delete(AkaTitle).where(AkaTitle.media_id == media_id))
        for aka_title in request.aka_titles:
            if aka_title.strip():
                aka = AkaTitle(media_id=media.id, title=aka_title.strip())
                session.add(aka)

    # Update parental certificate via link table
    if request.parental_certificate is not None:
        # Clear existing certificate links
        await session.exec(
            sa_delete(MediaParentalCertificateLink).where(
                MediaParentalCertificateLink.media_id == media.id
            )
        )
        if request.parental_certificate:
            cert = await get_or_create_parental_certificate(session, request.parental_certificate)
            link = MediaParentalCertificateLink(media_id=media.id, certificate_id=cert.id)
            session.add(link)

    # Update cast
    if request.cast is not None:
        await session.exec(sa_delete(MediaCast).where(MediaCast.media_id == media_id))
        for i, actor_name in enumerate(request.cast):
            if actor_name.strip():
                person = await _get_or_create_person(session, actor_name.strip())
                cast_link = MediaCast(
                    media_id=media.id,
                    person_id=person.id,
                    display_order=i,
                )
                session.add(cast_link)

    # Update directors
    if request.directors is not None:
        await session.exec(
            sa_delete(MediaCrew).where(
                MediaCrew.media_id == media_id,
                MediaCrew.department == "Directing",
            )
        )
        for director_name in request.directors:
            if director_name.strip():
                person = await _get_or_create_person(session, director_name.strip())
                crew_link = MediaCrew(
                    media_id=media.id,
                    person_id=person.id,
                    department="Directing",
                    job="Director",
                )
                session.add(crew_link)

    # Update writers
    if request.writers is not None:
        await session.exec(
            sa_delete(MediaCrew).where(
                MediaCrew.media_id == media_id,
                MediaCrew.department == "Writing",
            )
        )
        for writer_name in request.writers:
            if writer_name.strip():
                person = await _get_or_create_person(session, writer_name.strip())
                crew_link = MediaCrew(
                    media_id=media.id,
                    person_id=person.id,
                    department="Writing",
                    job="Writer",
                )
                session.add(crew_link)

    await session.commit()
    await session.refresh(media)

    logger.info(f"User {user.id} updated metadata: {media.title} (id={media.id})")

    return await format_media_response(session, media, include_seasons=True)


@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_metadata(
    media_id: int,
    force: bool = Query(False, description="Force delete even if streams are linked"),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete user-created metadata.
    Only the owner can delete their metadata.
    Cannot delete if streams are linked (unless force=True).
    """
    media = await get_media_by_id(session, media_id)

    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    # Check ownership
    if media.created_by_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own metadata",
        )

    # Check for linked streams
    if not force:
        links_result = await session.exec(
            select(func.count(StreamMediaLink.id)).where(StreamMediaLink.media_id == media_id)
        )
        link_count = links_result.one()

        if link_count > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot delete: {link_count} stream(s) are linked to this metadata. Use force=true to delete anyway.",
            )

    # Delete the media (CASCADE will handle related records)
    await session.delete(media)
    await session.commit()

    logger.info(f"User {user.id} deleted metadata: {media.title} (id={media_id})")


# ============================================
# Season/Episode Management Endpoints
# ============================================


@router.post("/{media_id}/seasons", response_model=SeasonResponse)
async def add_season_to_series(
    media_id: int,
    request: SeasonAddRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Add a season to a series.
    """
    media = await get_media_by_id(session, media_id)

    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    if media.type != MediaType.SERIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only add seasons to series",
        )

    # Check ownership
    if media.created_by_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only modify your own metadata",
        )

    # Get series metadata
    series_result = await session.exec(select(SeriesMetadata).where(SeriesMetadata.media_id == media_id))
    series_meta = series_result.first()

    if not series_meta:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Series metadata not found",
        )

    # Check if season already exists
    existing_result = await session.exec(
        select(Season).where(
            Season.series_id == series_meta.id,
            Season.season_number == request.season_number,
        )
    )
    if existing_result.first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Season {request.season_number} already exists",
        )

    # Create season
    season = Season(
        series_id=series_meta.id,
        season_number=request.season_number,
        name=request.name,
        overview=request.overview,
        air_date=request.air_date,
        episode_count=len(request.episodes),
    )
    session.add(season)
    await session.flush()

    # Create episodes
    episodes = []
    for ep_data in request.episodes:
        episode = Episode(
            season_id=season.id,
            episode_number=ep_data.episode_number,
            title=ep_data.title,
            overview=ep_data.overview,
            air_date=ep_data.air_date,
            runtime_minutes=ep_data.runtime_minutes,
            is_user_created=True,
            created_by_user_id=user.id,
        )
        session.add(episode)
        episodes.append(episode)

    # Update series totals
    series_meta.total_seasons = (series_meta.total_seasons or 0) + 1
    series_meta.total_episodes = (series_meta.total_episodes or 0) + len(request.episodes)

    await session.commit()
    await session.refresh(season)

    logger.info(f"User {user.id} added season {request.season_number} to series {media_id}")

    return SeasonResponse(
        id=season.id,
        season_number=season.season_number,
        name=season.name,
        overview=season.overview,
        air_date=season.air_date,
        episode_count=season.episode_count,
        episodes=[
            EpisodeResponse(
                id=ep.id,
                episode_number=ep.episode_number,
                title=ep.title,
                overview=ep.overview,
                air_date=ep.air_date,
                runtime_minutes=ep.runtime_minutes,
                is_user_created=ep.is_user_created,
                is_user_addition=ep.is_user_addition,
            )
            for ep in sorted(episodes, key=lambda e: e.episode_number)
        ],
    )


@router.post("/{media_id}/episodes", response_model=list[EpisodeResponse])
async def add_episodes_to_series(
    media_id: int,
    request: EpisodeAddRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Add episodes to an existing season.
    """
    media = await get_media_by_id(session, media_id)

    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    if media.type != MediaType.SERIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only add episodes to series",
        )

    # Check ownership
    if media.created_by_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only modify your own metadata",
        )

    # Get series metadata
    series_result = await session.exec(select(SeriesMetadata).where(SeriesMetadata.media_id == media_id))
    series_meta = series_result.first()

    if not series_meta:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Series metadata not found",
        )

    # Find the season
    season_result = await session.exec(
        select(Season).where(
            Season.series_id == series_meta.id,
            Season.season_number == request.season_number,
        )
    )
    season = season_result.first()

    if not season:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Season {request.season_number} not found",
        )

    # Check for duplicate episode numbers
    existing_result = await session.exec(select(Episode.episode_number).where(Episode.season_id == season.id))
    existing_numbers = set(existing_result.all())

    new_numbers = {ep.episode_number for ep in request.episodes}
    duplicates = existing_numbers & new_numbers

    if duplicates:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Episode(s) already exist: {sorted(duplicates)}",
        )

    # Create episodes
    created_episodes = []
    for ep_data in request.episodes:
        episode = Episode(
            season_id=season.id,
            episode_number=ep_data.episode_number,
            title=ep_data.title,
            overview=ep_data.overview,
            air_date=ep_data.air_date,
            runtime_minutes=ep_data.runtime_minutes,
            is_user_created=True,
            created_by_user_id=user.id,
        )
        session.add(episode)
        created_episodes.append(episode)

    # Update counts
    season.episode_count += len(request.episodes)
    series_meta.total_episodes = (series_meta.total_episodes or 0) + len(request.episodes)

    await session.commit()

    logger.info(
        f"User {user.id} added {len(request.episodes)} episodes to series {media_id} season {request.season_number}"
    )

    return [
        EpisodeResponse(
            id=ep.id,
            episode_number=ep.episode_number,
            title=ep.title,
            overview=ep.overview,
            air_date=ep.air_date,
            runtime_minutes=ep.runtime_minutes,
            is_user_created=ep.is_user_created,
            is_user_addition=ep.is_user_addition,
        )
        for ep in sorted(created_episodes, key=lambda e: e.episode_number)
    ]


@router.put("/{media_id}/episodes/{episode_id}", response_model=EpisodeResponse)
async def update_episode(
    media_id: int,
    episode_id: int,
    request: EpisodeUpdateRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Update an episode.
    """
    media = await get_media_by_id(session, media_id)

    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    # Check ownership
    if media.created_by_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only modify your own metadata",
        )

    # Get episode
    episode_result = await session.exec(select(Episode).where(Episode.id == episode_id))
    episode = episode_result.first()

    if not episode:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Episode not found",
        )

    # Update fields
    if request.title is not None:
        episode.title = request.title
    if request.overview is not None:
        episode.overview = request.overview
    if request.air_date is not None:
        episode.air_date = request.air_date
    if request.runtime_minutes is not None:
        episode.runtime_minutes = request.runtime_minutes

    episode.updated_at = datetime.now(pytz.UTC)

    await session.commit()
    await session.refresh(episode)

    logger.info(f"User {user.id} updated episode {episode_id}")

    return EpisodeResponse(
        id=episode.id,
        episode_number=episode.episode_number,
        title=episode.title,
        overview=episode.overview,
        air_date=episode.air_date,
        runtime_minutes=episode.runtime_minutes,
        is_user_created=episode.is_user_created,
        is_user_addition=episode.is_user_addition,
    )


@router.delete("/{media_id}/episodes/{episode_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_episode(
    media_id: int,
    episode_id: int,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete an episode.
    """
    media = await get_media_by_id(session, media_id)

    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    # Check ownership
    if media.created_by_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only modify your own metadata",
        )

    # Get episode with season info
    episode_result = await session.exec(select(Episode).where(Episode.id == episode_id))
    episode = episode_result.first()

    if not episode:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Episode not found",
        )

    # Get season and series for count updates
    season_result = await session.exec(select(Season).where(Season.id == episode.season_id))
    season = season_result.first()

    series_result = await session.exec(select(SeriesMetadata).where(SeriesMetadata.media_id == media_id))
    series_meta = series_result.first()

    # Delete episode images first (FK constraint)
    episode_images_result = await session.exec(select(EpisodeImage).where(EpisodeImage.episode_id == episode_id))
    for img in episode_images_result.all():
        await session.delete(img)

    # Delete episode
    await session.delete(episode)

    # Update counts
    if season:
        season.episode_count = max(0, season.episode_count - 1)
    if series_meta:
        series_meta.total_episodes = max(0, (series_meta.total_episodes or 1) - 1)

    await session.commit()

    logger.info(f"User {user.id} deleted episode {episode_id}")


@router.delete("/{media_id}/episodes/{episode_id}/admin", status_code=status.HTTP_204_NO_CONTENT)
async def delete_episode_admin(
    media_id: int,
    episode_id: int,
    delete_stream_links: bool = Query(
        False, description="Also delete file-media links for this episode (removes streams from this episode)"
    ),
    user: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete an episode (admin only).

    Allows admins to delete any episode regardless of ownership.
    Useful for cleaning up orphaned episodes created by auto-detection errors.

    Note: Streams linked to this episode via FileMediaLink are NOT automatically deleted.
    They will become "hidden" (not appearing on any episode page) but still exist in the database.
    Use delete_stream_links=true to also remove the file-media links.
    """
    media = await get_media_by_id(session, media_id)

    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    # Get episode with season info
    episode_result = await session.exec(select(Episode).where(Episode.id == episode_id))
    episode = episode_result.first()

    if not episode:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Episode not found",
        )

    # Get season info for the episode
    season_result = await session.exec(select(Season).where(Season.id == episode.season_id))
    season = season_result.first()

    series_result = await session.exec(select(SeriesMetadata).where(SeriesMetadata.media_id == media_id))
    series_meta = series_result.first()

    # Count linked streams for logging
    linked_streams_result = await session.exec(
        select(FileMediaLink).where(
            FileMediaLink.media_id == media_id,
            FileMediaLink.season_number == season.season_number if season else None,
            FileMediaLink.episode_number == episode.episode_number,
        )
    )
    linked_streams = linked_streams_result.all()
    linked_count = len(linked_streams)

    # Optionally delete file-media links
    deleted_links = 0
    if delete_stream_links and linked_streams:
        for link in linked_streams:
            await session.delete(link)
            deleted_links += 1

    # Delete episode images first (FK constraint)
    episode_images_result = await session.exec(select(EpisodeImage).where(EpisodeImage.episode_id == episode_id))
    for img in episode_images_result.all():
        await session.delete(img)

    # Delete episode
    await session.delete(episode)

    # Update counts
    if season:
        season.episode_count = max(0, season.episode_count - 1)
    if series_meta:
        series_meta.total_episodes = max(0, (series_meta.total_episodes or 1) - 1)

    await session.commit()

    if linked_count > 0:
        if delete_stream_links:
            logger.info(
                f"Moderator {user.id} ({user.username}) deleted episode {episode_id} "
                f"(S{season.season_number if season else '?'}E{episode.episode_number}) "
                f"from media {media_id} and removed {deleted_links} file-media links"
            )
        else:
            logger.warning(
                f"Moderator {user.id} ({user.username}) deleted episode {episode_id} "
                f"(S{season.season_number if season else '?'}E{episode.episode_number}) "
                f"from media {media_id}. Note: {linked_count} streams were linked to this episode "
                f"and are now hidden (use delete_stream_links=true to also remove links)"
            )
    else:
        logger.info(
            f"Moderator {user.id} ({user.username}) deleted episode {episode_id} "
            f"(S{season.season_number if season else '?'}E{episode.episode_number}) "
            f"from media {media_id}"
        )


@router.delete("/{media_id}/seasons/{season_number}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_season(
    media_id: int,
    season_number: int,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete a season and all its episodes.
    """
    media = await get_media_by_id(session, media_id)

    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    # Check ownership
    if media.created_by_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only modify your own metadata",
        )

    # Get series metadata
    series_result = await session.exec(select(SeriesMetadata).where(SeriesMetadata.media_id == media_id))
    series_meta = series_result.first()

    if not series_meta:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Series metadata not found",
        )

    # Find the season
    season_result = await session.exec(
        select(Season).where(
            Season.series_id == series_meta.id,
            Season.season_number == season_number,
        )
    )
    season = season_result.first()

    if not season:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Season {season_number} not found",
        )

    episode_count = season.episode_count

    # Delete season (CASCADE will handle episodes)
    await session.delete(season)

    # Update series totals
    series_meta.total_seasons = max(0, (series_meta.total_seasons or 1) - 1)
    series_meta.total_episodes = max(0, (series_meta.total_episodes or episode_count) - episode_count)

    await session.commit()

    logger.info(f"User {user.id} deleted season {season_number} from series {media_id}")


@router.delete("/{media_id}/seasons/{season_number}/admin", status_code=status.HTTP_204_NO_CONTENT)
async def delete_season_admin(
    media_id: int,
    season_number: int,
    user: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete a season and all its episodes (admin only).

    Allows admins to delete any season regardless of ownership.
    Useful for cleaning up orphaned seasons created by auto-detection errors.
    """
    media = await get_media_by_id(session, media_id)

    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    # Get series metadata
    series_result = await session.exec(select(SeriesMetadata).where(SeriesMetadata.media_id == media_id))
    series_meta = series_result.first()

    if not series_meta:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Series metadata not found",
        )

    # Find the season
    season_result = await session.exec(
        select(Season).where(
            Season.series_id == series_meta.id,
            Season.season_number == season_number,
        )
    )
    season = season_result.first()

    if not season:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Season {season_number} not found",
        )

    episode_count = season.episode_count

    # Delete season (CASCADE will handle episodes)
    await session.delete(season)

    # Update series totals
    series_meta.total_seasons = max(0, (series_meta.total_seasons or 1) - 1)
    series_meta.total_episodes = max(0, (series_meta.total_episodes or episode_count) - episode_count)

    await session.commit()

    logger.info(f"Moderator {user.id} ({user.username}) deleted season {season_number} from series {media_id}")


# ============================================
# Search Endpoint (for linking)
# ============================================


@router.get("/search/all")
async def search_all_metadata(
    query: str = Query(..., min_length=2),
    type: Literal["movie", "series", "tv", "all"] = "all",
    limit: int = Query(20, ge=1, le=50),
    include_official: bool = Query(True, description="Include official metadata in results"),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Search for metadata (both user-created and official) for linking purposes.
    Returns basic info suitable for autocomplete/selection.
    """
    # Build base query
    base_query = select(
        Media.id,
        Media.title,
        Media.year,
        Media.type,
        Media.is_user_created,
        Media.created_by_user_id,
    ).where(Media.title.ilike(f"%{query}%"))

    # Filter by type
    if type == "movie":
        base_query = base_query.where(Media.type == MediaType.MOVIE)
    elif type == "series":
        base_query = base_query.where(Media.type == MediaType.SERIES)
    elif type == "tv":
        base_query = base_query.where(Media.type == MediaType.TV)

    # Filter visibility
    if include_official:
        # Include official + user's own + public user content
        base_query = base_query.where(
            (Media.is_user_created == False) | (Media.created_by_user_id == user.id) | (Media.is_public == True)
        )
    else:
        # Only user's own content
        base_query = base_query.where(Media.created_by_user_id == user.id)

    base_query = base_query.order_by(Media.total_streams.desc()).limit(limit)

    result = await session.exec(base_query)
    rows = result.all()

    # Format results
    results = []
    for row in rows:
        # Get poster
        poster_result = await session.exec(
            select(MediaImage.url).where(
                MediaImage.media_id == row.id,
                MediaImage.image_type == "poster",
            )
        )
        poster = poster_result.first()

        # Get external ID
        ext_result = await session.exec(
            select(MediaExternalID.provider, MediaExternalID.external_id).where(MediaExternalID.media_id == row.id)
        )
        ext_ids = {r.provider: r.external_id for r in ext_result.all()}

        # Determine canonical external ID
        canonical_id = ext_ids.get("imdb") or ext_ids.get("tmdb") or ext_ids.get("mediafusion") or f"mf:{row.id}"

        results.append(
            {
                "id": row.id,
                "external_id": canonical_id,
                "title": row.title,
                "year": row.year,
                "type": row.type.value.lower(),
                "poster": poster,
                "is_user_created": row.is_user_created,
                "is_own": row.created_by_user_id == user.id,
            }
        )

    return {"results": results, "total": len(results)}


# ============================================
# Import from External Endpoints
# ============================================


@router.post("/import/preview", response_model=ImportPreviewResponse)
async def preview_import_from_external(
    request: ImportFromExternalRequest,
    user: User = Depends(require_auth),
):
    """
    Preview metadata from an external provider before importing.
    Returns the fetched metadata without creating any records.
    """
    from scrapers.scraper_tasks import meta_fetcher

    # Validate external ID format based on provider
    external_id = request.external_id.strip()
    if request.provider == "imdb":
        if not external_id.startswith("tt"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="IMDb ID must start with 'tt' (e.g., tt1234567).",
            )
    elif request.provider in ("tmdb", "tvdb", "mal", "kitsu"):
        # These should be numeric IDs
        if ":" in external_id:
            external_id = external_id.split(":")[-1]
        if not external_id.isdigit():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{request.provider.upper()} ID must be numeric.",
            )

    # Map media type for fetcher
    fetch_media_type = "movie" if request.media_type == "movie" else "series"

    try:
        data = await meta_fetcher.get_metadata_from_provider(request.provider, external_id, fetch_media_type)

        if not data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No data found on {request.provider.upper()} for ID {external_id}",
            )

        return ImportPreviewResponse(
            provider=request.provider,
            external_id=external_id,
            title=data.get("title", ""),
            year=data.get("year"),
            description=data.get("description"),
            poster=data.get("poster"),
            background=data.get("background"),
            genres=data.get("genres", []),
            runtime=data.get("runtime"),
            imdb_id=data.get("imdb_id"),
            tmdb_id=str(data.get("tmdb_id")) if data.get("tmdb_id") else None,
            tvdb_id=data.get("tvdb_id"),
            mal_id=data.get("mal_id"),
            kitsu_id=data.get("kitsu_id"),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching external metadata: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch metadata: {str(e)}",
        )


@router.post("/import", response_model=UserMediaResponse, status_code=status.HTTP_201_CREATED)
async def import_from_external(
    request: ImportFromExternalRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Import metadata from an external provider.

    If media with the same external ID already exists, returns the existing media
    instead of creating a duplicate. Otherwise, fetches metadata from the provider
    and creates a new entry.
    """
    from scrapers.scraper_tasks import meta_fetcher

    # Validate external ID format based on provider
    external_id = request.external_id.strip()
    if request.provider == "imdb":
        if not external_id.startswith("tt"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="IMDb ID must start with 'tt' (e.g., tt1234567).",
            )
    elif request.provider in ("tmdb", "tvdb", "mal", "kitsu"):
        if ":" in external_id:
            external_id = external_id.split(":")[-1]
        if not external_id.isdigit():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{request.provider.upper()} ID must be numeric.",
            )

    # Determine media type
    if request.media_type == "movie":
        media_type = MediaType.MOVIE
    elif request.media_type == "series":
        media_type = MediaType.SERIES
    else:
        media_type = MediaType.TV

    # Check if media with this external ID already exists
    # Build the lookup ID format based on provider
    if request.provider == "imdb":
        lookup_id = external_id  # tt1234567 format
    else:
        lookup_id = f"{request.provider}:{external_id}"  # provider:id format

    existing_media = await get_media_by_external_id(session, lookup_id, media_type)
    if existing_media:
        logger.info(
            f"User {user.id} imported existing {media_type.value} from {request.provider}: "
            f"{existing_media.title} (id={existing_media.id})"
        )
        return await format_media_response(session, existing_media, include_seasons=True)

    # Map media type for fetcher
    fetch_media_type = "movie" if request.media_type == "movie" else "series"

    try:
        data = await meta_fetcher.get_metadata_from_provider(request.provider, external_id, fetch_media_type)

        if not data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No data found on {request.provider.upper()} for ID {external_id}",
            )

        # Also check by IMDb ID from fetched data (in case the user used a TMDB ID
        # but an IMDb-based media already exists)
        if data.get("imdb_id") and request.provider != "imdb":
            existing_by_imdb = await get_media_by_external_id(session, data["imdb_id"], media_type)
            if existing_by_imdb:
                # Add the provider's external ID to the existing media if missing
                await add_external_id(session, existing_by_imdb.id, request.provider, external_id)
                await session.commit()
                logger.info(
                    f"User {user.id} imported existing {media_type.value} (found via IMDb) from {request.provider}: "
                    f"{existing_by_imdb.title} (id={existing_by_imdb.id})"
                )
                return await format_media_response(session, existing_by_imdb, include_seasons=True)

        # Generate user external ID
        title = data.get("title", f"Import from {request.provider}")
        mf_external_id = generate_user_external_id(title, user.id)

        # Parse runtime
        runtime_minutes = None
        if data.get("runtime"):
            runtime_str = data["runtime"]
            try:
                if "min" in runtime_str.lower():
                    runtime_minutes = int(runtime_str.lower().replace("min", "").strip())
                elif runtime_str.isdigit():
                    runtime_minutes = int(runtime_str)
            except (ValueError, TypeError):
                pass

        # Create base media record
        media = Media(
            type=media_type,
            title=title,
            year=data.get("year"),
            description=data.get("description"),
            runtime_minutes=runtime_minutes,
            is_user_created=True,
            created_by_user_id=user.id,
            is_public=request.is_public,
        )
        session.add(media)
        await session.flush()

        # Add mediafusion external ID
        await add_external_id(session, media.id, "mediafusion", mf_external_id)

        # Add external IDs from the fetched data
        if data.get("imdb_id"):
            await add_external_id(session, media.id, "imdb", data["imdb_id"])
        if data.get("tmdb_id"):
            await add_external_id(session, media.id, "tmdb", str(data["tmdb_id"]))
        if data.get("tvdb_id"):
            await add_external_id(session, media.id, "tvdb", str(data["tvdb_id"]))
        if data.get("mal_id"):
            await add_external_id(session, media.id, "mal", str(data["mal_id"]))
        if data.get("kitsu_id"):
            await add_external_id(session, media.id, "kitsu", str(data["kitsu_id"]))

        # Add genres
        if data.get("genres"):
            for genre_name in data["genres"]:
                genre = await get_or_create_genre(session, genre_name)
                link = MediaGenreLink(media_id=media.id, genre_id=genre.id)
                session.add(link)

        # Add images
        if data.get("poster") or data.get("background"):
            metadata_provider = await get_or_create_provider(session, request.provider)

            if data.get("poster"):
                poster = MediaImage(
                    media_id=media.id,
                    provider_id=metadata_provider.id,
                    image_type="poster",
                    url=data["poster"],
                    is_primary=True,
                )
                session.add(poster)

            if data.get("background"):
                background = MediaImage(
                    media_id=media.id,
                    provider_id=metadata_provider.id,
                    image_type="backdrop",
                    url=data["background"],
                )
                session.add(background)

        # Create type-specific metadata
        if media_type == MediaType.MOVIE:
            movie_meta = MovieMetadata(media_id=media.id)
            session.add(movie_meta)
        elif media_type == MediaType.SERIES:
            series_meta = SeriesMetadata(
                media_id=media.id,
                total_seasons=0,
                total_episodes=0,
            )
            session.add(series_meta)
        # TV type doesn't need additional metadata tables

        await session.commit()
        await session.refresh(media)

        logger.info(
            f"User {user.id} created new {media_type.value} metadata from {request.provider}: {title} (id={media.id})"
        )

        return await format_media_response(session, media, include_seasons=True)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error importing external metadata: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to import metadata: {str(e)}",
        )
