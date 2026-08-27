"""The Google OAuth authorization-code exchange (ADR-0016).

This is the piece that was missing: `oauth_credentials()` in `credentials.py`
turns an *already-obtained* refresh token into a usable credential, but nothing
in the codebase ever obtained one -- "OAuth mode" meant "paste a token you got
some other way." This module is where a token actually gets obtained: build the
consent-screen URL, exchange the code Google returns for a refresh token, and
ask Google whose account just authorised.

Deliberately thin. It talks to exactly three Google endpoints and returns plain
data; the API layer owns the redirect/cookie/state mechanics of an HTTP OAuth
flow, and the domain never sees any of this -- a stored `credential_ref` looks
identical to ingestion whether it arrived by OAuth or by pasting a
service-account key.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from ragoogle_infra.sources.credentials import DRIVE_SCOPES

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"

# email/profile alongside Drive: the whole point of running a real consent flow
# rather than asking the user to paste a token is that Google tells us who
# authorised, so the principal (ADR-0003) can be filled in rather than typed
# blind.
SCOPES = (*DRIVE_SCOPES, "openid", "email")


class OAuthExchangeError(Exception):
    """Google rejected the authorization code, or the userinfo call failed.

    A distinct type rather than letting `httpx.HTTPStatusError` propagate: the
    API layer needs to tell "Google said no" apart from "we couldn't reach
    Google at all" only to log them differently, but both end up as the same
    user-facing bounce back to the frontend with an error banner.
    """


def build_authorization_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """The URL to send the browser to.

    `access_type=offline` is required to receive a refresh token at all --
    without it Google issues only a short-lived access token, useless for a
    service that needs to read a Drive on an ongoing basis. `prompt=consent`
    forces the consent screen (and a fresh refresh token) even for a user who
    already granted access once; Google only issues a refresh token on a
    user's *first* consent otherwise, which would silently break reconnecting
    a source whose token was lost or revoked.
    """
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


@dataclass(frozen=True, slots=True)
class ExchangedCredential:
    refresh_token: str
    principal: str


async def exchange_code(
    client: httpx.AsyncClient,
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> ExchangedCredential:
    """Trade an authorization code for a refresh token and the authorised email.

    Two round trips to Google, not one: the token endpoint returns an access
    token and a refresh token but not who the user is; the userinfo endpoint
    needs the access token to answer that. Both are server-to-server calls the
    browser never sees.
    """
    token_response = await client.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    if token_response.status_code != 200:
        raise OAuthExchangeError(
            f"Google rejected the authorization code: {token_response.status_code} "
            f"{token_response.text[:300]}"
        )
    token_payload = token_response.json()
    refresh_token = token_payload.get("refresh_token")
    access_token = token_payload.get("access_token")
    if not refresh_token:
        # Not a Google failure -- consent screens skipped or prompt=consent
        # not honoured for some reason. Worth its own message: "denied access"
        # and "granted access but no refresh token" need different next steps
        # from a user reconnecting a source.
        raise OAuthExchangeError(
            "Google did not return a refresh token. This usually means consent "
            "was already granted previously without offline access; try "
            "disconnecting RAGDrive at https://myaccount.google.com/permissions "
            "and reconnecting."
        )

    # Google echoes the scopes it actually granted here -- and silently drops
    # a requested scope that isn't enabled on the OAuth consent screen's Data
    # Access configuration rather than rejecting the authorization outright,
    # so a token can come back looking successful while missing Drive access
    # entirely. Catching that here, before a refresh token that will always
    # fail is ever stored, is the only way to fail at connect time instead of
    # on the first Drive call afterwards. Absent when a caller doesn't model
    # this field (tests, non-Google token endpoints) -- nothing to check then.
    granted_scope = token_payload.get("scope")
    if granted_scope is not None:
        granted = set(granted_scope.split())
        missing = [scope for scope in DRIVE_SCOPES if scope not in granted]
        if missing:
            raise OAuthExchangeError(
                "Google granted access but not to Drive -- "
                f"{', '.join(missing)} missing from the granted scope "
                f"({granted_scope!r}). Add it under OAuth consent screen > Data "
                "Access in Google Cloud Console, then reconnect."
            )

    userinfo_response = await client.get(
        USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
    )
    if userinfo_response.status_code != 200:
        raise OAuthExchangeError(
            f"authorised, but could not confirm the account: "
            f"{userinfo_response.status_code} {userinfo_response.text[:300]}"
        )
    email = userinfo_response.json().get("email")
    if not email:
        raise OAuthExchangeError("Google's userinfo response had no email")

    return ExchangedCredential(refresh_token=refresh_token, principal=email)
