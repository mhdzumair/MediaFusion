"""
Profile Crypto Utilities

Handles encryption/decryption of sensitive profile configuration data.
Secrets (tokens, passwords, API keys) are stored encrypted, while
non-sensitive config is stored as plain JSON.
"""

import json
import logging
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

from db.config import settings

logger = logging.getLogger(__name__)

# Fields that contain sensitive data and should be encrypted
SENSITIVE_FIELDS = {
    # StreamingProvider secrets
    "token",
    "password",
    "email",  # Some services use email as credential
    # QBittorrent config
    "qb_password",
    "qb_username",
    # MediaFlow config
    "mediaflow_api_password",
    # RPDB config
    "rpdb_api_key",
    # MDBList config
    "mdblist_api_key",
    # API password
    "api_password",
}

# Nested paths to sensitive fields (for multi-provider support)
SENSITIVE_PATHS = [
    ("streaming_providers", "*", "token"),
    ("streaming_providers", "*", "password"),
    ("streaming_providers", "*", "email"),
    ("streaming_providers", "*", "qbittorrent_config", "qb_password"),
    ("streaming_providers", "*", "qbittorrent_config", "qb_username"),
    ("streaming_provider", "token"),
    ("streaming_provider", "password"),
    ("streaming_provider", "email"),
    ("streaming_provider", "qbittorrent_config", "qb_password"),
    ("streaming_provider", "qbittorrent_config", "qb_username"),
    ("mediaflow_config", "api_password"),
    ("rpdb_config", "api_key"),
    ("mdblist_config", "api_key"),
    ("api_password",),
]


class ProfileCrypto:
    """Handles encryption/decryption of profile secrets."""

    def __init__(self):
        self.secret_key = settings.secret_key.encode("utf-8").ljust(32)[:32]

    def _encrypt(self, data: str) -> str:
        """Encrypt string data and return base64-encoded result."""
        try:
            iv = get_random_bytes(16)
            cipher = AES.new(self.secret_key, AES.MODE_CBC, iv)
            padded_data = pad(data.encode("utf-8"), AES.block_size)
            encrypted_data = cipher.encrypt(padded_data)
            return urlsafe_b64encode(iv + encrypted_data).decode("ascii")
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise ValueError("Failed to encrypt data")

    def _decrypt(self, encrypted_data: str) -> str:
        """Decrypt base64-encoded data and return string."""
        try:
            raw_data = urlsafe_b64decode(encrypted_data.encode("ascii"))
            iv = raw_data[:16]
            cipher = AES.new(self.secret_key, AES.MODE_CBC, iv)
            decrypted_data = cipher.decrypt(raw_data[16:])
            unpadded_data = unpad(decrypted_data, AES.block_size)
            return unpadded_data.decode("utf-8")
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise ValueError("Failed to decrypt data")

    def _extract_value_at_path(self, data: dict, path: tuple, collected: dict) -> None:
        """Extract value at nested path, supporting wildcards for lists."""
        if not path:
            return

        key = path[0]
        remaining = path[1:]

        if key == "*":
            # Wildcard - iterate over list items
            parent_key = list(collected.keys())[-1] if collected else None
            if parent_key and isinstance(data, list):
                for i, item in enumerate(data):
                    if isinstance(item, dict) and remaining:
                        self._extract_value_at_path(
                            item,
                            remaining,
                            collected.setdefault(f"{parent_key}[{i}]", {}),
                        )
        elif key in data:
            value = data[key]
            if not remaining:
                # This is the final key - extract the value
                if value is not None:
                    collected[key] = value
            elif isinstance(value, dict):
                self._extract_value_at_path(value, remaining, collected.setdefault(key, {}))
            elif isinstance(value, list):
                for i, item in enumerate(data[key]):
                    if isinstance(item, dict):
                        sub_collected = collected.setdefault(f"{key}[{i}]", {})
                        self._extract_value_at_path(item, remaining, sub_collected)

    def _set_value_at_path(self, data: dict, path: tuple, value: Any) -> None:
        """Set value at nested path."""
        if not path:
            return

        key = path[0]
        remaining = path[1:]

        if not remaining:
            data[key] = value
        else:
            if key not in data:
                data[key] = {}
            self._set_value_at_path(data[key], remaining, value)

    def _remove_value_at_path(self, data: dict, path: tuple) -> None:
        """Remove value at nested path."""
        if not path or not isinstance(data, dict):
            return

        key = path[0]
        remaining = path[1:]

        if key == "*":
            return  # Can't remove wildcards

        if key not in data:
            return

        if not remaining:
            del data[key]
        elif isinstance(data[key], dict):
            self._remove_value_at_path(data[key], remaining)
        elif isinstance(data[key], list):
            for item in data[key]:
                if isinstance(item, dict):
                    self._remove_value_at_path(item, remaining)

    def extract_secrets(self, config: dict) -> tuple[dict, dict]:
        """
        Extract sensitive fields from config.

        Supports both full names (token, password, email) and aliases (tk, pw, em).

        Returns:
            Tuple of (clean_config, secrets_dict)
            - clean_config: Config with sensitive fields removed
            - secrets_dict: Only the sensitive fields
        """
        import copy

        clean_config = copy.deepcopy(config)
        secrets = {}

        # Field mappings: full_name -> alias
        provider_fields = [
            ("token", "tk"),
            ("password", "pw"),
            ("email", "em"),
        ]
        qb_fields = [
            ("qb_username", "qus"),
            ("qb_password", "qpw"),
        ]

        def extract_provider_secrets(provider: dict, provider_key: str, index: int | None = None):
            """Extract secrets from a single provider config."""
            provider_secrets = {}

            for full_name, alias in provider_fields:
                # Check both full name and alias
                if full_name in provider and provider[full_name]:
                    provider_secrets[full_name] = provider[full_name]
                    del provider[full_name]
                elif alias in provider and provider[alias]:
                    provider_secrets[alias] = provider[alias]
                    del provider[alias]

            # Handle nested qbittorrent_config (both full name and alias)
            qb_key = "qbittorrent_config" if "qbittorrent_config" in provider else "qbc" if "qbc" in provider else None
            if qb_key and provider[qb_key]:
                qb = provider[qb_key]
                qb_secrets = {}
                for full_name, alias in qb_fields:
                    if full_name in qb and qb[full_name]:
                        qb_secrets[full_name] = qb[full_name]
                        del qb[full_name]
                    elif alias in qb and qb[alias]:
                        qb_secrets[alias] = qb[alias]
                        del qb[alias]
                if qb_secrets:
                    provider_secrets[qb_key] = qb_secrets

            if provider_secrets:
                if index is not None:
                    provider_secrets["_index"] = index

            return provider_secrets

        # Handle streaming_providers list (sps alias)
        for key in ["streaming_providers", "sps"]:
            if key in clean_config and isinstance(clean_config[key], list):
                providers_secrets = []
                for i, provider in enumerate(clean_config[key]):
                    if isinstance(provider, dict):
                        provider_secrets = extract_provider_secrets(provider, key, i)
                        if provider_secrets:
                            providers_secrets.append(provider_secrets)

                if providers_secrets:
                    secrets[key] = providers_secrets
                break

        # Handle legacy single streaming_provider (sp alias)
        for key in ["streaming_provider", "sp"]:
            if key in clean_config and clean_config[key] and isinstance(clean_config[key], dict):
                sp_secrets = extract_provider_secrets(clean_config[key], key)
                if sp_secrets:
                    secrets[key] = sp_secrets
                break

        # Handle external service configs (both full names and aliases)
        for key, alias, secret_field, alias_field in [
            ("mediaflow_config", "mfc", "api_password", "ap"),
            ("rpdb_config", "rpc", "api_key", "ak"),
            ("mdblist_config", "mdb", "api_key", "ak"),
        ]:
            config_key = key if key in clean_config else alias if alias in clean_config else None
            if config_key and clean_config[config_key]:
                cfg = clean_config[config_key]
                for field in [secret_field, alias_field]:
                    if field in cfg and cfg[field]:
                        secrets[config_key] = {field: cfg[field]}
                        del cfg[field]
                        break

        # Handle top-level api_password (ap alias)
        for key in ["api_password", "ap"]:
            if key in clean_config and clean_config[key]:
                secrets[key] = clean_config[key]
                del clean_config[key]
                break

        return clean_config, secrets

    def merge_secrets(self, config: dict, secrets: dict) -> dict:
        """
        Merge secrets back into config.

        Supports both full names and aliases.

        Args:
            config: Clean config without sensitive fields
            secrets: Decrypted secrets dict

        Returns:
            Complete config with secrets restored
        """
        import copy

        full_config = copy.deepcopy(config)

        def merge_provider_secrets(provider: dict, provider_secrets: dict, qb_key: str = "qbittorrent_config"):
            """Merge secrets into a single provider config."""
            for field in ["token", "password", "email", "tk", "pw", "em"]:
                if field in provider_secrets:
                    provider[field] = provider_secrets[field]

            for qb_config_key in ["qbittorrent_config", "qbc"]:
                if qb_config_key in provider_secrets:
                    actual_qb_key = "qbc" if "qbc" in provider else "qbittorrent_config"
                    if actual_qb_key not in provider:
                        provider[actual_qb_key] = {}
                    for field in ["qb_username", "qb_password", "qus", "qpw"]:
                        if field in provider_secrets[qb_config_key]:
                            provider[actual_qb_key][field] = provider_secrets[qb_config_key][field]

        # Merge streaming_providers secrets (sps alias)
        for key in ["streaming_providers", "sps"]:
            if key in secrets:
                # Find the matching key in full_config
                config_key = (
                    "sps"
                    if "sps" in full_config
                    else "streaming_providers"
                    if "streaming_providers" in full_config
                    else key
                )
                if config_key not in full_config:
                    full_config[config_key] = []

                for sp_secret in secrets[key]:
                    idx = sp_secret.get("_index", 0)
                    if idx < len(full_config[config_key]):
                        merge_provider_secrets(full_config[config_key][idx], sp_secret)
                break

        # Merge legacy streaming_provider secrets (sp alias)
        for key in ["streaming_provider", "sp"]:
            if key in secrets:
                config_key = (
                    "sp"
                    if "sp" in full_config
                    else "streaming_provider"
                    if "streaming_provider" in full_config
                    else key
                )
                if config_key not in full_config:
                    full_config[config_key] = {}
                merge_provider_secrets(full_config[config_key], secrets[key])
                break

        # Merge external service secrets
        for secret_key in ["mediaflow_config", "mfc"]:
            if secret_key in secrets:
                config_key = (
                    "mfc"
                    if "mfc" in full_config
                    else "mediaflow_config"
                    if "mediaflow_config" in full_config
                    else secret_key
                )
                if config_key not in full_config:
                    full_config[config_key] = {}
                for field in ["api_password", "ap"]:
                    if field in secrets[secret_key]:
                        full_config[config_key][field] = secrets[secret_key][field]
                break

        for secret_key in ["rpdb_config", "rpc"]:
            if secret_key in secrets:
                config_key = (
                    "rpc" if "rpc" in full_config else "rpdb_config" if "rpdb_config" in full_config else secret_key
                )
                if config_key not in full_config:
                    full_config[config_key] = {}
                for field in ["api_key", "ak"]:
                    if field in secrets[secret_key]:
                        full_config[config_key][field] = secrets[secret_key][field]
                break

        for secret_key in ["mdblist_config", "mdb"]:
            if secret_key in secrets:
                config_key = (
                    "mdb"
                    if "mdb" in full_config
                    else "mdblist_config"
                    if "mdblist_config" in full_config
                    else secret_key
                )
                if config_key not in full_config:
                    full_config[config_key] = {}
                for field in ["api_key", "ak"]:
                    if field in secrets[secret_key]:
                        full_config[config_key][field] = secrets[secret_key][field]
                break

        for key in ["api_password", "ap"]:
            if key in secrets:
                config_key = "ap" if "ap" in full_config else "api_password"
                full_config[config_key] = secrets[key]
                break

        return full_config

    def encrypt_secrets(self, secrets: dict) -> str | None:
        """
        Encrypt secrets dict to string for storage.

        Args:
            secrets: Dict of sensitive fields

        Returns:
            Encrypted string or None if no secrets
        """
        if not secrets:
            return None

        json_str = json.dumps(secrets)
        return self._encrypt(json_str)

    def decrypt_secrets(self, encrypted_secrets: str | None) -> dict:
        """
        Decrypt secrets string back to dict.

        Args:
            encrypted_secrets: Encrypted string from database

        Returns:
            Decrypted secrets dict
        """
        if not encrypted_secrets:
            return {}

        json_str = self._decrypt(encrypted_secrets)
        return json.loads(json_str)

    def mask_secrets_for_display(self, config: dict) -> dict:
        """
        Replace sensitive values with masked versions for display.
        Shows first 4 and last 4 characters with asterisks in between.

        Args:
            config: Full config including secrets

        Returns:
            Config with masked sensitive values
        """
        import copy

        masked = copy.deepcopy(config)

        def mask_value(value: str) -> str:
            """Return a consistent mask pattern for any secret value."""
            if not value:
                return ""
            return "••••••••"

        def mask_provider(provider: dict):
            """Mask secrets in a provider config (supports both full names and aliases)."""
            for field in ["token", "tk", "password", "pw"]:
                if field in provider and provider[field]:
                    provider[field] = mask_value(provider[field])
            # qbittorrent_config or qbc
            for qb_key in ["qbittorrent_config", "qbc"]:
                if qb_key in provider and provider[qb_key]:
                    qb = provider[qb_key]
                    for field in ["qb_password", "qpw"]:
                        if field in qb and qb[field]:
                            qb[field] = mask_value(qb[field])

        # Mask streaming_providers (sps alias)
        for key in ["streaming_providers", "sps"]:
            if key in masked and isinstance(masked[key], list):
                for provider in masked[key]:
                    mask_provider(provider)

        # Mask legacy streaming_provider (sp alias)
        for key in ["streaming_provider", "sp"]:
            if key in masked and masked[key]:
                mask_provider(masked[key])

        # Mask external services (supports both full names and aliases)
        for key in ["mediaflow_config", "mfc"]:
            if key in masked and masked[key]:
                for field in ["api_password", "ap"]:
                    if field in masked[key] and masked[key][field]:
                        masked[key][field] = mask_value(masked[key][field])

        for key in ["rpdb_config", "rpc"]:
            if key in masked and masked[key]:
                for field in ["api_key", "ak"]:
                    if field in masked[key] and masked[key][field]:
                        masked[key][field] = mask_value(masked[key][field])

        for key in ["mdblist_config", "mdb"]:
            if key in masked and masked[key]:
                for field in ["api_key", "ak"]:
                    if field in masked[key] and masked[key][field]:
                        masked[key][field] = mask_value(masked[key][field])

        for key in ["api_password", "ap"]:
            if key in masked and masked[key]:
                masked[key] = mask_value(masked[key])

        return masked

    def get_full_config(self, config: dict | None, encrypted_secrets: str | None) -> dict:
        """
        Convenience method to decrypt and merge secrets into config.

        This is the main entry point for getting a complete config with
        decrypted secrets, ready for building UserData.

        Args:
            config: Non-sensitive profile configuration (may be None)
            encrypted_secrets: Encrypted secrets string from database

        Returns:
            Complete config dict with decrypted secrets merged in
        """
        if not config:
            config = {}
        else:
            config = config.copy()  # Don't mutate original

        secrets = self.decrypt_secrets(encrypted_secrets)
        return self.merge_secrets(config, secrets)


# Singleton instance
profile_crypto = ProfileCrypto()
