from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable, Iterable

import botocore


RETRYABLE_CODES = {
    "Throttling",
    "ThrottlingException",
    "RequestLimitExceeded",
    "TooManyRequestsException",
    "TooManyUpdates",
    "LimitExceededException",
}


async def retry_aws(call: Callable[[], Awaitable[None]], *, attempts: int = 6, base: float = 0.5, jitter: float = 0.25) -> None:
    """Retry an async callable that performs an AWS SDK call.

    Exponential backoff with jitter on known throttling/limit error codes.
    """
    exc: Exception | None = None
    for i in range(attempts):
        try:
            await call()
            return
        except botocore.exceptions.ClientError as e:  # type: ignore[attr-defined]
            code = e.response.get("Error", {}).get("Code") if getattr(e, "response", None) else None
            if code in RETRYABLE_CODES and i < attempts - 1:
                delay = base * (2**i) + random.uniform(0, jitter)
                await asyncio.sleep(delay)
                exc = e
                continue
            raise
        except Exception as e:
            exc = e
            if i < attempts - 1:
                delay = base * (2**i) + random.uniform(0, jitter)
                await asyncio.sleep(delay)
                continue
            raise
    if exc:
        raise exc
