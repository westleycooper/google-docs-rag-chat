"""The observability ping (ADR-0006): frontend/observability liveness.

The API cannot know whether a human has a browser tab open, but it can and
does check whether the frontend's own web server answers -- the same standard
`vectorstore` is held to. These test that logic in isolation from HTTP.
"""

from __future__ import annotations

import httpx
import pytest

from ragoogle_api.routers.health import _ping


class _StubTransport(httpx.AsyncBaseTransport):
    """A transport that returns a fixed status, or raises a connection error."""

    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.status_code is None:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(self.status_code, request=request)


def client_for(status_code: int | None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_StubTransport(status_code))


async def test_an_unconfigured_url_is_unknown_not_guessed():
    async with client_for(200) as client:
        state, latency = await _ping(client, None)
    assert state == "unknown"
    assert latency is None


async def test_a_reachable_server_is_ok():
    async with client_for(200) as client:
        state, latency = await _ping(client, "http://frontend")
    assert state == "ok"
    assert latency is not None
    assert latency >= 0


async def test_a_non_2xx_response_is_down_not_unknown():
    """We tried and got an answer -- 'unknown' would understate what we know."""
    async with client_for(503) as client:
        state, _ = await _ping(client, "http://frontend")
    assert state == "down"


async def test_a_connection_failure_is_down():
    async with client_for(None) as client:
        state, latency = await _ping(client, "http://frontend")
    assert state == "down"
    assert latency is None


@pytest.mark.parametrize("status_code", [200, 204, 201])
async def test_success_covers_the_2xx_range(status_code):
    async with client_for(status_code) as client:
        state, _ = await _ping(client, "http://frontend")
    assert state == "ok"


async def test_a_redirect_is_not_treated_as_success():
    """Not followed, and not silently accepted -- a health probe redirected to
    e.g. a login page would otherwise report a misconfigured server as ok."""
    async with client_for(301) as client:
        state, _ = await _ping(client, "http://frontend")
    assert state == "down"


async def test_a_slow_server_times_out_as_down():
    class _SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out", request=request)

    async with httpx.AsyncClient(transport=_SlowTransport()) as client:
        state, latency = await _ping(client, "http://frontend")
    assert state == "down"
    assert latency is None
