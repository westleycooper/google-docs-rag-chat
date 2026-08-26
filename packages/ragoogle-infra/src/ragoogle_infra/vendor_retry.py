"""Retry with exponential backoff and full jitter for transient vendor errors.

Shared by both Voyage adapters. Rate limits are an expected condition on any
real workload rather than an exceptional one -- a free-tier Voyage key allows
three requests per minute -- so failing an entire ingestion run or a user's
question on the first 429 is the wrong behaviour.

Full jitter rather than a fixed schedule: several concurrent calls that trip the
limit together would otherwise retry in lockstep and trip it again, turning one
rate limit into a thundering herd.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

MAX_RETRIES = 6
BASE_DELAY_SECONDS = 30.0
MAX_DELAY_SECONDS = 120.0

#: The floor matters more than the ceiling here, and it is the non-obvious part.
#:
#: Vendor rate limits are usually a budget over a *window* -- Voyage's free tier
#: is 10,000 tokens per minute, not a request rate. Once a window's budget is
#: spent, every retry inside that window is guaranteed to fail: there is nothing
#: left to consume. Classic full jitter from zero therefore burns most of its
#: attempts on calls that cannot possibly succeed, and gives up while still
#: inside the window that rejected it.
#:
#: Waiting at least most of a window before the first retry is what makes the
#: retry meaningful rather than merely present.
MIN_DELAY_SECONDS = 20.0


async def with_backoff[T](
    operation: Callable[[], Awaitable[T]],
    *,
    retry_on: type[Exception] | tuple[type[Exception], ...],
    description: str,
    max_retries: int = MAX_RETRIES,
    base_delay: float = BASE_DELAY_SECONDS,
) -> T:
    """Run `operation`, retrying `retry_on` with backoff.

    Deliberately narrow in what it retries: a rate limit is transient, but a 400
    for a malformed request is not, and retrying that would turn a clear error
    into a slow one.
    """
    delay = base_delay
    for attempt in range(1, max_retries + 1):
        try:
            return await operation()
        except retry_on:
            if attempt == max_retries:
                logger.error(
                    "%s still failing after %d attempts; giving up",
                    description,
                    max_retries,
                )
                raise
            # Jitter between the floor and the ceiling rather than from zero,
            # so every attempt lands in a fresh window while still spreading
            # concurrent callers out.
            ceiling = min(delay, MAX_DELAY_SECONDS)
            wait = random.uniform(MIN_DELAY_SECONDS, max(ceiling, MIN_DELAY_SECONDS * 1.5))
            logger.warning(
                "%s rate limited; retrying in %.1fs (attempt %d/%d)",
                description,
                wait,
                attempt,
                max_retries,
            )
            await asyncio.sleep(wait)
            delay *= 2
    raise AssertionError("unreachable: the loop either returns or raises")
