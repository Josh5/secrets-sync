from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import botocore


logger = logging.getLogger(__name__)

RETRYABLE_CODES = {
    "Throttling",
    "ThrottlingException",
    "RequestLimitExceeded",
    "TooManyRequestsException",
    "TooManyUpdates",
    "LimitExceededException",
}


async def retry_aws(
    call: Callable[[], Awaitable[None]],
    *,
    attempts: int = 20,
    base: float = 10.0,
    increment: float = 3.0,
    max_delay: float = 30.0,
) -> None:
    """Retry an async callable that performs an AWS SDK call.

    Linear backoff (base + increment * attempt) on known throttling/limit error codes.
    """
    exc: Exception | None = None
    for i in range(attempts):
        try:
            await call()
            return
        except botocore.exceptions.ClientError as e:  # type: ignore[attr-defined]
            code = e.response.get("Error", {}).get("Code") if getattr(e, "response", None) else None
            if code in RETRYABLE_CODES and i < attempts - 1:
                delay = min(base + increment * i, max_delay)
                if delay >= max_delay:
                    logger.warning(
                        "AWS rate limit (%s) encountered; retrying in %.2fs (attempt %d/%d)",
                        code,
                        delay,
                        i + 2,
                        attempts,
                    )
                await asyncio.sleep(delay)
                exc = e
                continue
            raise
        except Exception as e:
            exc = e
            if i < attempts - 1:
                delay = min(base + increment * i, max_delay)
                if delay >= max_delay:
                    logger.warning(
                        "Retryable exception encountered; retrying in %.2fs (attempt %d/%d)",
                        delay,
                        i + 2,
                        attempts,
                    )
                await asyncio.sleep(delay)
                continue
            raise
    if exc:
        raise exc
