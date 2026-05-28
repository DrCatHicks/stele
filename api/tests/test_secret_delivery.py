"""One-time encrypted secret delivery: crypto primitives + the single-use reveal.

The crypto tests are pure (no DB). The reveal tests run against the transactional
fixture: they insert a SecretDelivery row and prove the password is revealed
exactly once — a second attempt, an expired row, the wrong user, or the wrong
login role all yield nothing.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import secret_delivery, service
from api.auth.models import SecretDelivery

PASSWORD = "correct-horse-battery-staple"


# --- crypto primitives (no DB) ----------------------------------------------


def test_encrypt_decrypt_round_trips() -> None:
    token = secret_delivery.encrypt("hunter2")
    assert token != "hunter2"  # actually encrypted, not passed through
    assert secret_delivery.decrypt(token) == "hunter2"


def test_encrypt_is_nondeterministic_but_decryptable() -> None:
    # Fernet embeds an IV + timestamp, so two encryptions of the same plaintext
    # differ — yet both decrypt back to it.
    a = secret_delivery.encrypt("same")
    b = secret_delivery.encrypt("same")
    assert a != b
    assert secret_delivery.decrypt(a) == secret_delivery.decrypt(b) == "same"


def test_decrypt_rejects_garbage() -> None:
    with pytest.raises(secret_delivery.SecretDeliveryError):
        secret_delivery.decrypt("not-a-valid-fernet-token")


def test_invalid_key_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STELE_ENCRYPTION_KEY", "this-is-not-a-fernet-key")
    with pytest.raises(secret_delivery.SecretDeliveryError):
        secret_delivery.encrypt("x")


# --- single-use reveal ------------------------------------------------------

ROLE = "stele_analyst_jdoe_a1b2"


async def _user(session: AsyncSession) -> int:
    user = await service.create_user(
        session, f"reveal_{secrets.token_hex(4)}@example.com", PASSWORD, ["reviewer"]
    )
    return user.id


async def _deliver(
    session: AsyncSession,
    user_id: int,
    *,
    password: str = "s3cr3t",
    login_role: str = ROLE,
    expires_in: timedelta = timedelta(hours=1),
) -> None:
    session.add(
        SecretDelivery(
            target_user_id=user_id,
            login_role=login_role,
            ciphertext=secret_delivery.encrypt(password),
            expires_at=datetime.now(UTC) + expires_in,
        )
    )
    await session.commit()


async def test_reveal_returns_password_then_wipes(db_session: AsyncSession) -> None:
    user_id = await _user(db_session)
    await _deliver(db_session, user_id, password="live-password")

    revealed = await secret_delivery.reveal_for_user(db_session, user_id, ROLE)
    assert revealed is not None
    assert revealed.login_role == ROLE
    assert revealed.password == "live-password"

    # Single-use: a second reveal finds nothing.
    assert await secret_delivery.reveal_for_user(db_session, user_id, ROLE) is None
    # has_pending_delivery agrees the secret is gone.
    assert await secret_delivery.has_pending_delivery(db_session, user_id, ROLE) is False


async def test_reveal_skips_expired(db_session: AsyncSession) -> None:
    user_id = await _user(db_session)
    await _deliver(db_session, user_id, expires_in=timedelta(seconds=-1))
    assert await secret_delivery.reveal_for_user(db_session, user_id, ROLE) is None


async def test_reveal_is_scoped_to_user_and_role(db_session: AsyncSession) -> None:
    user_id = await _user(db_session)
    other_id = await _user(db_session)
    await _deliver(db_session, user_id)

    # Wrong user can't reveal it.
    assert await secret_delivery.reveal_for_user(db_session, other_id, ROLE) is None
    # Wrong login role can't reveal it.
    assert await secret_delivery.reveal_for_user(db_session, user_id, "stele_other_x") is None
    # The rightful owner still can.
    assert await secret_delivery.reveal_for_user(db_session, user_id, ROLE) is not None
