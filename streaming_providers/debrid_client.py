import asyncio
import traceback
from typing import Optional

import httpx

from streaming_providers.exceptions import ProviderException


class DebridClient:
    def __init__(self, token: Optional[str] = None):
        self.token = token
        self.headers = {}
        self.client = httpx.AsyncClient(timeout=18.0)  # Stremio timeout is 20s

    async def __aenter__(self):
        await self.initialize_headers()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.token:
            try:
                await self.disable_access_token()
            except ProviderException:
                pass
        await self.client.aclose()

    async def _make_request(
        self,
        method: str,
        url: str,
        data: Optional[dict | str] = None,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        is_return_none: bool = False,
        is_expected_to_fail: bool = False,
    ) -> dict | list:
        try:
            response = await self.client.request(
                method, url, data=data, json=json, params=params, headers=self.headers
            )
            response.raise_for_status()
            return await self._parse_response(response, is_return_none)
        except httpx.RequestError as error:
            await self._handle_request_error(error, is_expected_to_fail)
        except httpx.HTTPStatusError as error:
            await self._handle_http_error(error, is_expected_to_fail)
        except Exception as error:
            await self._handle_request_error(error, is_expected_to_fail)

    @staticmethod
    async def _handle_request_error(error: Exception, is_expected_to_fail: bool):
        if isinstance(error, httpx.TimeoutException):
            raise ProviderException("Request timed out.", "torrent_not_downloaded.mp4")
        elif isinstance(error, httpx.NetworkError):
            raise ProviderException(
                "Failed to connect to Debrid service.", "debrid_service_down_error.mp4"
            )
        elif not is_expected_to_fail:
            raise ProviderException(f"Request error: {str(error)}", "api_error.mp4")

    async def _handle_http_error(
        self, error: httpx.HTTPStatusError, is_expected_to_fail: bool
    ):
        if error.response.status_code in [502, 503, 504]:
            raise ProviderException(
                "Debrid service is down.", "debrid_service_down_error.mp4"
            )

        if is_expected_to_fail:
            return

        await self._handle_service_specific_errors(error)

        if error.response.status_code == 401:
            raise ProviderException("Invalid token", "invalid_token.mp4")

        formatted_traceback = "".join(traceback.format_exception(error))
        raise ProviderException(
            f"API Error {error.response.text} \n{formatted_traceback}",
            "api_error.mp4",
        )

    async def _handle_service_specific_errors(self, error: httpx.HTTPStatusError):
        """
        Service specific errors on api requests.
        """
        raise NotImplementedError

    @staticmethod
    async def _parse_response(response: httpx.Response, is_return_none: bool):
        if is_return_none:
            return {}
        try:
            return response.json()
        except ValueError as error:
            raise ProviderException(
                f"Failed to parse response error: {error}. \nresponse: {response.text}",
                "api_error.mp4",
            )

    async def initialize_headers(self):
        raise NotImplementedError

    async def disable_access_token(self):
        raise NotImplementedError

    async def wait_for_status(
        self,
        torrent_id: str,
        target_status: str | int,
        max_retries: int,
        retry_interval: int,
    ):
        """Wait for the torrent to reach a particular status."""
        for _ in range(max_retries):
            torrent_info = await self.get_torrent_info(torrent_id)
            if torrent_info["status"] == target_status:
                return torrent_info
            await asyncio.sleep(retry_interval)
        raise ProviderException(
            f"Torrent did not reach {target_status} status.",
            "torrent_not_downloaded.mp4",
        )

    async def get_torrent_info(self, torrent_id):
        raise NotImplementedError
