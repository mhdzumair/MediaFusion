from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from base64 import urlsafe_b64encode, urlsafe_b64decode

from db.config import settings
from db.schemas import UserData


def encrypt_user_data(user_data: UserData) -> str:
    data = user_data.model_dump_json(
        exclude_none=True, exclude_defaults=True, exclude_unset=True, round_trip=True
    ).encode("utf-8")
    iv = get_random_bytes(16)
    cipher = AES.new(settings.secret_key.encode("utf-8"), AES.MODE_CBC, iv)
    encrypted_data = cipher.encrypt(data + b"\0" * (16 - len(data) % 16))
    encrypted_str = urlsafe_b64encode(iv + encrypted_data).decode("utf-8")
    return encrypted_str


def decrypt_user_data(secret_str: str | None = None) -> UserData:
    if not secret_str:
        return UserData()
    try:
        encrypted_data = urlsafe_b64decode(secret_str)
        iv = encrypted_data[:16]
        cipher = AES.new(settings.secret_key.encode("utf-8"), AES.MODE_CBC, iv)
        decrypted_data = cipher.decrypt(encrypted_data[16:])
        decrypted_data = decrypted_data.rstrip(b"\0")
        user_data = UserData.model_validate_json(decrypted_data.decode("utf-8"))
    except Exception:
        user_data = UserData()
    return user_data
