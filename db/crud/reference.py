"""CRUD operations for reference/lookup tables (Genre, Catalog, Language, etc.)."""

import json

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.enums import MediaType
from db.models import (
    AudioChannel,
    AudioFormat,
    Catalog,
    Genre,
    HDRFormat,
    Language,
    Media,
    MediaGenreLink,
    ParentalCertificate,
)
from db.redis_database import REDIS_ASYNC_CLIENT
from utils.const import ADULT_GENRE_NAMES


async def get_or_create_genre(session: AsyncSession, name: str) -> Genre:
    """Get or create a genre by name."""
    query = select(Genre).where(Genre.name == name)
    result = await session.exec(query)
    genre = result.one_or_none()

    if not genre:
        genre = Genre(name=name)
        session.add(genre)
        await session.flush()

    return genre


async def get_or_create_catalog(session: AsyncSession, name: str) -> Catalog:
    """Get or create a catalog by name with caching."""
    cache_key = f"catalog:{name}"
    cached_id = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_id:
        query = select(Catalog).where(Catalog.id == int(cached_id))
        result = await session.exec(query)
        catalog = result.one_or_none()
        if catalog:
            return catalog

    query = select(Catalog).where(Catalog.name == name)
    result = await session.exec(query)
    catalog = result.one_or_none()

    if not catalog:
        catalog = Catalog(name=name)
        session.add(catalog)
        await session.flush()

    # Cache the ID
    await REDIS_ASYNC_CLIENT.set(cache_key, str(catalog.id), ex=86400)  # 24h

    return catalog


async def get_or_create_parental_certificate(session: AsyncSession, name: str) -> ParentalCertificate:
    """Get or create a parental certificate by name."""
    query = select(ParentalCertificate).where(ParentalCertificate.name == name)
    result = await session.exec(query)
    cert = result.one_or_none()

    if not cert:
        cert = ParentalCertificate(name=name)
        session.add(cert)
        await session.flush()

    return cert


async def get_or_create_language(session: AsyncSession, name: str) -> Language:
    """Get or create a language by name with caching."""
    cache_key = f"lang:{name}"
    cached_id = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_id:
        query = select(Language).where(Language.id == int(cached_id))
        result = await session.exec(query)
        lang = result.one_or_none()
        if lang:
            return lang

    query = select(Language).where(Language.name == name)
    result = await session.exec(query)
    lang = result.one_or_none()

    if not lang:
        lang = Language(name=name)
        session.add(lang)
        await session.flush()

    # Cache the ID
    await REDIS_ASYNC_CLIENT.set(cache_key, str(lang.id), ex=86400)  # 24h

    return lang


async def get_or_create_audio_format(session: AsyncSession, name: str) -> AudioFormat:
    """Get or create an audio format by name with caching."""
    if not name:
        name = "Unknown"

    cache_key = f"audio_format:{name}"
    cached_id = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_id:
        query = select(AudioFormat).where(AudioFormat.id == int(cached_id))
        result = await session.exec(query)
        af = result.one_or_none()
        if af:
            return af

    query = select(AudioFormat).where(AudioFormat.name == name)
    result = await session.exec(query)
    af = result.one_or_none()

    if not af:
        af = AudioFormat(name=name)
        session.add(af)
        await session.flush()

    # Cache the ID
    await REDIS_ASYNC_CLIENT.set(cache_key, str(af.id), ex=86400)  # 24h

    return af


async def get_or_create_audio_channel(session: AsyncSession, name: str) -> AudioChannel:
    """Get or create an audio channel by name with caching."""
    if not name:
        name = "Unknown"

    cache_key = f"audio_channel:{name}"
    cached_id = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_id:
        query = select(AudioChannel).where(AudioChannel.id == int(cached_id))
        result = await session.exec(query)
        ch = result.one_or_none()
        if ch:
            return ch

    query = select(AudioChannel).where(AudioChannel.name == name)
    result = await session.exec(query)
    ch = result.one_or_none()

    if not ch:
        ch = AudioChannel(name=name)
        session.add(ch)
        await session.flush()

    # Cache the ID
    await REDIS_ASYNC_CLIENT.set(cache_key, str(ch.id), ex=86400)  # 24h

    return ch


async def get_or_create_hdr_format(session: AsyncSession, name: str) -> HDRFormat:
    """Get or create an HDR format by name with caching."""
    if not name:
        name = "Unknown"

    cache_key = f"hdr_format:{name}"
    cached_id = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_id:
        query = select(HDRFormat).where(HDRFormat.id == int(cached_id))
        result = await session.exec(query)
        hdr = result.one_or_none()
        if hdr:
            return hdr

    query = select(HDRFormat).where(HDRFormat.name == name)
    result = await session.exec(query)
    hdr = result.one_or_none()

    if not hdr:
        hdr = HDRFormat(name=name)
        session.add(hdr)
        await session.flush()

    # Cache the ID
    await REDIS_ASYNC_CLIENT.set(cache_key, str(hdr.id), ex=86400)  # 24h

    return hdr


async def get_genres(
    session: AsyncSession,
    media_type: MediaType,
) -> list[str]:
    """Get all distinct genre names for a specific media type.

    Args:
        session: Database session
        media_type: The media type to filter genres by

    Returns:
        List of genre names that have at least one media of the given type
    """

    # Cache key based on media type
    cache_key = f"genres:{media_type.value}"
    cached = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached:
        return json.loads(cached)

    # Query distinct genres that are linked to media of this type
    query = (
        select(Genre.name)
        .distinct()
        .join(MediaGenreLink, MediaGenreLink.genre_id == Genre.id)
        .join(Media, Media.id == MediaGenreLink.media_id)
        .where(Media.type == media_type)
        .order_by(Genre.name)
    )

    result = await session.exec(query)
    genres = list(result.all())

    # Cache for 1 hour
    await REDIS_ASYNC_CLIENT.set(cache_key, json.dumps(genres), ex=3600)

    return genres


async def get_all_genres_by_type(
    session: AsyncSession,
) -> dict[str, list[str]]:
    """Get all genres grouped by media type in a single query.

    More efficient than calling get_genres() multiple times.

    Returns:
        Dict mapping media type names to their genre lists:
        {"movie": [...], "series": [...], "tv": [...]}
    """

    # Check cache first
    cache_key = "genres:all_by_type"
    cached = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached:
        return json.loads(cached)

    # Single query to get all genres with their media types
    query = (
        select(Media.type, Genre.name)
        .distinct()
        .join(MediaGenreLink, MediaGenreLink.media_id == Media.id)
        .join(Genre, Genre.id == MediaGenreLink.genre_id)
        .order_by(Media.type, Genre.name)
    )

    result = await session.exec(query)
    rows = result.all()

    # Group by media type
    genres_by_type: dict[str, list[str]] = {
        "movie": [],
        "series": [],
        "tv": [],
    }

    for media_type, genre_name in rows:
        type_key = media_type.value  # MediaType enum to string
        if type_key in genres_by_type and genre_name.lower() not in ADULT_GENRE_NAMES:
            genres_by_type[type_key].append(genre_name)

    # Cache for 1 hour
    await REDIS_ASYNC_CLIENT.set(cache_key, json.dumps(genres_by_type), ex=3600)

    # Also cache individual types for get_genres() calls
    for type_key, genre_list in genres_by_type.items():
        await REDIS_ASYNC_CLIENT.set(f"genres:{type_key}", json.dumps(genre_list), ex=3600)

    return genres_by_type
