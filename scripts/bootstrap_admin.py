"""Bootstrap the initial admin operator from the environment.

The first admin can't be created through the (auth-gated) admin UI, so it's
seeded here from env vars — never hard-coded (design doc §3.10). Idempotent: if
an account with the email already exists it's left untouched, so re-running on
deploy is safe.

Run:  STELE_ADMIN_EMAIL=you@example.com STELE_ADMIN_PASSWORD=... \
        uv run python scripts/bootstrap_admin.py
Honors STELE_DATABASE_URL (CI/prod point it at the least-privileged stele_api role).

Optional STELE_ADMIN_ROLE (default 'admin') lets the same script seed a
researcher/reviewer if ever needed; it must be one of the application roles.
"""

from __future__ import annotations

import asyncio
import os
import sys

from api.auth import service
from api.db import SessionLocal


async def bootstrap() -> int:
    email = os.environ.get("STELE_ADMIN_EMAIL")
    password = os.environ.get("STELE_ADMIN_PASSWORD")
    role = os.environ.get("STELE_ADMIN_ROLE", "admin")
    if not email or not password:
        print(
            "STELE_ADMIN_EMAIL and STELE_ADMIN_PASSWORD must be set.",
            file=sys.stderr,
        )
        return 2

    async with SessionLocal() as session:
        try:
            user = await service.create_user(session, email, password, [role])
        except service.DuplicateUser:
            print(f"User {service.normalize_email(email)} already exists; nothing to do.")
            return 0
        except service.InvalidRole:
            print(
                f"Invalid role {role!r}; must be one of {sorted(service.VALID_ROLES)}.",
                file=sys.stderr,
            )
            return 2
    print(f"Created {role} {user.email} (id {user.id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(bootstrap()))
