import hashlib
import json
import logging
import secrets
import time
import zlib
from base64 import urlsafe_b64decode, urlsafe_b64encode

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad
from sqlmodel import select

from db.config import settings
from db.database import get_async_session_context
from db.models import User, UserProfile
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import UserData
from utils.profile_context import build_user_data_from_config
from utils.profile_crypto import profile_crypto

logger = logging.getLogger(__name__)

# Constants
REDIS_THRESHOLD = 1000  # Threshold for Redis storage in characters
DIRECT_PREFIX = "D-"  # Prefix for direct encrypted data
REDIS_PREFIX = "R-"  # Prefix for Redis-stored data
UUID_PREFIX = "U-"  # Prefix for UUID-based dynamic config resolution
UUID_CACHE_PREFIX = "user_profile:"  # Redis key prefix for UUID-cached profile data
UUID_CACHE_TTL = 2592000  # 30 days in seconds (same as R- expiry)


def make_urlsafe(data: bytes) -> str:
    """Convert bytes to URL-safe string using base64 alphabet"""
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def from_urlsafe(urlsafe_str: str) -> bytes:
    """Convert URL-safe string back to bytes"""
    padding_needed = len(urlsafe_str) % 4
    if padding_needed:
        urlsafe_str += "=" * (4 - padding_needed)
    return urlsafe_b64decode(urlsafe_str.encode("ascii"))


class CryptoUtils:
    def __init__(self):
        self.secret_key = settings.secret_key.encode("utf-8").ljust(32)[:32]

    def _generate_storage_key(self, data_hash: str, random_chars: str) -> str:
        """Generate Redis storage key with prefix"""
        return f"user_{data_hash}{random_chars}"

    def _generate_random_chars(self, length: int = None) -> str:
        """Generate random characters of variable length to add entropy"""
        if length is None:
            length = secrets.randbelow(14) + 5  # Random length between 5-18
        return secrets.token_urlsafe(length)[:length]

    def _compress_and_encrypt(self, data: str) -> tuple[bytes, bytes]:
        """Compress and encrypt data, returning both IV and final data"""
        # First compress the data
        compressed_data = zlib.compress(data.encode("utf-8"))

        # Then encrypt the compressed data
        iv = get_random_bytes(16)
        cipher = AES.new(self.secret_key, AES.MODE_CBC, iv)

        # Ensure proper padding
        padded_data = pad(compressed_data, AES.block_size)
        encrypted_data = cipher.encrypt(padded_data)

        return iv, encrypted_data

    def _decrypt_and_decompress(self, iv: bytes, encrypted_data: bytes) -> str:
        """Decrypt and decompress data"""
        cipher = AES.new(self.secret_key, AES.MODE_CBC, iv)
        decrypted_data = cipher.decrypt(encrypted_data)
        unpadded_data = unpad(decrypted_data, AES.block_size)
        return zlib.decompress(unpadded_data).decode("utf-8")

    async def process_user_data(self, user_data: UserData, expire_seconds: int = 2592000) -> str:
        """
        Process user data with optimized compression and encryption
        Returns prefixed string indicating storage method used
        """
        try:
            # Convert user data to JSON
            json_data = user_data.model_dump_json(
                exclude_none=True,
                exclude_defaults=True,
                exclude_unset=True,
                round_trip=True,
                by_alias=True,
            )

            # Compress and encrypt
            iv, encrypted_data = self._compress_and_encrypt(json_data)

            # Combine IV and encrypted data
            final_data = iv + encrypted_data

            # Convert to URL-safe string
            urlsafe_data = make_urlsafe(final_data)

            # Check length and decide storage method
            if len(urlsafe_data) <= REDIS_THRESHOLD:
                return f"{DIRECT_PREFIX}{urlsafe_data}"

            # Store in Redis if too long
            data_hash = hashlib.md5(final_data).hexdigest()
            random_chars = self._generate_random_chars()
            storage_key = self._generate_storage_key(data_hash, random_chars)

            # Store raw encrypted data in Redis (no need for URL-safe encoding)
            await REDIS_ASYNC_CLIENT.setex(storage_key, expire_seconds, final_data)

            return f"{REDIS_PREFIX}{data_hash}{random_chars}"

        except Exception as e:
            logger.error(f"Failed to process user data: {e}")
            raise ValueError("Failed to process user data")

    async def decrypt_user_data(self, secret_str: str) -> UserData:
        """
        Decrypt user data from either storage method
        Args:
            secret_str: Prefixed string containing either direct data, Redis key,
                        or UUID for dynamic config resolution
        Returns:
            UserData object
        """
        if not secret_str:
            return UserData()

        try:
            # Handle legacy format (no prefix)
            if not secret_str.startswith((DIRECT_PREFIX, REDIS_PREFIX, UUID_PREFIX)):
                raise ValueError("Invalid user data")

            prefix = secret_str[:2]
            data = secret_str[2:]

            if prefix == DIRECT_PREFIX:
                # Direct decryption
                final_data = from_urlsafe(data)
                iv = final_data[:16]
                encrypted_data = final_data[16:]
                json_str = self._decrypt_and_decompress(iv, encrypted_data)
                return UserData.model_validate_json(json_str)

            elif prefix == REDIS_PREFIX:
                return await self.retrieve_and_decrypt(data)

            elif prefix == UUID_PREFIX:
                return await self._resolve_uuid_profile(data)

            else:
                raise ValueError("Invalid prefix")

        except Exception as e:
            logger.error(f"Failed to decrypt user data: {e}")
            raise ValueError("Invalid user data")

    async def _resolve_uuid_profile(self, profile_uuid: str) -> UserData:
        """
        Resolve a profile UUID to UserData by checking Redis cache first,
        then falling back to database lookup.

        Args:
            profile_uuid: The profile UUID string

        Returns:
            UserData object built from the profile's current config

        Raises:
            ValueError: If profile not found or resolution fails
        """
        if not profile_uuid or len(profile_uuid) < 32:
            raise ValueError("Invalid profile UUID")

        cache_key = f"{UUID_CACHE_PREFIX}{profile_uuid}"

        # 1. Try Redis cache first
        try:
            cached_json = await REDIS_ASYNC_CLIENT.getex(
                cache_key,
                ex=UUID_CACHE_TTL,  # Refresh TTL on access
            )
            if cached_json:
                cached_data = json.loads(cached_json)
                return self._build_user_data_from_cached_uuid(cached_data)
        except Exception as e:
            logger.warning(f"Failed to get UUID cache for {profile_uuid}: {e}")

        # 2. Cache miss - load from database
        return await self._load_and_cache_uuid_profile(profile_uuid)

    async def _load_and_cache_uuid_profile(self, profile_uuid: str) -> UserData:
        """
        Load profile from database by UUID, cache it in Redis, and return UserData.

        Uses a standalone async session (not from FastAPI DI) so this can be called
        from middleware context without requiring a session dependency.

        Args:
            profile_uuid: The profile UUID to look up

        Returns:
            UserData built from the profile's current config

        Raises:
            ValueError: If profile or user not found
        """
        async with get_async_session_context() as session:
            # Load profile by UUID with its user relationship
            result = await session.exec(select(UserProfile).where(UserProfile.uuid == profile_uuid))
            profile = result.first()

            if not profile:
                raise ValueError("Profile not found or deleted")

            # Load the user for UUID-based identification
            user = await session.get(User, profile.user_id)
            if not user:
                raise ValueError("User not found or deleted")

            # Decrypt secrets and build full config
            full_config = profile_crypto.get_full_config(profile.config, profile.encrypted_secrets)

            # Cache the profile data in Redis for future requests
            # Store: config, encrypted_secrets, user_id, profile_id, user_uuid, profile_uuid, api_password
            cache_data = {
                "config": profile.config or {},
                "encrypted_secrets": profile.encrypted_secrets,
                "user_id": user.id,
                "profile_id": profile.id,
                "user_uuid": user.uuid,
                "profile_uuid": profile.uuid,
            }
            # Include api_password if it exists in the config
            api_password = full_config.get("api_password") or full_config.get("ap")
            if api_password:
                cache_data["api_password"] = api_password

            await self._cache_uuid_profile(profile_uuid, cache_data)

            # Build UserData from the full config
            user_data = build_user_data_from_config(
                full_config,
                user_id=user.id,
                profile_id=profile.id,
                user_uuid=user.uuid,
                profile_uuid=profile.uuid,
            )
            return user_data

    def _build_user_data_from_cached_uuid(self, cached_data: dict) -> UserData:
        """
        Build UserData from UUID-cached profile data.

        The cached data contains the raw config and encrypted secrets,
        which are decrypted per-request (secrets are never stored in
        plaintext in Redis).

        Args:
            cached_data: Dict with config, encrypted_secrets, and identification fields

        Returns:
            UserData instance
        """
        config = cached_data.get("config", {})
        encrypted_secrets = cached_data.get("encrypted_secrets")

        # Decrypt secrets per-request
        full_config = profile_crypto.get_full_config(config, encrypted_secrets)

        return build_user_data_from_config(
            full_config,
            user_id=cached_data.get("user_id"),
            profile_id=cached_data.get("profile_id"),
            user_uuid=cached_data.get("user_uuid"),
            profile_uuid=cached_data.get("profile_uuid"),
            api_password=cached_data.get("api_password"),
        )

    async def _cache_uuid_profile(self, profile_uuid: str, cache_data: dict) -> None:
        """
        Cache profile data in Redis keyed by profile UUID.

        Args:
            profile_uuid: The profile UUID
            cache_data: Dict containing config, encrypted_secrets, and identification
        """
        cache_key = f"{UUID_CACHE_PREFIX}{profile_uuid}"
        try:
            await REDIS_ASYNC_CLIENT.setex(
                cache_key,
                UUID_CACHE_TTL,
                json.dumps(cache_data),
            )
            logger.debug(f"Cached UUID profile data for {profile_uuid}")
        except Exception as e:
            logger.warning(f"Failed to cache UUID profile for {profile_uuid}: {e}")

    @staticmethod
    async def invalidate_uuid_cache(profile_uuid: str) -> None:
        """
        Invalidate the UUID-keyed Redis cache for a profile.

        Call this whenever a profile's config is updated or the profile is deleted.

        Args:
            profile_uuid: The profile UUID to invalidate
        """
        cache_key = f"{UUID_CACHE_PREFIX}{profile_uuid}"
        try:
            await REDIS_ASYNC_CLIENT.delete(cache_key)
            logger.debug(f"Invalidated UUID cache for profile {profile_uuid}")
        except Exception as e:
            logger.warning(f"Failed to invalidate UUID cache for {profile_uuid}: {e}")

    def decode_user_data(self, encoded_user_data: str) -> UserData:
        """Decode and decrypt user data from URL-safe string"""
        try:
            json_str = from_urlsafe(encoded_user_data)
            return UserData.model_validate_json(json_str)
        except Exception:
            raise ValueError("Invalid user data")

    def encode_user_data(self, user_data: UserData) -> str:
        """Encode and encrypt user data to URL-safe string"""
        try:
            json_str = user_data.model_dump_json(
                exclude_none=True,
                exclude_defaults=True,
                exclude_unset=True,
                round_trip=True,
                by_alias=True,
            )
            return make_urlsafe(json_str.encode("utf-8"))
        except Exception:
            raise ValueError("Failed to encode user data")

    async def retrieve_and_decrypt(self, storage_id: str) -> UserData:
        """Retrieve and decrypt user data from Redis"""
        if not storage_id or len(storage_id) < 37:
            raise ValueError("Invalid storage ID")

        try:
            data_hash = storage_id[:32]
            random_chars = storage_id[32:]
            storage_key = self._generate_storage_key(data_hash, random_chars)

            # Get data and update expiry
            encrypted_data = await REDIS_ASYNC_CLIENT.getex(
                storage_key,
                ex=2592000,  # Reset expiry to 30 days on access
            )

            if not encrypted_data:
                raise ValueError("User data not found or expired")

            # Decrypt the raw data from Redis
            iv = encrypted_data[:16]
            data = encrypted_data[16:]
            json_str = self._decrypt_and_decompress(iv, data)

            return UserData.model_validate_json(json_str)

        except Exception as e:
            logger.error(f"Failed to retrieve and decrypt user data: {e}")
            raise ValueError("Invalid or expired user data")


# Keep existing functions for backward compatibility
def encrypt_text(text: str, secret_key: str | bytes) -> str:
    """Legacy encryption function - kept for backward compatibility"""
    iv = get_random_bytes(16)
    if isinstance(secret_key, str):
        secret_key = secret_key.encode("utf-8")
    cipher = AES.new(secret_key.ljust(32)[:32], AES.MODE_CBC, iv)
    encoded_text = text.encode("utf-8")
    encrypted_data = cipher.encrypt(encoded_text + b"\0" * (16 - len(encoded_text) % 16))
    compressed_data = zlib.compress(iv + encrypted_data)
    encrypted_str = urlsafe_b64encode(compressed_data).decode("utf-8")
    return encrypted_str


def decrypt_text(secret_str: str, secret_key: str | bytes) -> str:
    """Legacy decryption function - kept for backward compatibility"""
    decoded_data = urlsafe_b64decode(secret_str)
    encrypted_data = zlib.decompress(decoded_data)
    iv = encrypted_data[:16]
    if isinstance(secret_key, str):
        secret_key = secret_key.encode("utf-8")
    cipher = AES.new(secret_key.ljust(32)[:32], AES.MODE_CBC, iv)
    decrypted_data = cipher.decrypt(encrypted_data[16:])
    decrypted_data = decrypted_data.rstrip(b"\0")
    return decrypted_data.decode("utf-8")


def get_text_hash(text: str, full_hash: bool = False) -> str:
    hash_str = hashlib.sha256(text.encode()).hexdigest()
    return hash_str if full_hash else hash_str[:10]


def encrypt_data(secret_key: str, data: dict, expiration: int = None, ip: str = None) -> str:
    if expiration:
        data["exp"] = int(time.time()) + expiration
    if ip:
        data["ip"] = ip
    json_data = json.dumps(data).encode("utf-8")
    iv = get_random_bytes(16)
    cipher = AES.new(secret_key.encode("utf-8").ljust(32)[:32], AES.MODE_CBC, iv)
    encrypted_data = cipher.encrypt(pad(json_data, AES.block_size))
    return urlsafe_b64encode(iv + encrypted_data).decode("utf-8")


crypto_utils = CryptoUtils()
