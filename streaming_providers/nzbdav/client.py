"""NzbDAV client - reuses SABnzbd client since NzbDAV exposes a SABnzbd-compatible API."""

from streaming_providers.sabnzbd.client import SABnzbd as NzbDAVClient

__all__ = ["NzbDAVClient"]
