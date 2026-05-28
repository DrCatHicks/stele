"""Analyst/reviewer DB-credential provisioning (design doc §3.10).

Two halves with deliberately different trust levels:

- **Pure helpers** here (no DB, no privilege): normalize a subject, derive a safe
  Postgres login-role name, map an access tier to its §3.3 group role, mint a
  password. These are the injection-critical bits — a subject label flows into a
  ``CREATE ROLE`` identifier — so ``derive_login_role`` validates the *result*
  against a strict charset and length, defence-in-depth on top of the CLI's
  ``psycopg.sql.Identifier`` quoting. Unit-tested in isolation.
- **Async registry reads** for the admin API. The registry (``app.db_credential_grants``)
  is written only by the out-of-band CLI over an elevated connection; the API
  (and thus ``stele_api``) reads it but never mints roles. The privileged
  ``CREATE ROLE`` / ``GRANT`` path lives in ``scripts/provision_db_credential.py``.

``stele_api`` has neither CREATEROLE nor membership-with-admin on the group roles,
so live provisioning from the request path is impossible by construction — which
is the point (§3.10 calls this an operational procedure).
"""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Sequence

import psycopg
from psycopg import sql
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.models import DbCredentialGrant, ProvisionRequest

# Access tier → the §3.3 Postgres group role whose privileges the login inherits
# via SET ROLE. analyst reads marts; reviewer reads pii.
ACCESS_GROUP_ROLE: dict[str, str] = {
    "analyst": "stele_analyst",
    "reviewer": "stele_pii_reviewer",
}
VALID_ACCESS = frozenset(ACCESS_GROUP_ROLE)

# Postgres role names are capped at NAMEDATALEN-1 (63) bytes. A provisioned login
# must look like stele_<access>_<slug>_<suffix> and contain nothing that could
# escape an identifier: lowercase ascii, digits, underscores, leading letter.
_LOGIN_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
# Keep the slug short so the prefix + suffix fit comfortably under 63 bytes.
_MAX_SLUG_LEN = 32


class ProvisioningError(Exception):
    """Provisioning input or state was invalid."""


def normalize_subject(subject: str) -> str:
    """Trim + lowercase, matching how operator emails are normalized."""
    return subject.strip().lower()


def group_role_for(access: str) -> str:
    """The §3.3 group role for an access tier, or raise on an unknown tier."""
    try:
        return ACCESS_GROUP_ROLE[access]
    except KeyError:
        raise ProvisioningError(
            f"unknown access {access!r}; expected one of {sorted(VALID_ACCESS)}"
        ) from None


def _slugify(subject: str) -> str:
    """Reduce a normalized subject to a role-name-safe fragment.

    Non-[a-z0-9] runs collapse to a single underscore; leading/trailing
    underscores are stripped. May be empty (e.g. subject was all punctuation),
    which derive_login_role then rejects rather than emitting a nameless role.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", normalize_subject(subject)).strip("_")
    return slug[:_MAX_SLUG_LEN].strip("_")


def random_suffix() -> str:
    """Short collision-breaker so re-provisioning a subject yields a fresh role."""
    return secrets.token_hex(2)


def derive_login_role(access: str, subject: str, suffix: str) -> str:
    """Build and validate the login-role name: stele_<access>_<slug>_<suffix>.

    Raises ProvisioningError if the subject slugifies to nothing or the result
    isn't a strict, length-bounded identifier — the name reaches DDL, so a bad
    one must fail loudly here, never get quoted-and-shipped.
    """
    group = group_role_for(access)  # validates access
    slug = _slugify(subject)
    if not slug:
        raise ProvisioningError(f"subject {subject!r} has no usable identifier characters")
    # group is "stele_analyst" / "stele_pii_reviewer"; reuse its "stele_<access>"
    # stem so the login reads stele_analyst_<slug>_<suffix>.
    login = f"{group}_{slug}_{suffix}"
    if not _LOGIN_ROLE_RE.fullmatch(login):
        raise ProvisioningError(f"derived login role {login!r} is not a valid identifier")
    return login


def generate_password() -> str:
    """A high-entropy, quote-safe password (urlsafe alphabet has no quotes)."""
    return secrets.token_urlsafe(24)


async def list_grants(session: AsyncSession) -> Sequence[DbCredentialGrant]:
    """All credential grants, newest first — for the admin registry view."""
    result = await session.execute(
        select(DbCredentialGrant).order_by(DbCredentialGrant.created_at.desc())
    )
    return result.scalars().all()


async def grants_for_subject(
    session: AsyncSession, subject_label: str
) -> Sequence[DbCredentialGrant]:
    """A subject's credential grants, newest first — for the recipient's own view."""
    result = await session.execute(
        select(DbCredentialGrant)
        .where(DbCredentialGrant.subject_label == subject_label)
        .order_by(DbCredentialGrant.created_at.desc())
    )
    return result.scalars().all()


async def get_grant_by_login_role(
    session: AsyncSession, login_role: str
) -> DbCredentialGrant | None:
    return (
        await session.execute(
            select(DbCredentialGrant).where(DbCredentialGrant.login_role == login_role)
        )
    ).scalar_one_or_none()


async def active_grant_exists(session: AsyncSession, subject_label: str, access: str) -> bool:
    row = (
        await session.execute(
            select(DbCredentialGrant.id).where(
                DbCredentialGrant.subject_label == subject_label,
                DbCredentialGrant.access == access,
                DbCredentialGrant.status == "active",
            )
        )
    ).first()
    return row is not None


# --- Outbox (async, the stele_api request path) -----------------------------
#
# The API never touches role DDL; it only writes/reads the outbox. The worker
# (above, sync, elevated) consumes it.


async def enqueue_request(
    session: AsyncSession,
    *,
    action: str,
    access: str | None = None,
    subject_label: str | None = None,
    target_user_id: int | None = None,
    requested_by: int | None = None,
    login_role: str | None = None,
) -> ProvisionRequest:
    req = ProvisionRequest(
        action=action,
        access=access,
        subject_label=subject_label,
        target_user_id=target_user_id,
        requested_by=requested_by,
        login_role=login_role,
    )
    session.add(req)
    await session.commit()
    await session.refresh(req)
    return req


async def get_request(session: AsyncSession, request_id: int) -> ProvisionRequest | None:
    return (
        await session.execute(select(ProvisionRequest).where(ProvisionRequest.id == request_id))
    ).scalar_one_or_none()


async def list_requests(session: AsyncSession, *, limit: int = 50) -> Sequence[ProvisionRequest]:
    """Recent provisioning requests, newest first — for the admin activity view."""
    result = await session.execute(
        select(ProvisionRequest).order_by(ProvisionRequest.created_at.desc()).limit(limit)
    )
    return result.scalars().all()


async def pending_request_exists(session: AsyncSession, subject_label: str, access: str) -> bool:
    """Whether a provision for this (subject, access) is already queued/unprocessed."""
    row = (
        await session.execute(
            select(ProvisionRequest.id).where(
                ProvisionRequest.subject_label == subject_label,
                ProvisionRequest.access == access,
                ProvisionRequest.action == "provision",
                ProvisionRequest.status == "pending",
            )
        )
    ).first()
    return row is not None


# --- Privileged DDL (sync, psycopg) -----------------------------------------
#
# The role-creating half of §3.10. These run over an *elevated* connection
# (CREATEROLE + ADMIN OPTION on the group roles) — never the stele_api request
# path — and are shared by the operator CLI (scripts/provision_db_credential.py)
# and the provisioning worker (api.credential_worker), so the two never drift.

_PROVISION_URL_ENV = "STELE_PROVISION_DATABASE_URL"
_DEV_FALLBACK_URL = "postgresql://stele_dev:dev@localhost:5432/stele"
_FALLBACK_FLAG = "STELE_ALLOW_DEV_FALLBACK"


def provision_conninfo() -> str:
    """Resolve the elevated libpq conninfo, stripping any SQLAlchemy driver tag.

    Refuses to guess: ``STELE_PROVISION_DATABASE_URL`` must be set, or the run
    fails — a missing/misspelled var must never silently provision into the wrong
    database. The dev-superuser fallback is available only as a deliberate opt-in
    (``STELE_ALLOW_DEV_FALLBACK=1``).
    """
    url = os.environ.get(_PROVISION_URL_ENV)
    if url is None:
        if os.environ.get(_FALLBACK_FLAG, "").strip().lower() not in {"1", "true", "yes"}:
            raise ProvisioningError(
                f"{_PROVISION_URL_ENV} is not set. Point it at a role with CREATEROLE "
                "and ADMIN OPTION on the group roles (a superuser in dev). To use the local "
                f"dev superuser fallback, opt in explicitly with {_FALLBACK_FLAG}=1."
            )
        url = _DEV_FALLBACK_URL
    # SQLAlchemy-style "+psycopg" suffix isn't valid libpq; drop it if present.
    return url.replace("+psycopg", "", 1)


def role_exists(conn: psycopg.Connection, role: str) -> bool:
    row = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    return row is not None


def active_credential(conn: psycopg.Connection, subject: str, access: str) -> str | None:
    """login_role of an existing active credential for this subject+access, if any."""
    row = conn.execute(
        "SELECT login_role FROM app.db_credential_grants "
        "WHERE subject_label = %s AND access = %s AND status = 'active'",
        (subject, access),
    ).fetchone()
    return row[0] if row else None


def provision_in_tx(
    conn: psycopg.Connection,
    access: str,
    subject: str,
    *,
    provisioned_by: int | None = None,
) -> tuple[str, str]:
    """Mint a per-person login role + its registry row; return (login_role, password).

    MUST run inside ``conn.transaction()``: Postgres role DDL is transactional, so
    the CREATE ROLE / GRANT and the audit row commit together or not at all — we
    never leave a role without its registry record. The login is NOINHERIT, so a
    connection with it is privilege-less until the user runs ``SET ROLE <group>``.
    The caller owns the group-role-exists and no-active-duplicate prechecks.
    """
    group_role = group_role_for(access)
    login_role = derive_login_role(access, subject, random_suffix())
    password = generate_password()
    conn.execute(
        sql.SQL("CREATE ROLE {login} LOGIN PASSWORD {pw} NOINHERIT").format(
            login=sql.Identifier(login_role), pw=sql.Literal(password)
        )
    )
    conn.execute(
        sql.SQL("GRANT {group} TO {login}").format(
            group=sql.Identifier(group_role), login=sql.Identifier(login_role)
        )
    )
    conn.execute(
        "INSERT INTO app.db_credential_grants "
        "(subject_label, access, login_role, status, provisioned_by) "
        "VALUES (%s, %s, %s, 'active', %s)",
        (subject, access, login_role, provisioned_by),
    )
    return login_role, password


def rotate_in_tx(conn: psycopg.Connection, login_role: str) -> str:
    """Set a new password on an existing login role; return it. Call inside a tx."""
    password = generate_password()
    conn.execute(
        sql.SQL("ALTER ROLE {login} PASSWORD {pw}").format(
            login=sql.Identifier(login_role), pw=sql.Literal(password)
        )
    )
    conn.execute(
        "UPDATE app.db_credential_grants SET rotated_at = now() WHERE login_role = %s",
        (login_role,),
    )
    return password


def revoke_in_tx(conn: psycopg.Connection, login_role: str) -> None:
    """Drop the login role and mark its registry row revoked. Call inside a tx.

    DROP ROLE removes group membership implicitly; revoking the registry row keeps
    the intent explicit and is harmless if the role was already dropped by hand.
    """
    if role_exists(conn, login_role):
        conn.execute(sql.SQL("DROP ROLE {login}").format(login=sql.Identifier(login_role)))
    conn.execute(
        "UPDATE app.db_credential_grants "
        "SET status = 'revoked', revoked_at = now() WHERE login_role = %s",
        (login_role,),
    )
