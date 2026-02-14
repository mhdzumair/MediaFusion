"""
Stream Linking API endpoints for managing stream-to-media relationships.
Supports linking single streams to multiple media entries (e.g., multi-movie torrents).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth, require_role
from db.crud import (
    get_canonical_external_id,
    get_media_by_id,
    get_stream_by_id,
    link_stream_to_media,
    unlink_stream_from_media,
)
from db.database import get_async_session
from db.enums import UserRole
from db.models import Stream, StreamMediaLink, User

router = APIRouter(prefix="/api/v1/stream-links", tags=["Stream Linking"])


# ============================================
# Pydantic Schemas
# ============================================


class StreamLinkCreate(BaseModel):
    """Request to link a stream to a media entry"""

    stream_id: int
    media_id: int
    file_index: int | None = Field(None, description="File index within torrent for multi-file torrents")
    season: int | None = Field(None, description="Season number for series")
    episode: int | None = Field(None, description="Episode number for series")


class StreamLinkResponse(BaseModel):
    """Response for a stream-media link"""

    id: int
    stream_id: int
    media_id: int
    file_index: int | None
    season: int | None
    episode: int | None
    linked_at: datetime

    class Config:
        from_attributes = True


class StreamLinksResponse(BaseModel):
    """Response for multiple stream links"""

    links: list[StreamLinkResponse]
    total: int


class BulkLinkCreate(BaseModel):
    """Request to create multiple stream-media links at once"""

    links: list[StreamLinkCreate] = Field(..., min_length=1, max_length=50)


class BulkLinkResponse(BaseModel):
    """Response for bulk link creation"""

    created: int
    failed: int
    errors: list[str]


class MediaLinksForStream(BaseModel):
    """All media linked to a stream"""

    stream_id: int
    media_entries: list[dict]  # Basic media info


class StreamsForMedia(BaseModel):
    """All streams linked to a media entry"""

    media_id: int
    streams: list[dict]  # Basic stream info


# ============================================
# Stream Linking Endpoints
# ============================================


@router.post("", response_model=StreamLinkResponse, status_code=status.HTTP_201_CREATED)
async def create_stream_link(
    request: StreamLinkCreate,
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Link a stream to a media entry.
    Useful for:
    - Multi-movie torrents: Link one torrent to multiple movies
    - Specific file mapping: Link a specific file index to a specific movie
    - Series episodes: Link with season/episode info

    Requires CONTRIBUTOR or ADMIN role.
    """
    # Verify stream exists
    stream = await get_stream_by_id(session, request.stream_id)
    if not stream:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stream not found",
        )

    # Verify media exists
    media = await get_media_by_id(session, request.media_id)
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found",
        )

    # Check if link already exists
    existing = await session.exec(
        select(StreamMediaLink).where(
            StreamMediaLink.stream_id == request.stream_id,
            StreamMediaLink.media_id == request.media_id,
            StreamMediaLink.file_index == request.file_index,
        )
    )
    if existing.first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This link already exists",
        )

    # Create the link
    link = await link_stream_to_media(
        session,
        stream_id=request.stream_id,
        media_id=request.media_id,
        file_index=request.file_index,
        season=request.season,
        episode=request.episode,
    )
    await session.commit()

    return StreamLinkResponse(
        id=link.id,
        stream_id=link.stream_id,
        media_id=link.media_id,
        file_index=link.file_index,
        season=link.season,
        episode=link.episode,
        linked_at=link.linked_at,
    )


@router.post("/bulk", response_model=BulkLinkResponse)
async def create_bulk_stream_links(
    request: BulkLinkCreate,
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Create multiple stream-media links at once.
    Useful for linking a multi-movie torrent to all its constituent movies.

    Requires CONTRIBUTOR or ADMIN role.
    """
    created = 0
    failed = 0
    errors = []

    for link_req in request.links:
        try:
            # Verify stream exists
            stream = await get_stream_by_id(session, link_req.stream_id)
            if not stream:
                errors.append(f"Stream {link_req.stream_id} not found")
                failed += 1
                continue

            # Verify media exists
            media = await get_media_by_id(session, link_req.media_id)
            if not media:
                errors.append(f"Media {link_req.media_id} not found")
                failed += 1
                continue

            # Check if link already exists
            existing = await session.exec(
                select(StreamMediaLink).where(
                    StreamMediaLink.stream_id == link_req.stream_id,
                    StreamMediaLink.media_id == link_req.media_id,
                    StreamMediaLink.file_index == link_req.file_index,
                )
            )
            if existing.first():
                errors.append(f"Link already exists: stream {link_req.stream_id} -> media {link_req.media_id}")
                failed += 1
                continue

            # Create link
            await link_stream_to_media(
                session,
                stream_id=link_req.stream_id,
                media_id=link_req.media_id,
                file_index=link_req.file_index,
                season=link_req.season,
                episode=link_req.episode,
            )
            created += 1

        except Exception as e:
            errors.append(f"Error linking stream {link_req.stream_id} -> media {link_req.media_id}: {str(e)}")
            failed += 1

    await session.commit()

    return BulkLinkResponse(
        created=created,
        failed=failed,
        errors=errors[:10],  # Limit errors in response
    )


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stream_link(
    link_id: int,
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Remove a stream-media link.

    Requires CONTRIBUTOR or ADMIN role.
    """
    # Find the link
    result = await session.exec(select(StreamMediaLink).where(StreamMediaLink.id == link_id))
    link = result.first()

    if not link:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Link not found",
        )

    # Remove the link
    success = await unlink_stream_from_media(session, link.stream_id, link.media_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove link",
        )

    await session.commit()


@router.get("/stream/{stream_id}", response_model=MediaLinksForStream)
async def get_media_for_stream(
    stream_id: int,
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get all media entries linked to a stream.
    Useful for multi-movie torrents to see all linked movies.
    """
    # Get all links for this stream
    result = await session.exec(select(StreamMediaLink).where(StreamMediaLink.stream_id == stream_id))
    links = result.all()

    media_entries = []
    for link in links:
        media = await get_media_by_id(session, link.media_id)
        if media:
            canonical_ext_id = await get_canonical_external_id(session, media.id)
            media_entries.append(
                {
                    "link_id": link.id,
                    "media_id": media.id,
                    "external_id": canonical_ext_id,
                    "title": media.title,
                    "year": media.year,
                    "type": media.type.value,
                    "file_index": link.file_index,
                    # Note: season/episode are at file level via FileMediaLink, not stream level
                }
            )

    return MediaLinksForStream(
        stream_id=stream_id,
        media_entries=media_entries,
    )


@router.get("/media/{media_id}", response_model=StreamsForMedia)
async def get_streams_for_media(
    media_id: int,
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get all streams linked to a media entry.
    """
    # Get all links for this media
    result = await session.exec(select(StreamMediaLink).where(StreamMediaLink.media_id == media_id))
    links = result.all()

    streams = []
    for link in links:
        stream = await get_stream_by_id(session, link.stream_id)
        if stream:
            # Get size from TorrentStream if available
            size = None
            if stream.torrent_stream:
                size = stream.torrent_stream.total_size
            streams.append(
                {
                    "link_id": link.id,
                    "stream_id": stream.id,
                    "name": stream.name,
                    "type": stream.stream_type.value,
                    "size": size,
                    "resolution": stream.resolution,
                    "file_index": link.file_index,
                    "season": link.season,
                    "episode": link.episode,
                }
            )

    return StreamsForMedia(
        media_id=media_id,
        streams=streams,
    )


@router.get("/search")
async def search_unlinked_streams(
    query: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
):
    """
    Search for streams that might need linking.
    Returns streams matching the query with their current links.

    Requires CONTRIBUTOR or ADMIN role.
    """

    # Search streams by name
    result = await session.exec(select(Stream).where(Stream.name.ilike(f"%{query}%")).limit(limit))
    streams = result.all()

    results = []
    for stream in streams:
        # Get current links
        links_result = await session.exec(select(StreamMediaLink).where(StreamMediaLink.stream_id == stream.id))
        links = links_result.all()

        # Get size from TorrentStream if available
        size = None
        if stream.torrent_stream:
            size = stream.torrent_stream.total_size

        results.append(
            {
                "stream_id": stream.id,
                "name": stream.name,
                "type": stream.stream_type.value,
                "size": size,
                "link_count": len(links),
                "links": [
                    {
                        "media_id": link.media_id,
                        "file_index": link.file_index,
                    }
                    for link in links
                ],
            }
        )

    return {"results": results, "total": len(results)}


# ============================================
# File Link Update Endpoints (for series episode corrections)
# ============================================


class FileLinkUpdate(BaseModel):
    """Request to update a file-media link"""

    file_id: int
    season_number: int | None = None
    episode_number: int | None = None
    episode_end: int | None = None


class BulkFileLinkUpdate(BaseModel):
    """Request to update multiple file-media links at once"""

    stream_id: int
    media_id: int
    updates: list[FileLinkUpdate] = Field(..., min_length=1, max_length=100)


class FileLinkUpdateResponse(BaseModel):
    """Response for file link update"""

    updated: int
    failed: int
    errors: list[str]


@router.put("/files", response_model=FileLinkUpdateResponse)
async def update_file_links(
    request: BulkFileLinkUpdate,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Update file-to-media links for a stream.

    Used to correct season/episode numbers for series files.
    All logged-in users can update file links.
    """
    from db.models import FileMediaLink, StreamFile, TorrentStream

    updated = 0
    failed = 0
    errors = []

    # Verify stream exists and belongs to a torrent
    stream_query = select(Stream).where(Stream.id == request.stream_id)
    stream_result = await session.exec(stream_query)
    stream = stream_result.first()

    if not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found")

    # Get torrent stream to access files
    torrent_query = select(TorrentStream).where(TorrentStream.stream_id == request.stream_id)
    torrent_result = await session.exec(torrent_query)
    torrent = torrent_result.first()

    if not torrent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Torrent stream not found")

    for update in request.updates:
        try:
            # Verify file belongs to this stream's torrent
            file_query = select(StreamFile).where(
                StreamFile.id == update.file_id,
                StreamFile.stream_id == torrent.id,
            )
            file_result = await session.exec(file_query)
            file = file_result.first()

            if not file:
                errors.append(f"File {update.file_id} not found in this stream")
                failed += 1
                continue

            # Find existing link to update
            link_query = select(FileMediaLink).where(
                FileMediaLink.file_id == update.file_id,
                FileMediaLink.media_id == request.media_id,
            )
            link_result = await session.exec(link_query)
            link = link_result.first()

            if link:
                # Update existing link
                link.season_number = update.season_number
                link.episode_number = update.episode_number
                link.episode_end = update.episode_end
                session.add(link)
            else:
                # Create new link if it doesn't exist
                new_link = FileMediaLink(
                    file_id=update.file_id,
                    media_id=request.media_id,
                    season_number=update.season_number,
                    episode_number=update.episode_number,
                    episode_end=update.episode_end,
                )
                session.add(new_link)

            updated += 1

        except Exception as e:
            errors.append(f"Failed to update file {update.file_id}: {str(e)}")
            failed += 1

    await session.commit()

    return FileLinkUpdateResponse(
        updated=updated,
        failed=failed,
        errors=errors,
    )


@router.get("/files/{stream_id}")
async def get_stream_file_links(
    stream_id: int,
    media_id: int = Query(..., description="Media ID to filter file links"),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_auth),
):
    """
    Get all file links for a stream and media combination.
    Returns detailed file information with season/episode mappings.
    """
    from sqlalchemy.orm import selectinload

    from db.models import StreamFile, TorrentStream

    # Get torrent stream with files
    torrent_query = (
        select(TorrentStream)
        .where(TorrentStream.stream_id == stream_id)
        .options(selectinload(TorrentStream.files).selectinload(StreamFile.media_links))
    )
    torrent_result = await session.exec(torrent_query)
    torrent = torrent_result.first()

    if not torrent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Torrent stream not found")

    files = []
    for file in torrent.files:
        # Find link for this media
        link = next((ml for ml in file.media_links if ml.media_id == media_id), None)

        files.append(
            {
                "file_id": file.id,
                "file_name": file.filename or f"File {file.file_index or file.id}",
                "file_index": file.file_index,
                "size": file.size,
                "season_number": link.season_number if link else None,
                "episode_number": link.episode_number if link else None,
                "episode_end": link.episode_end if link else None,
            }
        )

    # Sort by filename
    files.sort(key=lambda f: f["file_name"])

    return {
        "stream_id": stream_id,
        "media_id": media_id,
        "files": files,
        "total": len(files),
    }


# ============================================
# Stream Files Endpoint (for annotation)
# ============================================


class StreamFileResponse(BaseModel):
    """Response for a stream file"""

    file_id: int
    file_name: str
    size: int | None = None  # File size in bytes
    season_number: int | None = None
    episode_number: int | None = None
    episode_end: int | None = None


@router.get("/stream/{stream_id}/files", response_model=list[StreamFileResponse])
async def get_stream_files_for_annotation(
    stream_id: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_auth),
):
    """
    Get all files for a stream for annotation purposes.
    Returns file information with any existing season/episode mappings.

    This endpoint is used by the file annotation dialog to load files
    without requiring the media_id upfront.
    """
    from sqlalchemy.orm import selectinload

    from db.models import StreamFile

    # Try to parse stream_id as integer
    try:
        sid = int(stream_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid stream ID")

    # Get stream with files and their media links
    # Note: files is on Stream model, not TorrentStream
    stream_query = (
        select(Stream).where(Stream.id == sid).options(selectinload(Stream.files).selectinload(StreamFile.media_links))
    )
    stream_result = await session.exec(stream_query)
    stream = stream_result.first()

    if not stream:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found")

    if not stream.files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No files found for this stream",
        )

    files = []
    for file in stream.files:
        # Get the first media link for this file (if any)
        # This gives us the current season/episode assignment
        link = file.media_links[0] if file.media_links else None

        files.append(
            StreamFileResponse(
                file_id=file.id,
                file_name=file.filename or f"File {file.file_index or file.id}",
                size=file.size,
                season_number=link.season_number if link else None,
                episode_number=link.episode_number if link else None,
                episode_end=link.episode_end if link else None,
            )
        )

    # Sort by filename
    files.sort(key=lambda f: f.file_name)

    return files


# ============================================
# Annotation Requests Endpoints (for moderators)
# ============================================


class StreamNeedingAnnotation(BaseModel):
    """Stream that needs file annotation"""

    stream_id: int
    stream_name: str
    source: str | None = None
    size: int | None = None
    resolution: str | None = None
    info_hash: str | None = None
    file_count: int
    unmapped_count: int  # Files without episode mapping
    created_at: datetime
    # Associated media info
    media_id: int
    media_title: str
    media_year: int | None = None

    class Config:
        from_attributes = True


class StreamsNeedingAnnotationResponse(BaseModel):
    """Response for streams needing annotation list"""

    items: list[StreamNeedingAnnotation]
    total: int
    page: int
    per_page: int
    pages: int


@router.get("/needs-annotation", response_model=StreamsNeedingAnnotationResponse)
async def get_streams_needing_annotation(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Search by stream name or media title"),
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get list of series streams that need file annotation.

    Returns streams that have:
    - Files (StreamFile entries)
    - But files lack FileMediaLink entries OR have null episode_number

    Requires MODERATOR or ADMIN role.
    """
    from sqlalchemy import text

    # Use raw SQL for efficiency - this is a complex aggregation query
    # that performs much better as raw SQL with proper window functions

    # Build the search condition
    search_condition = ""
    params = {
        "limit": per_page,
        "offset": (page - 1) * per_page,
    }

    if search:
        search_condition = """
            AND (
                s.name ILIKE :search
                OR m.title ILIKE :search
                OR ts.info_hash ILIKE :search
            )
        """
        params["search"] = f"%{search}%"

    # Efficient query using CTEs
    # First CTE: Get series streams with basic info
    # Second CTE: Aggregate file counts
    # This approach is much faster than multiple subqueries

    count_sql = text(f"""
        WITH series_streams AS (
            SELECT DISTINCT
                s.id as stream_id,
                s.name as stream_name,
                s.source,
                ts.total_size as size,
                s.resolution,
                s.created_at,
                ts.info_hash,
                m.id as media_id,
                m.title as media_title,
                m.year as media_year
            FROM stream s
            INNER JOIN torrent_stream ts ON ts.stream_id = s.id
            INNER JOIN stream_media_link sml ON sml.stream_id = s.id
            INNER JOIN media m ON sml.media_id = m.id
            WHERE m.type = 'SERIES'
              AND s.is_active = true
              AND s.is_blocked = false
              AND EXISTS (
                  SELECT 1 FROM stream_file sf
                  LEFT JOIN file_media_link fml ON fml.file_id = sf.id
                  WHERE sf.stream_id = s.id
                    AND (fml.id IS NULL OR fml.episode_number IS NULL)
              )
              {search_condition}
        )
        SELECT COUNT(*) FROM series_streams
    """)

    count_result = await session.exec(count_sql, params=params)
    total = count_result.one()[0]

    if total == 0:
        return StreamsNeedingAnnotationResponse(
            items=[],
            total=0,
            page=page,
            per_page=per_page,
            pages=1,
        )

    # Main query with file counts
    data_sql = text(f"""
        WITH series_streams AS (
            SELECT DISTINCT
                s.id as stream_id,
                s.name as stream_name,
                s.source,
                ts.total_size as size,
                s.resolution,
                s.created_at,
                ts.info_hash,
                m.id as media_id,
                m.title as media_title,
                m.year as media_year
            FROM stream s
            INNER JOIN torrent_stream ts ON ts.stream_id = s.id
            INNER JOIN stream_media_link sml ON sml.stream_id = s.id
            INNER JOIN media m ON sml.media_id = m.id
            WHERE m.type = 'SERIES'
              AND s.is_active = true
              AND s.is_blocked = false
              AND EXISTS (
                  SELECT 1 FROM stream_file sf
                  LEFT JOIN file_media_link fml ON fml.file_id = sf.id
                  WHERE sf.stream_id = s.id
                    AND (fml.id IS NULL OR fml.episode_number IS NULL)
              )
              {search_condition}
        ),
        file_counts AS (
            SELECT
                ss.stream_id,
                COUNT(sf.id) as file_count,
                COUNT(sf.id) FILTER (
                    WHERE fml.id IS NULL OR fml.episode_number IS NULL
                ) as unmapped_count
            FROM series_streams ss
            INNER JOIN stream_file sf ON sf.stream_id = ss.stream_id
            LEFT JOIN file_media_link fml ON fml.file_id = sf.id
            GROUP BY ss.stream_id
        )
        SELECT
            ss.*,
            COALESCE(fc.file_count, 0) as file_count,
            COALESCE(fc.unmapped_count, 0) as unmapped_count
        FROM series_streams ss
        LEFT JOIN file_counts fc ON fc.stream_id = ss.stream_id
        ORDER BY ss.created_at DESC
        LIMIT :limit OFFSET :offset
    """)

    result = await session.exec(data_sql, params=params)
    rows = result.all()

    # Build response items
    items = []
    for row in rows:
        items.append(
            StreamNeedingAnnotation(
                stream_id=row.stream_id,
                stream_name=row.stream_name,
                source=row.source,
                size=row.size,
                resolution=row.resolution,
                info_hash=row.info_hash,
                file_count=row.file_count or 0,
                unmapped_count=row.unmapped_count or 0,
                created_at=row.created_at,
                media_id=row.media_id,
                media_title=row.media_title,
                media_year=row.media_year,
            )
        )

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    return StreamsNeedingAnnotationResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )
