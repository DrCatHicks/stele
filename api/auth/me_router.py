"""Self-service DB credentials for the signed-in recipient (§3.10 revision).

An analyst or reviewer who has been granted DB access signs in and uses these
endpoints to reveal their freshly-minted password **once** and to regenerate it
if they lose it. Reveal is gated to the recipient's own session (never an
unauthenticated link) and scoped to credentials whose registry ``subject_label``
matches their email; the one-time semantics live in ``secret_delivery``.

Regenerate enqueues a rotate request — the privileged worker mints the new
password and drops a fresh one-time delivery, which the recipient then reveals.
``stele_api`` never touches role DDL here either.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.auth import provisioning, secret_delivery
from api.auth.deps import CurrentUser
from api.auth.schemas import MyCredentialOut, ProvisionRequestOut, RevealedSecretOut
from api.db import SessionDep

router = APIRouter(prefix="/me/db-credentials", tags=["me"])


@router.get("", response_model=list[MyCredentialOut])
async def my_credentials(session: SessionDep, user: CurrentUser) -> list[MyCredentialOut]:
    grants = await provisioning.grants_for_subject(session, user.email)
    out: list[MyCredentialOut] = []
    for grant in grants:
        out.append(
            MyCredentialOut(
                login_role=grant.login_role,
                access=grant.access,
                status=grant.status,
                created_at=grant.created_at,
                has_pending_secret=await secret_delivery.has_pending_delivery(
                    session, user.id, grant.login_role
                ),
            )
        )
    return out


@router.post("/{login_role}/reveal", response_model=RevealedSecretOut)
async def reveal_credential(
    login_role: str, session: SessionDep, user: CurrentUser
) -> RevealedSecretOut:
    grant = await provisioning.get_grant_by_login_role(session, login_role)
    if grant is None or grant.subject_label != user.email:
        raise HTTPException(status_code=404, detail="unknown credential")
    revealed = await secret_delivery.reveal_for_user(session, user.id, login_role)
    if revealed is None:
        raise HTTPException(
            status_code=410,
            detail="no password to reveal (already revealed, expired, or not ready yet)",
        )
    group_role = provisioning.group_role_for(grant.access)
    return RevealedSecretOut(
        login_role=login_role,
        access=grant.access,
        group_role=group_role,
        password=revealed.password,
        set_role_sql=f"SET ROLE {group_role};",
    )


@router.post("/{login_role}/regenerate", response_model=ProvisionRequestOut, status_code=202)
async def regenerate_credential(
    login_role: str, session: SessionDep, user: CurrentUser
) -> ProvisionRequestOut:
    grant = await provisioning.get_grant_by_login_role(session, login_role)
    if grant is None or grant.subject_label != user.email:
        raise HTTPException(status_code=404, detail="unknown credential")
    if grant.status != "active":
        raise HTTPException(status_code=409, detail="credential is not active")
    request = await provisioning.enqueue_request(
        session,
        action="rotate",
        access=grant.access,
        subject_label=grant.subject_label,
        login_role=login_role,
        target_user_id=user.id,
        requested_by=user.id,
    )
    return ProvisionRequestOut.model_validate(request)
