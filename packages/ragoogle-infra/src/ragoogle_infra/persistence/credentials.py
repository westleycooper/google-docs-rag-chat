"""Encrypted credential storage (ADR-0003).

Fernet (AES-128-CBC + HMAC-SHA256) with the data key supplied by configuration.
That makes this the *self-hosted* custodian: the operator holds the key. Each
cloud target in ADR-0005 gets its own adapter behind the same port, where the
key lives in Key Vault / KMS / Cloud KMS and never reaches the process.

What is constant either way: ciphertext is all that touches the database, so a
dump without the key is inert.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ragoogle_core.shared.errors import ConfigurationError, NotFound


def derive_key(secret: str) -> bytes:
    """Turn an operator-supplied secret into a Fernet key.

    A plain SHA-256 rather than a KDF with a work factor, deliberately: this
    input is a machine-generated high-entropy secret from the deployment's
    configuration, not a human password. A work factor would add startup latency
    while defending against a brute-force attack that is not available here.
    Passphrase-derived keys would need scrypt; that is what the cloud KMS
    adapters exist to avoid.
    """
    if len(secret) < 32:
        raise ConfigurationError(
            "the credential encryption secret must be at least 32 characters. "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


class PgCredentialStore:
    """Implements `ragoogle_core.ports.CredentialStore`."""

    def __init__(self, engine: AsyncEngine, secret: str) -> None:
        self._engine = engine
        self._fernet = Fernet(derive_key(secret))
        # Identifies the key without revealing it, so rotation can find rows
        # still encrypted under the previous key.
        self._key_id = hashlib.sha256(secret.encode()).hexdigest()[:16]

    async def put(self, reference: str, secret: str) -> None:
        ciphertext = self._fernet.encrypt(secret.encode())
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO credentials (reference, ciphertext, key_id)
                    VALUES (:reference, :ciphertext, :key_id)
                    ON CONFLICT (reference) DO UPDATE SET
                        ciphertext = EXCLUDED.ciphertext,
                        key_id = EXCLUDED.key_id,
                        updated_at = now()
                    """
                ),
                {
                    "reference": reference,
                    "ciphertext": ciphertext,
                    "key_id": self._key_id,
                },
            )

    async def get(self, reference: str) -> str:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text("SELECT ciphertext, key_id FROM credentials WHERE reference = :r"),
                {"r": reference},
            )
            row = result.fetchone()
        if row is None:
            raise NotFound("Credential", reference)
        try:
            return self._fernet.decrypt(bytes(row.ciphertext)).decode()
        except InvalidToken as error:
            # Almost always a rotated or mismatched key rather than tampering.
            # Saying so turns a baffling failure into an actionable one.
            raise ConfigurationError(
                f"credential {reference!r} was encrypted under key {row.key_id}, "
                f"which this process cannot decrypt. Check "
                f"RAGOOGLE_CREDENTIAL_SECRET matches the deployment that wrote it."
            ) from error

    async def delete(self, reference: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM credentials WHERE reference = :r"), {"r": reference}
            )
