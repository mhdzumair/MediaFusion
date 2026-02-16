"""Bootstrap utilities for initial admin setup on first deployment.

On first startup (no users in DB), creates a temporary bootstrap admin account
with a randomly generated password. The deployer logs in with this account
(credentials are printed to the server log), creates their own admin account
via the setup wizard, and the bootstrap admin is automatically deactivated.
"""

import logging
import secrets
import string

from sqlalchemy import func, select

from db.enums import UserRole
from db.models import User, UserProfile

logger = logging.getLogger(__name__)

# Bootstrap admin constants
BOOTSTRAP_EMAIL = "bootstrap@mediafusion.local"
BOOTSTRAP_USERNAME = "bootstrap-admin"
BOOTSTRAP_EMAIL_DOMAIN = "@mediafusion.local"

# Generated once per process startup; only meaningful when ensure_bootstrap_admin
# actually creates the user (fresh deployment). Stored in memory so the log
# message can reference it.
_bootstrap_password: str | None = None


def _generate_bootstrap_password(length: int = 20) -> str:
    """Generate a cryptographically secure random password."""
    alphabet = string.ascii_letters + string.digits + string.punctuation
    return "".join(secrets.choice(alphabet) for _ in range(length))

# In-memory flag to avoid repeated DB queries after setup is complete.
# Each worker process maintains its own copy; after setup completes the
# flag is flipped and stays False for the lifetime of the process.
_setup_required: bool | None = None


def is_bootstrap_user(user: User) -> bool:
    """Check whether a user is the bootstrap admin account."""
    return user.email == BOOTSTRAP_EMAIL


async def check_setup_required(session) -> bool:
    """Check whether the initial admin setup is still required.

    Returns True when either:
    - No users exist at all in the database, OR
    - The only active admin user is the bootstrap admin
    """
    global _setup_required

    # Fast path: if we already determined setup is complete, skip DB query
    if _setup_required is False:
        return False

    # Check if any real (non-bootstrap) admin exists
    result = await session.execute(
        select(func.count(User.id)).where(
            User.role == UserRole.ADMIN,
            User.is_active.is_(True),
            User.email != BOOTSTRAP_EMAIL,
        )
    )
    real_admin_count = result.scalar_one()

    if real_admin_count > 0:
        _setup_required = False
        return False

    _setup_required = True
    return True


def mark_setup_complete():
    """Mark setup as complete in the in-memory cache.

    Called after the setup endpoint successfully creates a real admin user
    so subsequent requests skip the DB check.
    """
    global _setup_required
    _setup_required = False


async def ensure_bootstrap_admin(session) -> None:
    """Create the bootstrap admin user if no users exist in the database.

    This is called during application startup (lifespan). It only creates
    the bootstrap admin when the user table is completely empty, meaning
    this is a fresh deployment. A random password is generated each time
    and printed to the server log.
    """
    global _bootstrap_password

    from api.routers.user.auth import hash_password

    # Count total users
    result = await session.execute(select(func.count(User.id)))
    user_count = result.scalar_one()

    if user_count > 0:
        logger.info("Users already exist in database, skipping bootstrap admin creation.")
        return

    # Generate a random password for this bootstrap session
    _bootstrap_password = _generate_bootstrap_password()

    # Create bootstrap admin
    user = User(
        email=BOOTSTRAP_EMAIL,
        username=BOOTSTRAP_USERNAME,
        password_hash=hash_password(_bootstrap_password),
        role=UserRole.ADMIN,
        is_verified=True,
        is_active=True,
    )
    session.add(user)
    await session.flush()

    # Create default profile
    profile = UserProfile(
        user_id=user.id,
        name="Default",
        config={},
        is_default=True,
    )
    session.add(profile)
    await session.commit()

    logger.info("=" * 60)
    logger.info("INITIAL SETUP REQUIRED")
    logger.info("=" * 60)
    logger.info("A bootstrap admin account has been created.")
    logger.info("Open the web UI to complete setup.")
    logger.info("")
    logger.info("  Email:    %s", BOOTSTRAP_EMAIL)
    logger.info("  Password: %s", _bootstrap_password)
    logger.info("")
    logger.info("You will also need your API_PASSWORD to proceed.")
    logger.info("")
    logger.info("You will be guided to create your own admin account.")
    logger.info("The bootstrap account will be deactivated automatically.")
    logger.info("=" * 60)
