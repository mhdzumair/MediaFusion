import pytest

from reference.routers.streaming import playback


class _FakeRedisNoPipeline:
    def __init__(self):
        self.values: dict[str, bytes] = {}

    def pipeline(self, transaction: bool = False):
        return None

    async def getex(self, key: str, ex: int):
        return self.values.get(key)

    async def set(self, key: str, value: bytes, ex: int):
        self.values[key] = value
        return True


@pytest.mark.asyncio
async def test_get_cached_stream_payload_falls_back_when_pipeline_unavailable(monkeypatch):
    fake_redis = _FakeRedisNoPipeline()
    fake_redis.values["cache:key"] = b"https://video.example/stream.mkv"
    fake_redis.values["cache:key:filename"] = b"stream.mkv"
    monkeypatch.setattr(playback, "REDIS_ASYNC_CLIENT", fake_redis)

    cached_url, cached_filename = await playback.get_cached_stream_payload("cache:key", include_filename=True)
    assert cached_url == "https://video.example/stream.mkv"
    assert cached_filename == "stream.mkv"


@pytest.mark.asyncio
async def test_cache_stream_url_falls_back_when_pipeline_unavailable(monkeypatch):
    fake_redis = _FakeRedisNoPipeline()
    monkeypatch.setattr(playback, "REDIS_ASYNC_CLIENT", fake_redis)

    await playback.cache_stream_url("cache:key", "https://video.example/stream.mkv", "stream.mkv")

    assert fake_redis.values["cache:key"] == b"https://video.example/stream.mkv"
    assert fake_redis.values["cache:key:filename"] == b"stream.mkv"
