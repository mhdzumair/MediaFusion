import hashlib
import json
import logging
import time
import zlib
from base64 import urlsafe_b64decode, urlsafe_b64encode
from threading import Lock

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
URL_EMBEDDED_USERDATA_MAX_LEN = 2000  # Max url-safe chars for D- (direct) encrypted payload
DIRECT_PREFIX = "D-"  # Prefix for direct encrypted data
REDIS_LEGACY_PREFIX = "R-"  # Deprecated: was Redis-stored pointer; rejected with guidance to use U-
UUID_PREFIX = "U-"  # Prefix for UUID-based dynamic config resolution
UUID_CACHE_PREFIX = "user_profile:"  # Redis key prefix for UUID-cached profile data
UUID_CACHE_TTL = 2592000  # 30 days in seconds

CONFIG_TOO_LARGE_FOR_URL_MESSAGE = (
    "Your configuration is too large to embed in the install URL. "
    "Sign in and save a profile (logged-in install), then use the install link that starts with U-."
)

DEPRECATED_REDIS_BACKED_SECRET_MESSAGE = (
    "This link used a removed configuration format that depended on server cache (R-). "
    "Sign in, save your settings as a profile, and install using the profile link (starts with U-)."
)

DECRYPT_CACHE_TTL_SECONDS = 120  # in-process cache TTL for decrypted secret strings
DECRYPT_CACHE_MAX_ENTRIES = 2048  # bounded cache size to avoid unbounded memory growth
INVALID_SECRET_CACHE_TTL_SECONDS = 300  # short-circuit repeated invalid secrets for 5 minutes
INVALID_SECRET_CACHE_MAX_ENTRIES = 4096  # bounded size for invalid-secret bloom cache
MAX_SECRET_STR_LENGTH = 4096  # defensive cap against very large malformed inputs


class UserFacingSecretError(ValueError):
    """Raised when secret handling fails with a message that is safe to show to the user."""


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
        self._decrypted_user_data_cache: dict[str, tuple[float, UserData]] = {}
        self._decrypted_user_data_cache_mu = Lock()
        self._invalid_secret_cache: dict[str, float] = {}
        self._invalid_secret_cache_mu = Lock()

    def _get_cached_decrypted_user_data(self, secret_str: str) -> UserData | None:
        """Return cached decrypted UserData for secret_str if still valid."""
        if not secret_str:
            return None

        now = time.monotonic()
        with self._decrypted_user_data_cache_mu:
            cache_item = self._decrypted_user_data_cache.get(secret_str)
            if not cache_item:
                return None

            expires_at, cached_user_data = cache_item
            if expires_at <= now:
                self._decrypted_user_data_cache.pop(secret_str, None)
                return None

            # Return a defensive copy so request-level code cannot mutate shared cache state.
            return cached_user_data.model_copy(deep=True)

    def _evict_expired_decrypt_cache_entries(self, now: float) -> None:
        expired_keys = [
            cache_key for cache_key, (expires_at, _) in self._decrypted_user_data_cache.items() if expires_at <= now
        ]
        for cache_key in expired_keys:
            self._decrypted_user_data_cache.pop(cache_key, None)

    def _cache_decrypted_user_data(self, secret_str: str, user_data: UserData) -> None:
        """Cache decrypted UserData for a short duration to reduce repeated decrypt CPU cost."""
        if not secret_str:
            return

        now = time.monotonic()
        with self._decrypted_user_data_cache_mu:
            self._evict_expired_decrypt_cache_entries(now)

            if len(self._decrypted_user_data_cache) >= DECRYPT_CACHE_MAX_ENTRIES:
                # Pop oldest inserted entry (dict preserves insertion order in modern Python).
                oldest_key = next(iter(self._decrypted_user_data_cache), None)
                if oldest_key is not None:
                    self._decrypted_user_data_cache.pop(oldest_key, None)

            self._decrypted_user_data_cache[secret_str] = (
                now + DECRYPT_CACHE_TTL_SECONDS,
                user_data.model_copy(deep=True),
            )

    def _invalidate_decrypt_cache_prefix(self, secret_prefix: str) -> None:
        """Invalidate cached decrypted payloads for all keys with the given prefix."""
        with self._decrypted_user_data_cache_mu:
            matching_keys = [key for key in self._decrypted_user_data_cache if key.startswith(secret_prefix)]
            for key in matching_keys:
                self._decrypted_user_data_cache.pop(key, None)

    @staticmethod
    def _secret_digest(secret_str: str) -> str:
        return hashlib.sha256(secret_str.encode("utf-8")).hexdigest()

    def _evict_expired_invalid_secret_entries(self, now: float) -> None:
        expired_digests = [digest for digest, expires_at in self._invalid_secret_cache.items() if expires_at <= now]
        for digest in expired_digests:
            self._invalid_secret_cache.pop(digest, None)

    def _is_known_invalid_secret(self, secret_str: str) -> bool:
        now = time.monotonic()
        digest = self._secret_digest(secret_str)
        with self._invalid_secret_cache_mu:
            expires_at = self._invalid_secret_cache.get(digest)
            if expires_at is None:
                return False
            if expires_at <= now:
                self._invalid_secret_cache.pop(digest, None)
                return False
            return True

    def _mark_invalid_secret(self, secret_str: str) -> bool:
        """
        Mark secret as invalid and return True only when this is a fresh mark.
        """
        now = time.monotonic()
        digest = self._secret_digest(secret_str)
        with self._invalid_secret_cache_mu:
            self._evict_expired_invalid_secret_entries(now)
            already_known = digest in self._invalid_secret_cache
            if len(self._invalid_secret_cache) >= INVALID_SECRET_CACHE_MAX_ENTRIES:
                oldest_digest = next(iter(self._invalid_secret_cache), None)
                if oldest_digest is not None:
                    self._invalid_secret_cache.pop(oldest_digest, None)
            self._invalid_secret_cache[digest] = now + INVALID_SECRET_CACHE_TTL_SECONDS
            return not already_known

    def _clear_invalid_secret(self, secret_str: str) -> None:
        digest = self._secret_digest(secret_str)
        with self._invalid_secret_cache_mu:
            self._invalid_secret_cache.pop(digest, None)

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

    async def process_user_data(self, user_data: UserData) -> str:
        """
        Compress, encrypt, and return a D- prefixed URL-safe secret.

        Config larger than URL_EMBEDDED_USERDATA_MAX_LEN cannot be embedded; callers must use a
        stored profile (U-{{uuid}}) instead of Redis-backed R- pointers.
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

            if len(urlsafe_data) <= URL_EMBEDDED_USERDATA_MAX_LEN:
                return f"{DIRECT_PREFIX}{urlsafe_data}"

            raise UserFacingSecretError(CONFIG_TOO_LARGE_FOR_URL_MESSAGE)

        except UserFacingSecretError:
            raise
        except Exception as e:
            logger.error(f"Failed to process user data: {e}")
            raise ValueError("Failed to process user data")

    async def decrypt_user_data(self, secret_str: str) -> UserData:
        """
        Decrypt user data from a D- (direct) or U- (profile UUID) secret.
        Args:
            secret_str: Prefixed string (D- encrypted payload or U- profile UUID).
        Returns:
            UserData object
        """
        if not secret_str:
            return UserData()

        if len(secret_str) > MAX_SECRET_STR_LENGTH:
            self._mark_invalid_secret(secret_str)
            raise ValueError("Invalid user data")

        if self._is_known_invalid_secret(secret_str):
            raise ValueError("Invalid user data")

        cached_user_data = self._get_cached_decrypted_user_data(secret_str)
        if cached_user_data is not None:
            return cached_user_data

        try:
            if secret_str.startswith(REDIS_LEGACY_PREFIX):
                raise UserFacingSecretError(DEPRECATED_REDIS_BACKED_SECRET_MESSAGE)

            if not secret_str.startswith((DIRECT_PREFIX, UUID_PREFIX)):
                raise ValueError("Invalid user data")

            prefix = secret_str[:2]
            data = secret_str[2:]

            if prefix == DIRECT_PREFIX:
                # Direct decryption
                final_data = from_urlsafe(data)
                iv = final_data[:16]
                encrypted_data = final_data[16:]
                json_str = self._decrypt_and_decompress(iv, encrypted_data)
                user_data = UserData.model_validate_json(json_str)
                self._clear_invalid_secret(secret_str)
                self._cache_decrypted_user_data(secret_str, user_data)
                return user_data

            if prefix == UUID_PREFIX:
                user_data = await self._resolve_uuid_profile(data)
                self._clear_invalid_secret(secret_str)
                self._cache_decrypted_user_data(secret_str, user_data)
                return user_data

            raise ValueError("Invalid prefix")

        except UserFacingSecretError:
            raise
        except ValueError as e:
            is_new_invalid_secret = self._mark_invalid_secret(secret_str)
            log_fn = logger.warning if is_new_invalid_secret else logger.debug
            log_fn(
                "Rejected invalid user data secret (prefix=%s, len=%s): %s",
                secret_str[:2] if secret_str else "NA",
                len(secret_str) if secret_str else 0,
                e,
            )
            raise ValueError("Invalid user data")
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

    async def invalidate_uuid_cache(self, profile_uuid: str) -> None:
        """
        Invalidate the UUID-keyed Redis cache for a profile.

        Call this whenever a profile's config is updated or the profile is deleted.

        Args:
            profile_uuid: The profile UUID to invalidate
        """
        cache_key = f"{UUID_CACHE_PREFIX}{profile_uuid}"
        try:
            await REDIS_ASYNC_CLIENT.delete(cache_key)
            self._invalidate_decrypt_cache_prefix(f"{UUID_PREFIX}{profile_uuid}")
            logger.debug(f"Invalidated UUID cache for profile {profile_uuid}")
        except Exception as e:
            logger.warning(f"Failed to invalidate UUID cache for {profile_uuid}: {e}")

    def format_profile_uuid_secret(self, profile_uuid: str) -> str:
        """Return the U-{{uuid}} path segment for profile-backed addon and playback URLs."""
        if not profile_uuid:
            return ""
        return f"{UUID_PREFIX}{profile_uuid}"

    async def prime_profile_uuid_cache(
        self,
        profile: UserProfile,
        user: User,
        api_password: str | None = None,
    ) -> None:
        """
        Optional Redis warm-up before the first U-{{uuid}} request.

        Not required for correctness: cache miss loads the profile from the database and
        populates Redis (see _load_and_cache_uuid_profile). Used when exposing manifest
        install links so the first addon hit can avoid a DB round trip.

        api_password: if the instance API key is not stored in the profile config, the
        manifest URL handler may pass X-API-Key here so Stremio (no header) still resolves
        UserData with the correct key on private instances.
        """
        cache_data = {
            "config": profile.config or {},
            "encrypted_secrets": profile.encrypted_secrets,
            "user_id": user.id,
            "profile_id": profile.id,
            "user_uuid": user.uuid,
            "profile_uuid": profile.uuid,
        }
        if api_password:
            cache_data["api_password"] = api_password
        await self._cache_uuid_profile(profile.uuid, cache_data)

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
