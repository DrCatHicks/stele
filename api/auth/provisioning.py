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

import re
import secrets
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.models import DbCredentialGrant

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
