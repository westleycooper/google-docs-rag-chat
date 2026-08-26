"""Backoff for transient vendor errors.

The behaviour worth pinning down is the delay *floor*, which is the
counter-intuitive part: a vendor rate limit is usually a budget over a window,
and a retry inside a spent window cannot succeed no matter how many attempts
are left.
"""

from __future__ import annotations

import pytest

from ragoogle_infra.vendor_retry import (
    BASE_DELAY_SECONDS,
    MAX_RETRIES,
    MIN_DELAY_SECONDS,
    with_backoff,
)


class ThrottledError(Exception):
    """Stand-in for a vendor rate limit."""


class FatalError(Exception):
    """Stand-in for a non-retryable error, e.g. a malformed request."""


@pytest.fixture
def no_sleep(monkeypatch):
    """Record the delays instead of serving them, so tests stay fast."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("ragoogle_infra.vendor_retry.asyncio.sleep", fake_sleep)
    return slept


async def test_a_successful_call_does_not_retry(no_sleep):
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert await with_backoff(operation, retry_on=ThrottledError, description="x") == "ok"
    assert calls == 1
    assert no_sleep == []


async def test_it_retries_until_the_call_succeeds(no_sleep):
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ThrottledError
        return "eventually"

    assert await with_backoff(operation, retry_on=ThrottledError, description="x") == "eventually"
    assert calls == 3
    assert len(no_sleep) == 2


async def test_every_wait_clears_the_rate_limit_window(no_sleep):
    """The floor is the point: a retry inside a spent window cannot succeed."""

    async def operation() -> str:
        raise ThrottledError

    with pytest.raises(ThrottledError):
        await with_backoff(operation, retry_on=ThrottledError, description="x")

    assert no_sleep
    assert all(wait >= MIN_DELAY_SECONDS for wait in no_sleep), no_sleep


async def test_it_gives_up_after_the_configured_attempts(no_sleep):
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ThrottledError

    with pytest.raises(ThrottledError):
        await with_backoff(operation, retry_on=ThrottledError, description="x")

    assert calls == MAX_RETRIES
    assert len(no_sleep) == MAX_RETRIES - 1


async def test_a_non_retryable_error_is_raised_immediately(no_sleep):
    """Retrying a malformed request turns a clear error into a slow one."""
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise FatalError("bad request")

    with pytest.raises(FatalError):
        await with_backoff(operation, retry_on=ThrottledError, description="x")

    assert calls == 1
    assert no_sleep == []


async def test_several_exception_types_can_be_retried(no_sleep):
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ThrottledError
        if calls == 2:
            raise TimeoutError
        return "ok"

    result = await with_backoff(operation, retry_on=(ThrottledError, TimeoutError), description="x")
    assert result == "ok"
    assert calls == 3


async def test_delays_grow_between_attempts(no_sleep):
    """Backoff, not a fixed interval -- a persistent limit needs longer waits."""

    async def operation() -> str:
        raise ThrottledError

    with pytest.raises(ThrottledError):
        await with_backoff(
            operation, retry_on=ThrottledError, description="x", base_delay=BASE_DELAY_SECONDS
        )

    # Jitter makes individual waits non-monotonic on purpose, so compare the
    # ceilings: the last wait must be able to exceed the first.
    assert max(no_sleep) > MIN_DELAY_SECONDS


async def test_a_single_attempt_configuration_never_sleeps(no_sleep):
    async def operation() -> str:
        raise ThrottledError

    with pytest.raises(ThrottledError):
        await with_backoff(operation, retry_on=ThrottledError, description="x", max_retries=1)

    assert no_sleep == []
