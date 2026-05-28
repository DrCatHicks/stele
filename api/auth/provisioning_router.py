"""Admin DB-credential endpoints: the registry + the UI-driven provisioning flow.

Read and *request*, never role DDL. ``stele_api`` has no CREATEROLE (design doc
§3.10), so these endpoints only read the registry/outbox and INSERT provisioning
*requests*; a separate privileged worker (``api.credential_worker``) performs the
``CREATE ROLE`` / ``GRANT``. Every route here is admin-only.

- GET ``""`` — the credential registry (who holds what; metadata only, no password).
- POST ``/grant`` — grant a person DB access at a tier. Ensures their app account
  + role and enqueues a provision request. The reviewer (PII) tier requires the
  admin to re-confirm their own password — a step-up, since it mints a credential
  that can read identifying data.
- POST ``/{login_role}/revoke`` — enqueue a revoke.
- GET ``/requests`` and ``/requests/{id}`` — poll request status.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from api.auth import provisioning, service
from api.auth.deps import require_role
from api.auth.schemas import DbCredentialOut, GrantDbAccessRequest, ProvisionRequestOut
from api.auth.service import AuthenticatedUser
from api.db import SessionDep

router = APIRouter(prefix="/admin/db-credentials", tags=["admin"])

# Who holds which data-access credential, and who may mint one, is admin-only —
# the narrowest gate (design doc §3.10).
_admin_only = Depends(require_role("admin"))
AdminUser = Annotated[AuthenticatedUser, Depends(require_role("admin"))]


@router.get("", response_model=list[DbCredentialOut], dependencies=[_admin_only])
async def list_db_credentials(session: SessionDep) -> list[DbCredentialOut]:
    grants = await provisioning.list_grants(session)
    return [DbCredentialOut.model_validate(g) for g in grants]


@router.post("/grant", response_model=ProvisionRequestOut, status_code=202)
async def grant_access(
    body: GrantDbAccessRequest, session: SessionDep, admin: AdminUser
) -> ProvisionRequestOut:
    # Step-up: minting a PII-capable (reviewer) credential requires the admin to
    # re-confirm their own password, not just hold a live session.
    if body.access == "reviewer" and (
        not body.confirm_password
        or not await service.verify_user_password(session, admin.id, body.confirm_password)
    ):
        raise HTTPException(
            status_code=403,
            detail="reviewer-tier grant requires confirming your password",
        )
    try:
        request, _ = await service.grant_db_access(
            session,
            email=body.email,
            access=body.access,
            actor_id=admin.id,
            initial_password=body.initial_password,
        )
    except service.InvalidAccess:
        raise HTTPException(
            status_code=422, detail="access must be 'analyst' or 'reviewer'"
        ) from None
    except service.MissingInitialPassword:
        raise HTTPException(
            status_code=422, detail="initial_password is required to create a new account"
        ) from None
    except service.DuplicateGrant:
        raise HTTPException(
            status_code=409,
            detail="subject already has an active or pending credential for this tier",
        ) from None
    return ProvisionRequestOut.model_validate(request)


@router.post("/{login_role}/revoke", response_model=ProvisionRequestOut, status_code=202)
async def revoke_credential(
    login_role: str, session: SessionDep, admin: AdminUser
) -> ProvisionRequestOut:
    grant = await provisioning.get_grant_by_login_role(session, login_role)
    if grant is None:
        raise HTTPException(status_code=404, detail="unknown login role")
    if grant.status != "active":
        raise HTTPException(status_code=409, detail="credential is not active")
    request = await provisioning.enqueue_request(
        session,
        action="revoke",
        access=grant.access,
        subject_label=grant.subject_label,
        login_role=login_role,
        requested_by=admin.id,
    )
    return ProvisionRequestOut.model_validate(request)


@router.get("/requests", response_model=list[ProvisionRequestOut], dependencies=[_admin_only])
async def list_provision_requests(session: SessionDep) -> list[ProvisionRequestOut]:
    return [
        ProvisionRequestOut.model_validate(r) for r in await provisioning.list_requests(session)
    ]


@router.get(
    "/requests/{request_id}", response_model=ProvisionRequestOut, dependencies=[_admin_only]
)
async def get_provision_request(request_id: int, session: SessionDep) -> ProvisionRequestOut:
    request = await provisioning.get_request(session, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="unknown request")
    return ProvisionRequestOut.model_validate(request)
