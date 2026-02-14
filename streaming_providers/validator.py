from fastapi import Request

from db.schemas import UserData
from streaming_providers.mapper import VALIDATE_CREDENTIALS_FUNCTIONS
from utils.network import get_user_public_ip


async def validate_provider_credentials(request: Request, user_data: UserData) -> dict:
    """Validate all provider credentials in the streaming_providers list."""
    providers = user_data.get_active_providers()
    if not providers:
        return {"status": "success"}

    user_ip = await get_user_public_ip(request, user_data)

    # Validate each provider
    for provider in providers:
        # Skip providers that don't require credential validation (e.g., p2p)
        if provider.service not in VALIDATE_CREDENTIALS_FUNCTIONS:
            continue

        result = await VALIDATE_CREDENTIALS_FUNCTIONS[provider.service](streaming_provider=provider, user_ip=user_ip)

        if result.get("status") == "error":
            return result

    return {"status": "success"}
