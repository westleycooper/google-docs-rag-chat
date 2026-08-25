"""The credential store port (ADR-0003).

Service-account keys and OAuth refresh tokens are long-lived credentials to a
user's entire document corpus, so they get the same treatment: encrypted at rest,
never held in an aggregate, referenced only by an opaque handle.

The port exists so the key custodian is swappable. A self-hosted deployment can
hold the data key locally; each cloud target in ADR-0005 has its own KMS. What
must not vary is that nothing outside this boundary ever sees plaintext.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CredentialStore(Protocol):
    async def put(self, reference: str, secret: str) -> None:
        """Store or replace a secret under an opaque reference."""
        ...

    async def get(self, reference: str) -> str:
        """Retrieve a secret. Raises `NotFound` if the reference is unknown."""
        ...

    async def delete(self, reference: str) -> None:
        """Remove a secret. Deleting an unknown reference is not an error."""
        ...
