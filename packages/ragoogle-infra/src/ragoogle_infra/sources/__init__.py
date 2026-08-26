"""Document source adapters (ADR-0003)."""

from ragoogle_infra.sources.credentials import (
    DriveCredentialFactory,
    oauth_credentials,
    service_account_credentials,
)
from ragoogle_infra.sources.google_drive import GoogleDriveSource
from ragoogle_infra.sources.local_directory import LocalDirectorySource

__all__ = [
    "DriveCredentialFactory",
    "GoogleDriveSource",
    "LocalDirectorySource",
    "oauth_credentials",
    "service_account_credentials",
]
