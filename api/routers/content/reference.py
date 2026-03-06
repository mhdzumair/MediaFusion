"""Reference data endpoints for metadata editing flows."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from db.database import get_async_session
from db.models import (
    Catalog,
    Genre,
    MediaCast,
    MediaCatalogLink,
    MediaGenreLink,
    MediaParentalCertificateLink,
    ParentalCertificate,
    Person,
    User,
)

router = APIRouter(prefix="/api/v1/metadata/reference", tags=["Metadata Reference"])


class ReferenceItem(BaseModel):
    id: int
    name: str
    usage_count: int = 0


class ReferenceListResponse(BaseModel):
    items: list[ReferenceItem]
    total: int
    page: int = 1
    per_page: int = 50
    pages: int = 1
    has_more: bool = False


@router.get("/genres", response_model=ReferenceListResponse)
async def list_genres(
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    _user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """List genres with usage count and pagination."""
    count_query = select(func.count(Genre.id))
    if search:
        count_query = count_query.where(Genre.name.ilike(f"%{search}%"))
    total_result = await session.exec(count_query)
    total = total_result.one()

    query = (
        select(
            Genre.id,
            Genre.name,
            func.count(MediaGenreLink.media_id).label("usage_count"),
        )
        .outerjoin(MediaGenreLink, Genre.id == MediaGenreLink.genre_id)
        .group_by(Genre.id, Genre.name)
    )

    if search:
        query = query.where(Genre.name.ilike(f"%{search}%"))

    offset = (page - 1) * per_page
    query = query.order_by(Genre.name).offset(offset).limit(per_page)

    result = await session.exec(query)
    rows = result.all()

    items = [
        ReferenceItem(id=row.id, name=row.name, usage_count=row.usage_count)
        for row in rows
        if row.name.lower() not in ["adult", "18+"]
    ]

    pages = (total + per_page - 1) // per_page if total > 0 else 1
    return ReferenceListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_more=page < pages,
    )


@router.get("/catalogs", response_model=ReferenceListResponse)
async def list_catalogs(
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    _user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """List catalogs with usage count and pagination."""
    count_query = select(func.count(Catalog.id))
    if search:
        count_query = count_query.where(Catalog.name.ilike(f"%{search}%"))
    total_result = await session.exec(count_query)
    total = total_result.one()

    query = (
        select(
            Catalog.id,
            Catalog.name,
            func.count(MediaCatalogLink.media_id).label("usage_count"),
        )
        .outerjoin(MediaCatalogLink, Catalog.id == MediaCatalogLink.catalog_id)
        .group_by(Catalog.id, Catalog.name)
    )

    if search:
        query = query.where(Catalog.name.ilike(f"%{search}%"))

    offset = (page - 1) * per_page
    query = query.order_by(Catalog.name).offset(offset).limit(per_page)

    result = await session.exec(query)
    rows = result.all()

    items = [ReferenceItem(id=row.id, name=row.name, usage_count=row.usage_count) for row in rows]
    pages = (total + per_page - 1) // per_page if total > 0 else 1
    return ReferenceListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_more=page < pages,
    )


@router.get("/stars", response_model=ReferenceListResponse)
async def list_stars(
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    _user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """List people (cast/crew) with usage count and pagination."""
    count_query = select(func.count(Person.id))
    if search:
        count_query = count_query.where(Person.name.ilike(f"%{search}%"))
    total_result = await session.exec(count_query)
    total = total_result.one()

    query = (
        select(Person.id, Person.name, func.count(MediaCast.media_id).label("usage_count"))
        .outerjoin(MediaCast, Person.id == MediaCast.person_id)
        .group_by(Person.id, Person.name)
    )

    if search:
        query = query.where(Person.name.ilike(f"%{search}%"))

    offset = (page - 1) * per_page
    query = query.order_by(Person.name).offset(offset).limit(per_page)

    result = await session.exec(query)
    rows = result.all()

    items = [ReferenceItem(id=row.id, name=row.name, usage_count=row.usage_count) for row in rows]
    pages = (total + per_page - 1) // per_page if total > 0 else 1
    return ReferenceListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_more=page < pages,
    )


@router.get("/parental-certificates", response_model=ReferenceListResponse)
async def list_parental_certificates(
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    _user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """List parental certificates with usage count and pagination."""
    count_query = select(func.count(ParentalCertificate.id))
    if search:
        count_query = count_query.where(ParentalCertificate.name.ilike(f"%{search}%"))
    total_result = await session.exec(count_query)
    total = total_result.one()

    query = (
        select(
            ParentalCertificate.id,
            ParentalCertificate.name,
            func.count(MediaParentalCertificateLink.media_id).label("usage_count"),
        )
        .outerjoin(
            MediaParentalCertificateLink,
            ParentalCertificate.id == MediaParentalCertificateLink.certificate_id,
        )
        .group_by(ParentalCertificate.id, ParentalCertificate.name)
    )

    if search:
        query = query.where(ParentalCertificate.name.ilike(f"%{search}%"))

    offset = (page - 1) * per_page
    query = query.order_by(ParentalCertificate.name).offset(offset).limit(per_page)

    result = await session.exec(query)
    rows = result.all()

    items = [ReferenceItem(id=row.id, name=row.name, usage_count=row.usage_count) for row in rows]
    pages = (total + per_page - 1) // per_page if total > 0 else 1
    return ReferenceListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_more=page < pages,
    )
