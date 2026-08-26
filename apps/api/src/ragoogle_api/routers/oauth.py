"""The Google OAuth consent flow (ADR-0016).

Two endpoints, both browser-navigation targets rather than JSON APIs: the
frontend sets `window.location.href` to `/oauth/google/start`, the browser goes
to Google, and Google's own redirect lands on `/oauth/google/callback`, which
bounces the browser back into the frontend with the result in the query string.
Neither is meant to be called with `fetch()` -- a fetch would follow the
redirect silently instead of navigating the browser to Google's consent screen.

State/CSRF: an opaque nonce is round-tripped two ways -- inside the `state`
parameter Google echoes back verbatim, and in a short-lived HttpOnly cookie set
during `/start`. The callback accepts a code only if both match, which is what
stops a third party from tricking a signed-in user into connecting a Drive the
attacker controls (the classic OAuth CSRF/login-CSRF attack this parameter
exists to prevent).
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import uuid
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Query
from fastapi.responses import RedirectResponse

from ragoogle_api.deps import ContainerDep
from ragoogle_infra.sources.google_oauth import (
    OAuthExchangeError,
    build_authorization_url,
    exchange_code,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth/google", tags=["oauth"])

STATE_COOKIE = "ragdrive_oauth_state"
# Long enough for a user to sit on Google's consent screen, short enough that a
# stale cookie from an abandoned attempt is not a lingering CSRF surface.
STATE_MAX_AGE_SECONDS = 600


def _encode_state(*, nonce: str, return_path: str, editing_source_id: str | None) -> str:
    payload = {"nonce": nonce, "return_path": return_path, "editing_source_id": editing_source_id}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_state(raw: str) -> dict[str, str | None]:
    payload: dict[str, str | None] = json.loads(base64.urlsafe_b64decode(raw.encode()))
    return payload


@router.get("/start", operation_id="startGoogleOAuth", include_in_schema=True)
async def start(
    container: ContainerDep,
    return_path: str = "/configuration",
    editing_source_id: str | None = None,
) -> RedirectResponse:
    """Redirect the browser to Google's consent screen.

    `return_path` and `editing_source_id` exist so the *same* button and flow
    work from both the create dialog (no source yet) and an existing source's
    edit dialog (reconnecting or replacing its credential): both round-trip
    through `state` and come back on the callback redirect.
    """
    settings = container.settings
    if not settings.google_oauth_client_id:
        return RedirectResponse(
            _error_redirect(
                settings.frontend_public_url,
                return_path,
                "Google OAuth is not configured on this deployment "
                "(RAGOOGLE_GOOGLE_OAUTH_CLIENT_ID is unset).",
            ),
            status_code=307,
        )

    nonce = secrets.token_urlsafe(24)
    state = _encode_state(nonce=nonce, return_path=return_path, editing_source_id=editing_source_id)
    url = build_authorization_url(
        client_id=settings.google_oauth_client_id,
        redirect_uri=settings.google_oauth_redirect_uri,
        state=state,
    )
    response = RedirectResponse(url, status_code=307)
    response.set_cookie(
        STATE_COOKIE,
        nonce,
        max_age=STATE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.google_oauth_redirect_uri.startswith("https://"),
    )
    return response


@router.get("/callback", operation_id="googleOAuthCallback", include_in_schema=True)
async def callback(
    container: ContainerDep,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    oauth_state_cookie: Annotated[str | None, Cookie(alias=STATE_COOKIE)] = None,
) -> RedirectResponse:
    """Exchange the code, store the credential, send the browser home.

    Every failure path bounces back into the frontend with an error message in
    the query string rather than rendering a raw API error page -- the browser
    arrives here mid-navigation from Google, not from a fetch call the frontend
    can catch, so an API-shaped JSON error is not something a user would ever
    usefully see.
    """
    settings = container.settings
    return_path, editing_source_id = "/configuration", None
    if state:
        try:
            decoded = _decode_state(state)
            return_path = str(decoded.get("return_path") or return_path)
            editing_source_id = decoded.get("editing_source_id")
        except (ValueError, TypeError, json.JSONDecodeError):
            pass  # fall through to the generic error below

    if error:
        return RedirectResponse(
            _error_redirect(settings.frontend_public_url, return_path, f"Google said: {error}"),
            status_code=307,
        )
    if not code or not state:
        return RedirectResponse(
            _error_redirect(settings.frontend_public_url, return_path, "missing code or state"),
            status_code=307,
        )

    decoded = _decode_state(state)
    if not oauth_state_cookie or decoded.get("nonce") != oauth_state_cookie:
        return RedirectResponse(
            _error_redirect(
                settings.frontend_public_url,
                return_path,
                "the connection request could not be verified; please try again",
            ),
            status_code=307,
        )

    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        return RedirectResponse(
            _error_redirect(
                settings.frontend_public_url, return_path, "Google OAuth is not configured"
            ),
            status_code=307,
        )
    if container.credentials is None:
        return RedirectResponse(
            _error_redirect(
                settings.frontend_public_url,
                return_path,
                "RAGOOGLE_CREDENTIAL_SECRET is not configured; refusing to store "
                "the credential unencrypted",
            ),
            status_code=307,
        )

    try:
        async with httpx.AsyncClient() as client:
            exchanged = await exchange_code(
                client,
                code=code,
                client_id=settings.google_oauth_client_id,
                client_secret=settings.google_oauth_client_secret,
                redirect_uri=settings.google_oauth_redirect_uri,
            )
    except OAuthExchangeError as exc:
        logger.warning("google oauth exchange failed: %s", exc)
        return RedirectResponse(
            _error_redirect(settings.frontend_public_url, return_path, str(exc)),
            status_code=307,
        )

    reference = f"google-oauth/{uuid.uuid4()}"
    secret = json.dumps(
        {
            "refresh_token": exchanged.refresh_token,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
        }
    )
    await container.credentials.put(reference, secret)

    params = {
        "oauth_status": "connected",
        "credential_ref": reference,
        "principal": exchanged.principal,
    }
    if editing_source_id:
        params["editing_source_id"] = editing_source_id
    response = RedirectResponse(
        f"{settings.frontend_public_url}{return_path}?{urlencode(params)}", status_code=307
    )
    response.delete_cookie(STATE_COOKIE)
    return response


def _error_redirect(frontend_public_url: str, return_path: str, message: str) -> str:
    params = urlencode({"oauth_status": "error", "message": message})
    return f"{frontend_public_url}{return_path}?{params}"
