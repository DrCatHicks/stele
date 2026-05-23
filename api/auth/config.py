"""Auth configuration, resolved from the environment at call time.

Read lazily (not at import) so tests and deployments can set the variables after
the module is imported. The secret has a loud dev-only default; production must
set ``STELE_SESSION_SECRET`` (a leaked secret lets an attacker forge cookies,
though the signed value still has to match a live server-side session row).
"""

from __future__ import annotations

import os
from datetime import timedelta

# Cookie carrying the signed session token.
COOKIE_NAME = "stele_session"
# itsdangerous salt namespacing the signature to this use.
SIGNER_SALT = "stele.session"
# Server-side session lifetime. Expiry is enforced in the DB row, not the cookie.
SESSION_TTL = timedelta(days=7)

_DEV_SECRET = "dev-insecure-session-secret-change-me"  # noqa: S105  # dev-only default, overridden in prod


def session_secret() -> str:
    return os.environ.get("STELE_SESSION_SECRET", _DEV_SECRET)


def cookie_secure() -> bool:
    # Secure by default; tests and plain-HTTP dev set STELE_COOKIE_SECURE=false so
    # the cookie survives a non-HTTPS round-trip.
    return os.environ.get("STELE_COOKIE_SECURE", "true").lower() != "false"
