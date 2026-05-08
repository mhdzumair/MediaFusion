"""NzbDAV client - reuses SABnzbd client since NzbDAV exposes a SABnzbd-compatible API."""

from workers.providers.sabnzbd.client import SABnzbd as NzbDAVClient

__all__ = ["NzbDAVClient"]
