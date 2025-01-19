import asyncio
import logging
from ipaddress import ip_address
from typing import Callable, AsyncGenerator, Any, Tuple, Dict
from urllib import parse
from urllib.parse import urlencode, urlparse

import httpx
from fastapi.requests import Request

from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import UserData
from utils import crypto


class CircuitBreakerOpenException(Exception):
    """Custom exception to indicate the circuit breaker is open."""

    pass


class CircuitBreaker:
    """
    A specialized circuit breaker implementation optimized for web scraping scenarios.
    It implements a more gradual recovery mechanism to handle intermittent failures
    and ensure stable recovery.

    States:
    - CLOSED: Normal operation, all requests allowed
    - OPEN: Failure threshold exceeded, no requests allowed
    - HALF-OPEN: Testing recovery with controlled number of requests
    """

    def __init__(
        self,
        failure_threshold: int,  # Number of failures before opening circuit
        recovery_timeout: int,  # Time in seconds before attempting recovery
        half_open_attempts: int,  # Number of successful attempts needed for recovery
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_attempts = half_open_attempts
        self.state = "CLOSED"
        self.failures = 0
        self.successful_attempts = 0  # Track successful attempts in HALF-OPEN
        self.last_failure_time = None

    def is_closed(self) -> bool:
        """
        Check if requests should be allowed through.
        In HALF-OPEN state, allows controlled testing of the service.
        """
        current_time = asyncio.get_event_loop().time()

        if self.state == "CLOSED":
            return True
        elif self.state == "HALF-OPEN":
            return True
        elif (
            self.state == "OPEN"
            and self.last_failure_time
            and (current_time - self.last_failure_time) >= self.recovery_timeout
        ):
            self.state = "HALF-OPEN"
            self.failures = 0
            self.successful_attempts = (
                0  # Reset success counter when entering HALF-OPEN
            )
            return True

        return False

    def reset(self):
        """Reset the circuit breaker to its initial state"""
        self.state = "CLOSED"
        self.failures = 0
        self.successful_attempts = 0
        self.last_failure_time = None

    def record_failure(self):
        """
        Record a failure and update circuit breaker state.
        In HALF-OPEN, failures don't immediately trigger OPEN state
        to account for intermittent failures during recovery.
        """
        self.failures += 1
        self.last_failure_time = asyncio.get_event_loop().time()
        self.successful_attempts = 0  # Reset success counter on any failure

        if self.failures >= self.failure_threshold:
            self.state = "OPEN"

    def record_success(self):
        """
        Record a success and update circuit breaker state.
        In HALF-OPEN, requires multiple successive successes to close,
        ensuring stable recovery.
        """
        if self.state == "HALF-OPEN":
            self.successful_attempts += 1
            if self.successful_attempts >= self.half_open_attempts:
                self.reset()  # Only reset after proving stability
        elif self.state == "CLOSED":
            self.failures = 0
            self.successful_attempts = 0

    async def call(self, func: Callable, item: Any, *args, **kwargs) -> Tuple[Any, Any]:
        """
        Execute the given function with circuit breaker protection.
        Returns a tuple of (item, result/exception).
        """
        if not self.is_closed():
            return item, CircuitBreakerOpenException(
                f"Circuit breaker is OPEN. Failures: {self.failures}, "
                f"Last failure: {self.last_failure_time}"
            )

        try:
            result = await func(item, *args, **kwargs)
            self.record_success()
            return item, result
        except Exception as e:
            self.record_failure()
            return item, e

    def get_status(self) -> Dict[str, Any]:
        """Get detailed current status of the circuit breaker"""
        return {
            "state": self.state,
            "failures": self.failures,
            "successful_attempts": self.successful_attempts,
            "last_failure_time": self.last_failure_time,
            "is_accepting_requests": self.is_closed(),
        }


async def batch_process_with_circuit_breaker(
    process_func: Callable,
    data: list,
    batch_size: int,
    rate_limit_delay: int,
    cb: CircuitBreaker,
    max_retries: int = 5,
    retry_exceptions: list[type[Exception]] = (),
    *args,
    **kwargs,
) -> AsyncGenerator[Any, None]:
    """
    Process data in batches using the circuit breaker pattern with a maximum number of retries.
    Yields results as they become available.
    """
    total_retries = 0
    processed_count = 0

    # Ensure retry_exceptions is a tuple for the except clause
    retry_exceptions = tuple(retry_exceptions) + (CircuitBreakerOpenException,)

    for i in range(0, len(data), batch_size):
        batch = data[i : i + batch_size]
        batch_retries = 0

        while batch:  # Continue until all items in the batch are processed
            retry_batch = []  # Reset retry list for this iteration

            async with asyncio.TaskGroup() as tg:  # Using TaskGroup to manage tasks
                task_data = [
                    tg.create_task(cb.call(process_func, item, *args, **kwargs))
                    for item in batch
                ]

                # Process results as soon as they complete
                for task in asyncio.as_completed(task_data):
                    item, result = await task
                    if isinstance(result, Exception):
                        if isinstance(result, retry_exceptions):
                            retry_batch.append(item)
                            logging.info(
                                f"Retryable exception occurred for item {item}: {result}"
                            )
                        else:
                            logging.exception(
                                f"Unexpected error during batch processing {result}",
                                exc_info=result,
                            )
                    else:
                        processed_count += 1
                        yield result

            if retry_batch:
                if batch_retries >= max_retries:
                    logging.info(
                        f"Reached maximum number of retries ({max_retries}) for this batch."
                    )
                    break  # Move to the next batch
                else:
                    batch_retries += 1
                    total_retries += 1
                    logging.info(
                        f"Retrying {len(retry_batch)} items due to circuit breaker. Retry attempt {batch_retries}"
                    )
                    batch = retry_batch  # Retry only failed items
                    await asyncio.sleep(
                        cb.recovery_timeout
                    )  # Wait for breaker to close
            else:
                break  # Exit loop if all items in the batch have been processed successfully

            # Respect the rate limit
            await asyncio.sleep(rate_limit_delay)

    logging.info(f"Processed {processed_count} items out of {len(data)} total items.")


async def get_redirector_url(url: str, headers: dict) -> str | None:
    """
    Get the final URL after following all redirects.
    """
    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as client:
            response = await client.head(url, headers=headers, follow_redirects=True)
            return str(response.url)
    except httpx.HTTPError as e:
        return


def get_client_ip(request: Request) -> str | None:
    """
    Extract the client's real IP address from the request headers or fallback to the client host.
    """
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # In some cases, this header can contain multiple IPs
        # separated by commas.
        # The first one is the original client's IP.
        return x_forwarded_for.split(",")[0].strip()
    # Fallback to X-Real-IP if X-Forwarded-For is not available
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip
    return request.client.host if request.client else "127.0.0.1"


async def get_mediaflow_proxy_public_ip(mediaflow_config) -> str | None:
    """
    Get the public IP address of the MediaFlow proxy server.
    """
    if mediaflow_config.public_ip:
        return mediaflow_config.public_ip

    parsed_url = urlparse(mediaflow_config.proxy_url)
    if is_private_ip(parsed_url.netloc):
        # MediaFlow proxy URL is a private IP address
        return None

    cache_key = crypto.get_text_hash(
        f"{mediaflow_config.proxy_url}:{mediaflow_config.api_password}",
        full_hash=True,
    )
    if public_ip := await REDIS_ASYNC_CLIENT.getex(cache_key, ex=300):
        return public_ip.decode()

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                parse.urljoin(mediaflow_config.proxy_url, "/proxy/ip"),
                params={"api_password": mediaflow_config.api_password},
                timeout=10,
            )
            response.raise_for_status()
            public_ip = response.json().get("ip")
            if public_ip:
                await REDIS_ASYNC_CLIENT.set(cache_key, public_ip, ex=300)
                return public_ip
    except httpx.HTTPStatusError as e:
        logging.error(f"HTTP error occurred: {e}")
    except httpx.TimeoutException as e:
        logging.error(f"Request timed out: {e}")
    except httpx.RequestError as e:
        logging.error(f"Request error occurred: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
    raise Exception("Failed to get MediaFlow proxy public IP address.")


async def get_user_public_ip(
    request: Request, user_data: UserData | None = None
) -> str | None:
    # Check if user has mediaflow config
    if (
        user_data
        and user_data.mediaflow_config
        and user_data.mediaflow_config.proxy_debrid_streams
    ):
        public_ip = await get_mediaflow_proxy_public_ip(user_data.mediaflow_config)
        if public_ip:
            return public_ip
    # Get the user's public IP address
    user_ip = get_client_ip(request)
    # check if the user's IP address is a private IP address
    if is_private_ip(user_ip):
        # Use host public IP address.
        return None
    return user_ip


def get_request_namespace(request: Request) -> str:
    """
    Extract the namespace from the request URL.
    """
    host = request.url.hostname
    if "elfhosted.com" not in host:
        return "mediafusion"

    subdomain = host.split(".")[0]
    parts = subdomain.rsplit("-mediafusion")
    if len(parts) == 1:
        # public namespace
        return "mediafusion"

    namespace = f"tenant-{parts[0]}"
    return namespace


def get_user_data(request: Request, secret_str: str | None = None) -> UserData:
    return request.user


def encode_mediaflow_proxy_url(
    mediaflow_proxy_url: str,
    endpoint: str,
    destination_url: str | None = None,
    query_params: dict | None = None,
    request_headers: dict | None = None,
    response_headers: dict | None = None,
    encryption_api_password: str = None,
    expiration: int = None,
    ip: str = None,
) -> str:
    query_params = query_params or {}
    if destination_url is not None:
        query_params["d"] = destination_url

    # Add headers if provided
    if request_headers:
        query_params.update(
            {f"h_{key}": value for key, value in request_headers.items()}
        )
    if response_headers:
        query_params.update(
            {f"r_{key}": value for key, value in response_headers.items()}
        )

    if encryption_api_password:
        if "api_password" not in query_params:
            query_params["api_password"] = encryption_api_password
        encrypted_token = crypto.encrypt_data(
            encryption_api_password, query_params, expiration, ip
        )
        encoded_params = urlencode({"token": encrypted_token})
    else:
        encoded_params = urlencode(query_params)

    # Construct the full URL
    base_url = parse.urljoin(mediaflow_proxy_url, endpoint)
    return f"{base_url}?{encoded_params}"


def is_private_ip(ip_str: str) -> bool:
    try:
        ip = ip_address(ip_str)
        return ip.is_private
    except ValueError:
        return False
