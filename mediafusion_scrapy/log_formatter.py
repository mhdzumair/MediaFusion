import logging
import re
from collections.abc import Mapping

from scrapy.logformatter import LogFormatter


class CompactDropItemLogFormatter(LogFormatter):
    """Avoid logging huge item payloads for dropped items."""

    _MAX_FIELD_LEN = 160
    _MAX_REASON_LEN = 220
    _WHITESPACE_RE = re.compile(r"\s+")

    @classmethod
    def _compact_text(cls, value, *, max_len: int) -> str:
        text = cls._WHITESPACE_RE.sub(" ", str(value)).strip()
        return text if len(text) <= max_len else f"{text[:max_len]}..."

    @staticmethod
    def _extract_summary(item) -> str:
        if not isinstance(item, Mapping):
            return ""

        keys = (
            "title",
            "torrent_title",
            "torrent_name",
            "source",
            "uploader",
            "webpage_url",
            "torrent_link",
            "magnet_link",
            "info_hash",
        )
        chunks = []
        for key in keys:
            value = item.get(key)
            if not value:
                continue
            text = CompactDropItemLogFormatter._compact_text(
                value,
                max_len=CompactDropItemLogFormatter._MAX_FIELD_LEN,
            )
            chunks.append(f"{key}={text}")

        return ", ".join(chunks)

    @classmethod
    def _extract_reason(cls, exception) -> str:
        if exception is None:
            return "DropItem"
        text = cls._WHITESPACE_RE.sub(" ", str(exception)).strip()
        if "{" in text and "}" in text:
            prefix = text.split("{", 1)[0].rstrip(": ")
            text = f"{prefix}: {{...}}" if prefix else "{...}"
        return cls._compact_text(text, max_len=cls._MAX_REASON_LEN)

    def dropped(self, item, exception, response, spider):
        summary = self._extract_summary(item)
        reason = self._extract_reason(exception)
        summary_suffix = f" [{summary}]" if summary else ""
        return {
            "level": logging.WARNING,
            "msg": "Dropped (%(spider)s): %(reason)s%(summary)s",
            "args": {
                "spider": getattr(spider, "name", "unknown"),
                "reason": reason,
                "summary": summary_suffix,
            },
        }
