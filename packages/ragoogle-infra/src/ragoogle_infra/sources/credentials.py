"""Google credentials for both auth modes (ADR-0003).

Both paths resolve to the same thing from the caller's point of view: a
credential and the *effective principal* whose access defines the corpus
boundary. That principal is not cosmetic -- it appears on every skip record, and
a skip that cannot say who was denied does not answer the question users ask.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from google.auth.credentials import Credentials
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials

# Read-only throughout. Ragoogle never needs to modify a corpus, and a narrower
# scope is the difference between a leaked credential being an exposure and
# being a catastrophe.
DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)


@dataclass(frozen=True, slots=True)
class DriveCredentialFactory:
    """A credential plus the principal it acts as."""

    credentials: Credentials
    principal: str


class CredentialSource(Protocol):
    def __call__(self) -> DriveCredentialFactory: ...


def service_account_credentials(
    key_json: str | dict[str, object], subject: str
) -> DriveCredentialFactory:
    """Domain-wide delegation: act as `subject`.

    The corpus is whatever `subject` can see, which is why the subject rather
    than the service account is recorded as the principal. A skip saying
    "denied to ragoogle-ingest@project.iam.gserviceaccount.com" would be true
    and useless; "denied to finance-lead@company.com" is actionable.
    """
    if not subject.strip():
        raise ValueError(
            "domain-wide delegation requires a subject to impersonate; without one "
            "the service account sees only its own (empty) Drive"
        )
    info = json.loads(key_json) if isinstance(key_json, str) else key_json
    # google-auth ships no type information for these constructors.
    creds = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
        info, scopes=list(DRIVE_SCOPES)
    ).with_subject(subject)
    return DriveCredentialFactory(credentials=creds, principal=subject)


def oauth_credentials(
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    principal: str,
    token_uri: str = "https://oauth2.googleapis.com/token",
) -> DriveCredentialFactory:
    """User consent: act as the user who authorised.

    No admin involvement, works for personal Gmail, and the corpus is exactly
    what that user can see.
    """
    if not refresh_token.strip():
        raise ValueError("OAuth credentials require a refresh token")
    creds = OAuthCredentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=token_uri,
        scopes=list(DRIVE_SCOPES),
    )
    return DriveCredentialFactory(credentials=creds, principal=principal)
