"""Encrypted, one-time delivery of a freshly-minted DB password (design doc §3.10).

The UI provisioning flow has no terminal to print a password to (unlike the CLI's
``/dev/tty``), so the worker stores the password here — Fernet-encrypted, never
plaintext — and the recipient reveals it exactly once from their own authenticated
session, after which the ciphertext is wiped. The password therefore exists in the
database only between the worker minting it and the recipient's first reveal, and
only as ciphertext under a key (``STELE_ENCRYPTION_KEY``) the database never holds.

Two halves with different trust levels, like ``provisioning.py``:
- crypto primitives + the worker's *sync* write (psycopg, inside the DDL tx);
- the *async* single-use reveal path for the API (the stele_api session).
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.models import SecretDelivery

_ENCRYPTION_KEY_ENV = "STELE_ENCRYPTION_KEY"
# Loud, deterministic dev-only fallback (mirrors config.session_secret). Production
# MUST set STELE_ENCRYPTION_KEY to a real Fernet key (urlsafe-base64 of 32 bytes);
# a leaked key lets an attacker decrypt any unrevealed delivery row.
_DEV_KEY = base64.urlsafe_b64encode(
    hashlib.sha256(b"stele-dev-insecure-encryption-key-change-me").digest()
)
# An unrevealed secret past this TTL can no longer be revealed; the recipient asks
# for a regenerate (rotate), which mints a fresh delivery.
DELIVERY_TTL = timedelta(hours=24)


class SecretDeliveryError(Exception):
    """Encryption is misconfigured, or a stored secret could not be decrypted."""


def _fernet() -> Fernet:
    """Build the Fernet cipher from the env key (or the dev fallback).

    Read at call time, not import, so tests and deployments can set the key after
    this module is imported (matching config.session_secret).
    """
    raw = os.environ.get(_ENCRYPTION_KEY_ENV)
    key: bytes = raw.encode() if raw is not None else _DEV_KEY
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise SecretDeliveryError(
            f"{_ENCRYPTION_KEY_ENV} is not a valid Fernet key (urlsafe-base64 of 32 bytes): {exc}"
        ) from exc


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretDeliveryError("stored secret could not be decrypted") from exc


def store_secret_in_tx(
    conn: psycopg.Connection,
    *,
    target_user_id: int,
    login_role: str,
    password: str,
) -> None:
    """Insert the encrypted one-time delivery (worker side, sync).

    Call inside the worker's DDL transaction so the role, its registry row, and
    this delivery commit together — fail-closed, never a role whose password no
    one can retrieve.
    """
    conn.execute(
        "INSERT INTO app.secret_deliveries "
        "(target_user_id, login_role, ciphertext, expires_at) "
        "VALUES (%s, %s, %s, now() + make_interval(secs => %s))",
        (target_user_id, login_role, encrypt(password), int(DELIVERY_TTL.total_seconds())),
    )


@dataclass(frozen=True)
class RevealedSecret:
    login_role: str
    password: str


async def reveal_for_user(
    session: AsyncSession, user_id: int, login_role: str
) -> RevealedSecret | None:
    """Reveal a user's pending credential password exactly once, then wipe it.

    Returns None when there is nothing to reveal: no pending delivery for this
    (user, login_role), or it was already consumed or has expired. Single-use is
    enforced by selecting the row FOR UPDATE and, in the same transaction, nulling
    its ciphertext and stamping consumed_at — so a second call finds nothing.
    """
    now = datetime.now(UTC)
    row = (
        await session.execute(
            select(SecretDelivery)
            .where(
                SecretDelivery.target_user_id == user_id,
                SecretDelivery.login_role == login_role,
                SecretDelivery.consumed_at.is_(None),
                SecretDelivery.ciphertext.is_not(None),
                SecretDelivery.expires_at > now,
            )
            .order_by(SecretDelivery.created_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    assert row.ciphertext is not None  # guaranteed by the ciphertext IS NOT NULL filter
    password = decrypt(row.ciphertext)
    row.consumed_at = now
    row.ciphertext = None
    await session.commit()
    return RevealedSecret(login_role=row.login_role, password=password)


async def has_pending_delivery(session: AsyncSession, user_id: int, login_role: str) -> bool:
    """Whether a still-revealable delivery exists for this (user, login_role)."""
    row = (
        await session.execute(
            select(SecretDelivery.id).where(
                SecretDelivery.target_user_id == user_id,
                SecretDelivery.login_role == login_role,
                SecretDelivery.consumed_at.is_(None),
                SecretDelivery.ciphertext.is_not(None),
                SecretDelivery.expires_at > datetime.now(UTC),
            )
        )
    ).first()
    return row is not None
