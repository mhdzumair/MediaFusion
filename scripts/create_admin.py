#!/usr/bin/env python3
"""
Script to create an admin user for MediaFusion.

Usage:
    python scripts/create_admin.py --email admin@example.com --password secretpassword
    python scripts/create_admin.py --email admin@example.com --password secretpassword --username admin
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime

import pytz
from sqlmodel import select

from api.routers.user.auth import hash_password
from db.database import get_async_session
from db.enums import UserRole
from db.models import User, UserProfile


async def create_admin_user(
    email: str,
    password: str,
    username: str | None = None,
) -> None:
    """Create an admin user with default profile."""

    async for session in get_async_session():
        # Check if email already exists
        result = await session.exec(select(User).where(User.email == email))
        existing = result.first()

        if existing:
            if existing.role == UserRole.ADMIN:
                print(f"✅ Admin user already exists: {email}")
                return
            else:
                # Upgrade existing user to admin
                existing.role = UserRole.ADMIN
                await session.commit()
                print(f"✅ Upgraded existing user to admin: {email}")
                return

        # Check if username exists
        if username:
            result = await session.exec(select(User).where(User.username == username))
            if result.first():
                print(f"❌ Username already taken: {username}")
                return

        # Create admin user
        user = User(
            email=email,
            username=username or email.split("@")[0],
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
            is_verified=True,
            is_active=True,
            last_login=datetime.now(pytz.UTC),
        )
        session.add(user)
        await session.flush()  # Get user.id

        # Create default profile
        profile = UserProfile(
            user_id=user.id,
            name="Default",
            config={},
            is_default=True,
        )
        session.add(profile)
        await session.commit()

        print("✅ Admin user created successfully!")
        print(f"   Email: {email}")
        print(f"   Username: {user.username}")
        print(f"   Role: {user.role.value}")


def main():
    parser = argparse.ArgumentParser(description="Create an admin user for MediaFusion")
    parser.add_argument("--email", "-e", required=True, help="Admin email address")
    parser.add_argument("--password", "-p", required=True, help="Admin password")
    parser.add_argument("--username", "-u", help="Admin username (defaults to email prefix)")

    args = parser.parse_args()

    asyncio.run(
        create_admin_user(
            email=args.email,
            password=args.password,
            username=args.username,
        )
    )


if __name__ == "__main__":
    main()
