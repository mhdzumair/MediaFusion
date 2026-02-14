"""
User Management API endpoints for admin operations.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import UserResponse, require_role
from db.database import get_async_session
from db.enums import UserRole
from db.models import User

router = APIRouter(prefix="/api/v1/users", tags=["User Management"])


# ============================================
# Pydantic Schemas
# ============================================


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    page: int
    per_page: int
    pages: int


class UserUpdateRequest(BaseModel):
    username: str | None = None
    is_active: bool | None = None
    is_verified: bool | None = None


class RoleUpdateRequest(BaseModel):
    role: str


# ============================================
# API Endpoints
# ============================================


@router.get("", response_model=UserListResponse)
async def list_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    role: str | None = None,
    search: str | None = None,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """List all users (Admin only)."""
    query = select(User)

    # Apply filters
    if role:
        try:
            role_enum = UserRole(role)
            query = query.where(User.role == role_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role: {role}",
            )

    if search:
        search_pattern = f"%{search}%"
        query = query.where((User.email.ilike(search_pattern)) | (User.username.ilike(search_pattern)))

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page).order_by(User.created_at.desc())

    result = await session.exec(query)
    users = result.all()

    return UserListResponse(
        items=[
            UserResponse(
                id=u.id,
                uuid=u.uuid,
                email=u.email,
                username=u.username,
                role=u.role.value,
                is_verified=u.is_verified,
                is_active=u.is_active,
                created_at=u.created_at,
                last_login=u.last_login,
                contribution_points=u.contribution_points,
                contribution_level=u.contribution_level,
            )
            for u in users
        ],
        total=total,
        page=page,
        per_page=per_page,
        pages=(total + per_page - 1) // per_page if total > 0 else 1,
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get a specific user by ID (Admin only)."""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserResponse(
        id=user.id,
        uuid=user.uuid,
        email=user.email,
        username=user.username,
        role=user.role.value,
        is_verified=user.is_verified,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        contribution_points=user.contribution_points,
        contribution_level=user.contribution_level,
    )


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    update_data: UserUpdateRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Update a user's information (Admin only)."""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Update fields if provided
    if update_data.username is not None:
        # Check if username is already taken
        result = await session.exec(select(User).where(User.username == update_data.username, User.id != user_id))
        existing = result.first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already taken",
            )
        user.username = update_data.username

    if update_data.is_active is not None:
        user.is_active = update_data.is_active

    if update_data.is_verified is not None:
        user.is_verified = update_data.is_verified

    session.add(user)
    await session.commit()
    await session.refresh(user)

    return UserResponse(
        id=user.id,
        uuid=user.uuid,
        email=user.email,
        username=user.username,
        role=user.role.value,
        is_verified=user.is_verified,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        contribution_points=user.contribution_points,
        contribution_level=user.contribution_level,
    )


@router.patch("/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: int,
    role_data: RoleUpdateRequest,
    admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Update a user's role (Admin only)."""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent self-demotion
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role",
        )

    # Validate role
    try:
        new_role = UserRole(role_data.role)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role: {role_data.role}. Valid roles: {[r.value for r in UserRole]}",
        )

    user.role = new_role
    session.add(user)
    await session.commit()
    await session.refresh(user)

    return UserResponse(
        id=user.id,
        uuid=user.uuid,
        email=user.email,
        username=user.username,
        role=user.role.value,
        is_verified=user.is_verified,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        contribution_points=user.contribution_points,
        contribution_level=user.contribution_level,
    )


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a user (Admin only). This will cascade delete all user data."""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent self-deletion
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    await session.delete(user)
    await session.commit()

    return {"message": "User deleted successfully"}
