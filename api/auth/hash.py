"""Password hashing with argon2id (design doc §3.10).

A thin wrapper over argon2-cffi's PasswordHasher with library defaults, which
select the argon2id variant. ``verify_password`` returns a bool for any bad
input — wrong password OR a malformed/corrupt stored hash — so the login path
branches on the result and never turns a failed auth into a 500. (InvalidHashError
is not a subclass of VerificationError, so it must be caught explicitly.)
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
