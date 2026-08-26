"""The Google OAuth authorization-code exchange (ADR-0016).

Against a stubbed transport, not real Google endpoints -- what matters here is
that the module builds the right request shapes and turns Google's various
failure modes into a clear OAuthExchangeError, not that Google itself works.
"""

from __future__ import annotations

import httpx
import pytest

from ragoogle_infra.sources.google_oauth import (
    OAuthExchangeError,
    build_authorization_url,
    exchange_code,
)


def test_the_authorization_url_requests_offline_access_and_forced_consent():
    """access_type=offline is the only way to receive a refresh token at all;
    prompt=consent forces one even on a second connection attempt."""
    url = build_authorization_url(
        client_id="client-1", redirect_uri="https://api.example.com/cb", state="xyz"
    )
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=xyz" in url
    assert "client_id=client-1" in url


def test_the_authorization_url_requests_drive_readonly_and_email():
    url = build_authorization_url(client_id="c", redirect_uri="https://x", state="s")
    assert "drive.readonly" in url
    assert "email" in url


class _StubTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: dict[str, httpx.Response]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for path, response in self.responses.items():
            if request.url.path == path:
                return response
        raise AssertionError(f"unexpected request to {request.url}")


def client_for(responses: dict[str, httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_StubTransport(responses))


TOKEN_PATH = "/token"
USERINFO_PATH = "/oauth2/v3/userinfo"


async def test_a_successful_exchange_returns_the_refresh_token_and_email():
    transport = _StubTransport(
        {
            TOKEN_PATH: httpx.Response(200, json={"access_token": "at", "refresh_token": "rt"}),
            USERINFO_PATH: httpx.Response(200, json={"email": "lead@example.com"}),
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        # exchange_code hits fixed Google URLs; the stub transport matches on
        # path regardless of host, so this exercises the real call shape.
        result = await exchange_code(
            client,
            code="auth-code",
            client_id="cid",
            client_secret="csecret",
            redirect_uri="https://api.example.com/oauth/google/callback",
        )
    assert result.refresh_token == "rt"
    assert result.principal == "lead@example.com"


async def test_the_token_request_carries_the_authorization_code_grant():
    transport = _StubTransport(
        {
            TOKEN_PATH: httpx.Response(200, json={"access_token": "a", "refresh_token": "r"}),
            USERINFO_PATH: httpx.Response(200, json={"email": "x@example.com"}),
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        await exchange_code(
            client, code="c1", client_id="cid", client_secret="cs", redirect_uri="https://x"
        )
    body = transport.requests[0].content.decode()
    assert "grant_type=authorization_code" in body
    assert "code=c1" in body


async def test_a_rejected_code_raises_with_googles_response_attached():
    async with client_for({TOKEN_PATH: httpx.Response(400, text="invalid_grant")}) as client:
        with pytest.raises(OAuthExchangeError, match="invalid_grant"):
            await exchange_code(
                client, code="bad", client_id="c", client_secret="s", redirect_uri="https://x"
            )


async def test_a_missing_refresh_token_is_a_distinct_actionable_error():
    """Distinguished from 'denied' because the fix is different: revoke and
    reconnect, not merely retry."""
    transport = _StubTransport({TOKEN_PATH: httpx.Response(200, json={"access_token": "a"})})
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(OAuthExchangeError, match=r"myaccount\.google\.com/permissions"):
            await exchange_code(
                client, code="c", client_id="c", client_secret="s", redirect_uri="https://x"
            )


async def test_a_failed_userinfo_call_is_reported_as_a_partial_success():
    transport = _StubTransport(
        {
            TOKEN_PATH: httpx.Response(200, json={"access_token": "a", "refresh_token": "r"}),
            USERINFO_PATH: httpx.Response(500, text="server error"),
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(OAuthExchangeError, match="authorised, but"):
            await exchange_code(
                client, code="c", client_id="c", client_secret="s", redirect_uri="https://x"
            )


async def test_a_userinfo_response_with_no_email_is_rejected():
    transport = _StubTransport(
        {
            TOKEN_PATH: httpx.Response(200, json={"access_token": "a", "refresh_token": "r"}),
            USERINFO_PATH: httpx.Response(200, json={}),
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(OAuthExchangeError, match="no email"):
            await exchange_code(
                client, code="c", client_id="c", client_secret="s", redirect_uri="https://x"
            )
