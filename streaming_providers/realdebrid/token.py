from base64 import b64encode, b64decode


def encode_token_data(client_id: str, client_secret: str, code: str):
    token = f"{client_id}:{client_secret}:{code}"
    return b64encode(str(token).encode()).decode()


def get_token_data(token: str) -> dict[str, str]:
    client_id, client_secret, code = b64decode(token).decode().split(":")
    return {"client_id": client_id, "client_secret": client_secret, "code": code}
