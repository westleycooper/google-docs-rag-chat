"""Standalone credential storage (ADR-0016).

Decoupled from any source: a secret can be stored -- by pasting a
service-account key, or by completing the OAuth flow in `routers/oauth.py` --
before the source it will belong to has been created. That is what makes
`POST /sources/browse-folders` usable from the *create* dialog rather than only
after a source has been saved once.

`PUT /sources/{id}/credential` still exists, for the different case of rotating
an *existing* source's credential -- the two serve different moments in a
source's lifecycle and neither replaces the other.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from ragoogle_api.deps import ContainerDep
from ragoogle_api.schemas import CredentialIn, StoredCredentialOut

router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.post(
    "",
    operation_id="storeCredential",
    response_model=StoredCredentialOut,
    status_code=status.HTTP_201_CREATED,
)
async def store_credential(payload: CredentialIn, container: ContainerDep) -> StoredCredentialOut:
    """Store a secret, encrypted at rest, under a freshly generated reference.

    The reference is always server-generated, never typed by a user -- a
    hand-typed `credential_ref` is one typo from silently pointing a source at
    nothing, or at another source's key.
    """
    if container.credentials is None:
        raise HTTPException(
            status_code=503,
            detail="RAGOOGLE_CREDENTIAL_SECRET is not configured; refusing to "
            "store credentials unencrypted.",
        )
    reference = f"service-account/{uuid.uuid4()}"
    await container.credentials.put(reference, payload.secret)
    return StoredCredentialOut(credential_ref=reference)
