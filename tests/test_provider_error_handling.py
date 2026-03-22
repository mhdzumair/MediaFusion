from types import SimpleNamespace
from datetime import UTC, date, datetime

import aiohttp
import pytest

from streaming_providers.alldebrid import utils as alldebrid_utils
from streaming_providers.debrid_client import DebridClient
from streaming_providers.easydebrid import utils as easydebrid_utils
from streaming_providers.exceptions import ProviderException
from streaming_providers.pikpak import utils as pikpak_utils
from streaming_providers.premiumize import utils as premiumize_utils
from streaming_providers.realdebrid.client import RealDebrid
from streaming_providers.realdebrid import utils as realdebrid_utils
from streaming_providers.parser import _get_episode_date
from streaming_providers.torbox.client import Torbox


class _DummyDebridClient(DebridClient):
    debrid_proxy_provider_id = "_dummy"

    async def _handle_service_specific_errors(self, error_data: dict, status_code: int):
        return

    async def initialize_headers(self):
        self.headers = {}

    async def disable_access_token(self):
        return

    async def get_torrent_info(self, torrent_id: str) -> dict:
        return {}


class _DummyErrorResponse:
    headers = {"Content-Type": "text/html"}

    def raise_for_status(self):
        raise aiohttp.ClientResponseError(
            request_info=None,
            history=(),
            status=429,
            message="Too Many Requests",
        )

    async def text(self):
        return "<html>Too Many Requests</html>"

    async def json(self):
        return {}


class _DummyServerErrorResponse:
    headers = {"Content-Type": "text/html"}

    def raise_for_status(self):
        raise aiohttp.ClientResponseError(
            request_info=None,
            history=(),
            status=500,
            message="Internal Server Error",
        )

    async def text(self):
        return "<html>Internal Server Error</html>"

    async def json(self):
        return {}


@pytest.mark.asyncio
async def test_debrid_client_maps_429_to_too_many_requests():
    client = _DummyDebridClient(token=None)
    with pytest.raises(ProviderException) as exc:
        await client._check_response_status(_DummyErrorResponse(), is_expected_to_fail=False)
    assert exc.value.video_file_name == "too_many_requests.mp4"


@pytest.mark.asyncio
async def test_debrid_client_maps_500_to_service_down():
    client = _DummyDebridClient(token=None)
    with pytest.raises(ProviderException) as exc:
        await client._check_response_status(_DummyServerErrorResponse(), is_expected_to_fail=False)
    assert exc.value.video_file_name == "debrid_service_down_error.mp4"


@pytest.mark.asyncio
async def test_realdebrid_add_new_torrent_retries_unknown_resource(monkeypatch):
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    monkeypatch.setattr(realdebrid_utils.asyncio, "sleep", _fake_sleep)

    class FakeRDClient:
        def __init__(self):
            self.info_calls = 0

        async def get_active_torrents(self):
            return {"limit": 100, "nb": 0, "list": []}

        async def add_magnet_link(self, magnet_link):
            return {"id": "JAJEBC5BIRRIU"}

        async def get_torrent_info(self, torrent_id):
            self.info_calls += 1
            if self.info_calls < 3:
                raise ProviderException("API Error {'error': 'unknown_ressource', 'error_code': 7}", "api_error.mp4")
            return {"id": torrent_id}

    stream = SimpleNamespace(torrent_file=None)
    client = FakeRDClient()

    result = await realdebrid_utils.add_new_torrent(client, "magnet:?xt=urn:btih:abc", "abc", stream)
    assert result["id"] == "JAJEBC5BIRRIU"
    assert client.info_calls == 3
    assert sleep_calls == [0.5, 1.0]


@pytest.mark.asyncio
async def test_realdebrid_add_new_torrent_readds_after_persistent_unknown_resource(monkeypatch):
    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    monkeypatch.setattr(realdebrid_utils.asyncio, "sleep", _fake_sleep)

    class FakeRDClient:
        def __init__(self):
            self.add_calls = 0
            self.info_calls_by_id: dict[str, int] = {}

        async def get_active_torrents(self):
            return {"limit": 100, "nb": 0, "list": []}

        async def add_magnet_link(self, magnet_link):
            self.add_calls += 1
            return {"id": f"id-{self.add_calls}"}

        async def get_torrent_info(self, torrent_id):
            self.info_calls_by_id[torrent_id] = self.info_calls_by_id.get(torrent_id, 0) + 1
            if torrent_id == "id-1":
                raise ProviderException("API Error {'error': 'unknown_ressource', 'error_code': 7}", "api_error.mp4")
            return {"id": torrent_id}

    stream = SimpleNamespace(torrent_file=None)
    client = FakeRDClient()

    result = await realdebrid_utils.add_new_torrent(client, "magnet:?xt=urn:btih:abc", "abc", stream)
    assert result["id"] == "id-2"
    assert client.add_calls == 2
    assert client.info_calls_by_id["id-1"] == 3

    # 3 unknown-resource checks on first id + one pause before re-add.
    assert sleep_calls == [0.5, 1.0, 0.5]


@pytest.mark.asyncio
async def test_realdebrid_add_new_torrent_handles_none_create_response():
    class FakeRDClient:
        async def get_active_torrents(self):
            return {"limit": 100, "nb": 0, "list": []}

        async def add_magnet_link(self, magnet_link):
            return None

    stream = SimpleNamespace(torrent_file=None)

    with pytest.raises(ProviderException) as exc:
        await realdebrid_utils.add_new_torrent(FakeRDClient(), "magnet:?xt=urn:btih:abc", "abc", stream)
    assert exc.value.video_file_name == "transfer_error.mp4"


@pytest.mark.asyncio
async def test_realdebrid_maps_hoster_not_free_error():
    client = RealDebrid(token=None)

    async def fake_make_request(*args, **kwargs):
        return {"error": "hoster_not_free", "error_code": 20}

    client._make_request = fake_make_request

    with pytest.raises(ProviderException) as exc:
        await client.create_download_link("https://example.com/file")
    assert exc.value.video_file_name == "need_premium.mp4"


@pytest.mark.asyncio
async def test_realdebrid_maps_fair_usage_limit_error():
    client = RealDebrid(token=None)

    async def fake_make_request(*args, **kwargs):
        return {"error": "fair_usage_limit", "error_code": 36}

    client._make_request = fake_make_request

    with pytest.raises(ProviderException) as exc:
        await client.create_download_link("https://example.com/file")
    assert exc.value.video_file_name == "exceed_remote_traffic_limit.mp4"


@pytest.mark.asyncio
async def test_realdebrid_maps_unknown_resource_error_to_torrent_not_downloaded():
    client = RealDebrid(token=None)

    with pytest.raises(ProviderException) as exc:
        await client._handle_service_specific_errors({"error": "unknown_ressource", "error_code": 7}, 404)
    assert exc.value.video_file_name == "torrent_not_downloaded.mp4"


@pytest.mark.asyncio
async def test_premiumize_add_new_torrent_falls_back_when_cache_check_is_down():
    class FakePMClient:
        async def get_torrent_instant_availability(self, torrent_hashes):
            raise ProviderException("Debrid service is down.", "debrid_service_down_error.mp4")

        async def get_folder_list(self):
            return {"content": [{"name": "abc123", "id": "folder-1"}]}

        async def add_magnet_link(self, magnet_link, folder_id):
            return {"id": "transfer-1"}

    stream = SimpleNamespace(torrent_file=None)

    result = await premiumize_utils.add_new_torrent(
        FakePMClient(),
        "magnet:?xt=urn:btih:abc123",
        stream,
        "abc123",
    )
    assert result["id"] == "transfer-1"


@pytest.mark.asyncio
async def test_alldebrid_add_new_torrent_handles_item_error():
    class FakeADClient:
        async def add_magnet_link(self, magnet_link):
            return {
                "status": "success",
                "data": {
                    "magnets": [
                        {
                            "error": {"code": "MAGNET_MUST_BE_PREMIUM", "message": "premium required"},
                        }
                    ]
                },
            }

    stream = SimpleNamespace(torrent_file=None, name="test")

    with pytest.raises(ProviderException) as exc:
        await alldebrid_utils.add_new_torrent(FakeADClient(), "magnet:?xt=urn:btih:def", stream)
    assert exc.value.video_file_name == "need_premium.mp4"


@pytest.mark.asyncio
async def test_easydebrid_maps_html_rate_limit_response(monkeypatch):
    class FakeEasyDebrid:
        def __init__(self, token: str | None = None, user_ip: str | None = None):
            return

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return

        async def create_download_link(self, magnet):
            return "<html><title>Too Many Requests</title></html>"

    monkeypatch.setattr(easydebrid_utils, "EasyDebrid", FakeEasyDebrid)

    with pytest.raises(ProviderException) as exc:
        await easydebrid_utils.get_video_url_from_easydebrid(
            magnet_link="magnet:?xt=urn:btih:xyz",
            streaming_provider=SimpleNamespace(token="token"),
            filename="file.mkv",
            user_ip="127.0.0.1",
            stream=SimpleNamespace(),
        )
    assert exc.value.video_file_name == "too_many_requests.mp4"


def test_pikpak_review_error_mapping():
    with pytest.raises(ProviderException) as exc:
        pikpak_utils._raise_pikpak_provider_exception(Exception('meta:{key:"result" value:"review"} result:review'))
    assert exc.value.video_file_name == "invalid_credentials.mp4"


@pytest.mark.asyncio
async def test_torbox_maps_plan_restricted_queue_error_to_need_premium():
    client = Torbox(token="token")

    async def fake_make_request(*args, **kwargs):
        return {
            "success": False,
            "error": "PLAN_RESTRICTED_FEATURE",
            "detail": "API feature not available on your plan. Please upgrade to a paid plan to access the API.",
            "data": None,
        }

    client._make_request = fake_make_request

    with pytest.raises(ProviderException) as exc:
        await client.get_queued_torrents()

    assert exc.value.video_file_name == "need_premium.mp4"


def test_episode_date_helper_supports_legacy_and_v5_fields():
    legacy_episode = SimpleNamespace(released=datetime(2026, 1, 15, tzinfo=UTC))
    v5_episode = SimpleNamespace(air_date=date(2026, 1, 16))
    missing_date_episode = SimpleNamespace(title="Episode")

    assert _get_episode_date(legacy_episode) == "2026-01-15"
    assert _get_episode_date(v5_episode) == "2026-01-16"
    assert _get_episode_date(missing_date_episode) is None
