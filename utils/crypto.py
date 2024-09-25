import base64
import hashlib
import json
import time

import zlib
from base64 import urlsafe_b64encode, urlsafe_b64decode

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad

from db.schemas import UserData
from utils.runtime_const import SECRET_KEY


def encrypt_user_data(user_data: UserData) -> str:
    data = user_data.model_dump_json(
        exclude_none=True,
        exclude_defaults=True,
        exclude_unset=True,
        round_trip=True,
        by_alias=True,
    )
    dump_data = data.encode("utf-8")
    iv = get_random_bytes(16)
    cipher = AES.new(SECRET_KEY, AES.MODE_CBC, iv)
    encrypted_data = cipher.encrypt(dump_data + b"\0" * (16 - len(dump_data) % 16))
    compressed_data = zlib.compress(iv + encrypted_data)
    encrypted_str = urlsafe_b64encode(compressed_data).decode("utf-8")
    return encrypted_str


def decrypt_user_data(secret_str: str | None = None) -> UserData:
    if not secret_str:
        return UserData()
    try:
        decoded_data = urlsafe_b64decode(secret_str)
        encrypted_data = zlib.decompress(decoded_data)
        iv = encrypted_data[:16]
        cipher = AES.new(SECRET_KEY, AES.MODE_CBC, iv)
        decrypted_data = cipher.decrypt(encrypted_data[16:])
        decrypted_data = decrypted_data.rstrip(b"\0")
        user_data = UserData.model_validate_json(decrypted_data.decode("utf-8"))
    except Exception:
        user_data = UserData()
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
    return base64.urlsafe_b64encode(iv + encrypted_data).decode("utf-8")
