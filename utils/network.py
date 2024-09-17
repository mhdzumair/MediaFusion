import asyncio
import logging
from typing import Callable, AsyncGenerator, Any, Tuple
from urllib import parse

import httpx
from fastapi.requests import Request

from db.schemas import UserData
from utils import crypto
from utils.runtime_const import PRIVATE_CIDR, REDIS_ASYNC_CLIENT


class CircuitBreakerOpenException(Exception):
    """Custom exception to indicate the circuit breaker is open."""

    pass


class CircuitBreaker:
    """
    A circuit breaker implementation that can be used to wrap around network calls
    to prevent cascading failures. It has three states: CLOSED, OPEN, and HALF-OPEN.
    """

    def __init__(
        self, failure_threshold: int, recovery_timeout: int, half_open_attempts: int
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_attempts = half_open_attempts
        self.state = "CLOSED"
        self.failures = 0
        self.last_failure_time = None

    async def call(self, func: Callable, item: Any, *args, **kwargs) -> Tuple[Any, Any]:
        if (
            self.state == "OPEN"
            and (asyncio.get_event_loop().time() - self.last_failure_time)
            < self.recovery_timeout
        ):
            return item, CircuitBreakerOpenException(
                "Circuit breaker is open; calls are temporarily halted"
            )
        elif self.state == "OPEN":
            self.state = "HALF-OPEN"
            self.failures = 0  # Reset failures in half-open state

        try:
            result = await func(item, *args, **kwargs)
            if self.state == "HALF-OPEN":
                self.failures += 1
                if self.failures >= self.half_open_attempts:
                    self.state = "CLOSED"
                    self.failures = 0
            return item, result
        except Exception as e:
            self.failures += 1
            self.last_failure_time = asyncio.get_event_loop().time()
            if self.failures >= self.failure_threshold:
                self.state = "OPEN"
            return item, e


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
                                f"Unexpected error during batch processing for item {item}: {result}"
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
        async with httpx.AsyncClient() as client:
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


async def get_mediaflow_proxy_public_ip(
    mediaflow_proxy_url: str, api_password
) -> str | None:
    """
    Get the public IP address of the MediaFlow proxy server.
    """
    cache_key = crypto.get_text_hash(
        f"{mediaflow_proxy_url}:{api_password}", full_hash=True
    )
    if public_ip := await REDIS_ASYNC_CLIENT.getex(cache_key, ex=300):
        return public_ip

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                mediaflow_proxy_url + "/proxy/ip",
                params={"api_password": api_password},
                timeout=10,
            )
            response.raise_for_status()
            public_ip = response.json().get("ip")
            if public_ip:
                await REDIS_ASYNC_CLIENT.set(cache_key, public_ip, ex=300)
                return public_ip
    except httpx.HTTPStatusError as e:
        logging.error(f"HTTP error occurred: {e}")
    except httpx.RequestError as e:
        logging.error(f"Request error occurred: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
    return None


async def get_user_public_ip(
    request: Request, user_data: UserData | None = None
) -> str | None:
    # Check if user has mediaflow config
    if (
        user_data
        and user_data.mediaflow_config
        and user_data.mediaflow_config.proxy_debrid_streams
    ):
        public_ip = await get_mediaflow_proxy_public_ip(
            user_data.mediaflow_config.proxy_url,
            user_data.mediaflow_config.api_password,
        )
        if public_ip:
            return public_ip
    # Get the user's public IP address
    user_ip = get_client_ip(request)
    # check if the user's IP address is a private IP address
    if PRIVATE_CIDR.match(user_ip):
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
    # Encode the query parameters
    encoded_params = parse.urlencode(query_params, quote_via=parse.quote)

    # Construct the full URL
    base_url = parse.urljoin(mediaflow_proxy_url, endpoint)
    return f"{base_url}?{encoded_params}"
