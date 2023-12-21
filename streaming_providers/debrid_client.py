import time
import traceback

import requests
from requests import RequestException, JSONDecodeError

from streaming_providers.exceptions import ProviderException


class DebridClient:
    def __init__(self, token=None):
        self.token = token
        self.headers = {}
        self.initialize_headers()

    def __del__(self):
        if self.token:
            self.disable_access_token()

    def _make_request(
        self,
        method: str,
        url: str,
        data=None,
        params=None,
        is_return_none=False,
        is_expected_to_fail=False,
    ) -> dict:
        response = self._perform_request(method, url, data, params)
        self._handle_errors(response, is_expected_to_fail)
        return self._parse_response(response, is_return_none)

    def _perform_request(self, method, url, data, params):
        return requests.request(
            method, url, params=params, data=data, headers=self.headers
        )

    def _handle_errors(self, response, is_expected_to_fail):
        try:
            response.raise_for_status()
        except RequestException as error:
            if is_expected_to_fail:
                return
            self._handle_service_specific_errors(error)

            if error.response.status_code == 401:
                raise ProviderException("Invalid token", "invalid_token.mp4")
            formatted_traceback = "".join(traceback.format_exception(error))
            raise ProviderException(
                f"API Error: {formatted_traceback}", "api_error.mp4"
            )

    def _handle_service_specific_errors(self, error):
        """
        Service specific errors on api requests.
        """
        raise NotImplementedError

    @staticmethod
    def _parse_response(response, is_return_none):
        if is_return_none:
            return {}
        try:
            return response.json()
        except JSONDecodeError as error:
            raise ProviderException(
                f"Failed to parse response: {error}", "api_error.mp4"
            )

    def initialize_headers(self):
        raise NotImplementedError

    def disable_access_token(self):
        raise NotImplementedError

    def wait_for_status(
        self, torrent_id: str, target_status: str, max_retries: int, retry_interval: int
    ):
        """Wait for the torrent to reach a particular status."""
        retries = 0
        while retries < max_retries:
            torrent_info = self.get_torrent_info(torrent_id)
            print(torrent_info)
            if torrent_info["status"] == target_status:
                return torrent_info
            time.sleep(retry_interval)
            retries += 1
        raise ProviderException(
            f"Torrent did not reach {target_status} status.",
            "torrent_not_downloaded.mp4",
        )

    def get_torrent_info(self, torrent_id):
        raise NotImplementedError
