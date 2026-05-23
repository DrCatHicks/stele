"""Password hashing with argon2id (design doc §3.10).

A thin wrapper over argon2-cffi's PasswordHasher with library defaults, which
select the argon2id variant. ``verify_password`` returns a bool and never
raises on a wrong password, so callers branch on the result rather than catching
exceptions in the hot login path.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False
