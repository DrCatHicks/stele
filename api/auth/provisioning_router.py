"""Admin-only view of the analyst/reviewer DB-credential registry (design doc §3.10).

Read-only by design. Provisioning, rotation, and revocation are an operational
procedure carried out by ``scripts/provision_db_credential.py`` over an elevated
connection — the public ``stele_api`` role has no role-DDL privilege, so this
endpoint exposes the audit trail (who holds which data-access credential, since
when, live or revoked) for the admin UI without putting ``CREATE ROLE`` behind the
request path. No password is ever returned: the registry doesn't store one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import provisioning
from api.auth.deps import require_role
from api.auth.schemas import DbCredentialOut
from api.db import SessionDep

router = APIRouter(prefix="/admin/db-credentials", tags=["admin"])

# Who holds which data-access credential is admin-only recon; the narrowest gate
# (design doc §3.10). Provisioning itself is the CLI's job, not an endpoint.
_admin_only = Depends(require_role("admin"))


@router.get("", response_model=list[DbCredentialOut], dependencies=[_admin_only])
async def list_db_credentials(session: SessionDep) -> list[DbCredentialOut]:
    grants = await provisioning.list_grants(session)
    return [DbCredentialOut.model_validate(g) for g in grants]
