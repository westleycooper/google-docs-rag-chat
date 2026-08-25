"""Document source adapters (ADR-0003)."""

from ragoogle_infra.sources.credentials import (
    DriveCredentialFactory,
    oauth_credentials,
    service_account_credentials,
)
from ragoogle_infra.sources.google_drive import GoogleDriveSource

__all__ = [
    "DriveCredentialFactory",
    "GoogleDriveSource",
    "oauth_credentials",
    "service_account_credentials",
]
