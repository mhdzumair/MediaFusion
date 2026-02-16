"""Setup utilities for initial admin creation on first deployment.

On first startup (no users in DB at all), the web UI shows a setup wizard
that asks the deployer to verify their API_PASSWORD and create an admin
account directly. No temporary bootstrap user is created.

SECURITY: The setup endpoint is only available when the user table is
completely empty. Once ANY user exists (admin, regular, active, or inactive),
setup is permanently locked.
"""

import logging

from sqlalchemy import func, select

from db.models import User

logger = logging.getLogger(__name__)

# In-memory flag to avoid repeated DB queries after setup is complete.
# Each worker process maintains its own copy; after setup completes the
# flag is flipped and stays False for the lifetime of the process.
_setup_required: bool | None = None


async def check_setup_required(session) -> bool:
    """Check whether the initial admin setup is still required.

    Returns True ONLY when the user table is completely empty (zero rows).
    Once any user exists -- regardless of role, active status, or any other
    attribute -- this returns False permanently.
    """
    global _setup_required

    # Fast path: if we already determined setup is complete, skip DB query
    if _setup_required is False:
        return False

    result = await session.execute(select(func.count(User.id)))
    user_count = result.scalar_one()

    if user_count > 0:
        _setup_required = False
        return False

    _setup_required = True
    return True


def mark_setup_complete():
    """Mark setup as complete in the in-memory cache.

    Called after the setup endpoint successfully creates the first admin user
    so subsequent requests skip the DB check.
    """
    global _setup_required
    _setup_required = False
