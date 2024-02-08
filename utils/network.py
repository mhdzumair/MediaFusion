import asyncio
import logging
from typing import Callable

import httpx


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

    async def call(self, func: Callable, *args, **kwargs):
        if (
            self.state == "OPEN"
            and (asyncio.get_event_loop().time() - self.last_failure_time)
            < self.recovery_timeout
        ):
            raise CircuitBreakerOpenException(
                "Circuit breaker is open; calls are temporarily halted"
            )
        elif self.state == "OPEN":
            self.state = "HALF-OPEN"
            self.failures = 0  # Reset failures in half-open state

        try:
            result = await func(*args, **kwargs)
            if self.state == "HALF-OPEN":
                self.failures += 1
                if self.failures < self.half_open_attempts:
                    return result
                else:
                    self.state = "CLOSED"
                    self.failures = 0
        except Exception as e:
            self.failures += 1
            self.last_failure_time = asyncio.get_event_loop().time()
            if self.failures >= self.failure_threshold:
                self.state = "OPEN"
            raise e  # Reraise the exception to handle it outside

        return result


async def batch_process_with_circuit_breaker(
    process_func: Callable,
    data: list,
    batch_size: int,
    rate_limit_delay: int,
    cb: CircuitBreaker,
    *args,
    **kwargs,
):
    """
    Process data in batches using the circuit breaker pattern.
    """
    results = []
    for i in range(0, len(data), batch_size):
        batch = data[i : i + batch_size]
        while batch:  # Continue until all items in the batch are processed
            try:
                batch_results = await asyncio.gather(
                    *(cb.call(process_func, item, *args, **kwargs) for item in batch),
                    return_exceptions=True,  # Collect exceptions instead of raising
                )
                # Separate results and exceptions
                successful_results, retry_batch = [], []
                for item, result in zip(batch, batch_results):
                    if isinstance(
                        result, (CircuitBreakerOpenException, httpx.HTTPError)
                    ):
                        retry_batch.append(item)
                    elif isinstance(result, Exception):
                        logging.error(
                            f"Unexpected error during batch processing: {result}, {result.__class__.__name__}"
                        )
                    else:
                        successful_results.append(result)

                if retry_batch:
                    logging.info(
                        f"Retrying {len(retry_batch)} items due to circuit breaker."
                    )
                    batch = retry_batch  # Prepare to retry only the failed items
                    await asyncio.sleep(
                        cb.recovery_timeout
                    )  # Wait for the breaker to potentially close
                else:
                    # Extend results with successful results
                    results.extend(successful_results)
                    break  # Break the loop when all items are processed successfully
            finally:
                await asyncio.sleep(rate_limit_delay)  # Always respect the rate limit

    return results
