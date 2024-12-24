import hashlib
import json
import logging
import secrets
import time
import zlib
from base64 import urlsafe_b64encode, urlsafe_b64decode
from typing import Tuple

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import UserData
from utils.runtime_const import SECRET_KEY

logger = logging.getLogger(__name__)

# Constants
REDIS_THRESHOLD = 1000  # Threshold for Redis storage in characters
DIRECT_PREFIX = "D-"  # Prefix for direct encrypted data
REDIS_PREFIX = "R-"  # Prefix for Redis-stored data


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

    def _compress_and_encrypt(self, data: str) -> Tuple[bytes, bytes]:
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

    async def process_user_data(
        self, user_data: UserData, expire_seconds: int = 2592000
    ) -> str:
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
            secret_str: Prefixed string containing either direct data or Redis key
        Returns:
            UserData object
        """
        if not secret_str:
            return UserData()

        try:
            # Handle legacy format (no prefix)
            if not secret_str.startswith((DIRECT_PREFIX, REDIS_PREFIX)):
                return decrypt_legacy_user_data(secret_str)

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
            else:
                raise ValueError("Invalid prefix")

        except Exception as e:
            logger.error(f"Failed to decrypt user data: {e}")
            raise ValueError("Invalid user data")

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
                storage_key, ex=2592000  # Reset expiry to 30 days on access
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
    encrypted_data = cipher.encrypt(
        encoded_text + b"\0" * (16 - len(encoded_text) % 16)
    )
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


def decrypt_legacy_user_data(secret_str: str | None = None) -> UserData:
    """Legacy decryption function for existing user configs"""
    if not secret_str:
        return UserData()

    try:
        decrypted_data = decrypt_text(secret_str, SECRET_KEY)
    except Exception as e:
        logger.error(f"Failed to decrypt legacy user data: {e}")
        raise ValueError("Invalid user data")
    user_data = UserData.model_validate_json(decrypted_data)
    return user_data


def get_text_hash(text: str, full_hash: bool = False) -> str:
    hash_str = hashlib.sha256(text.encode()).hexdigest()
    return hash_str if full_hash else hash_str[:10]


def encrypt_data(
    secret_key: str, data: dict, expiration: int = None, ip: str = None
) -> str:
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
