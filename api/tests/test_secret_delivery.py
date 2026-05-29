"""One-time encrypted secret delivery: crypto primitives + the single-use reveal.

The crypto tests are pure (no DB). The reveal tests are committed integration tests
(like test_credential_worker): they seed app.secret_deliveries over the elevated
connection — stele_api may not INSERT it (§3.10) — and exercise reveal over a real,
*committing* session, not the rollback fixture. Mixing committed rows with the
transactional session deadlocks at cleanup (its open transaction holds the row
locks the elevated DELETE waits on), so these own their cleanup in a finally.
"""

from __future__ import annotations

import secrets

import psycopg
import pytest

from api.auth import secret_delivery
from api.db import SessionLocal

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


# --- single-use reveal (committed integration) ------------------------------

ROLE = "stele_analyst_jdoe_a1b2"


def _seed_user(conn: psycopg.Connection) -> int:
    row = conn.execute(
        "INSERT INTO app.users (email, password_hash) VALUES (%s, %s) RETURNING id",
        (f"reveal_{secrets.token_hex(4)}@example.com", "x"),
    ).fetchone()
    assert row is not None
    return int(row[0])


def _seed_delivery(
    conn: psycopg.Connection,
    *,
    password: str = "s3cr3t",
    login_role: str = ROLE,
    expires_in_seconds: int = 3600,
) -> int:
    user_id = _seed_user(conn)
    conn.execute(
        "INSERT INTO app.secret_deliveries (target_user_id, login_role, ciphertext, expires_at) "
        "VALUES (%s, %s, %s, now() + make_interval(secs => %s))",
        (user_id, login_role, secret_delivery.encrypt(password), expires_in_seconds),
    )
    return user_id


def _cleanup(conn: psycopg.Connection, *user_ids: int) -> None:
    for user_id in user_ids:
        conn.execute("DELETE FROM app.users WHERE id = %s", (user_id,))  # cascades the delivery


async def test_reveal_returns_password_then_wipes(elevated_conn: psycopg.Connection) -> None:
    user_id = _seed_delivery(elevated_conn, password="live-password")
    try:
        async with SessionLocal() as session:
            revealed = await secret_delivery.reveal_for_user(session, user_id, ROLE)
            assert revealed is not None
            assert revealed.login_role == ROLE
            assert revealed.password == "live-password"

            # Single-use: a second reveal finds nothing.
            assert await secret_delivery.reveal_for_user(session, user_id, ROLE) is None
            assert await secret_delivery.has_pending_delivery(session, user_id, ROLE) is False
    finally:
        _cleanup(elevated_conn, user_id)


async def test_reveal_skips_expired(elevated_conn: psycopg.Connection) -> None:
    user_id = _seed_delivery(elevated_conn, expires_in_seconds=-1)
    try:
        async with SessionLocal() as session:
            assert await secret_delivery.reveal_for_user(session, user_id, ROLE) is None
    finally:
        _cleanup(elevated_conn, user_id)


async def test_reveal_is_scoped_to_user_and_role(elevated_conn: psycopg.Connection) -> None:
    user_id = _seed_delivery(elevated_conn)
    other_id = _seed_user(elevated_conn)
    try:
        async with SessionLocal() as session:
            # Wrong user can't reveal it.
            assert await secret_delivery.reveal_for_user(session, other_id, ROLE) is None
            # Wrong login role can't reveal it.
            assert await secret_delivery.reveal_for_user(session, user_id, "stele_other_x") is None
            # The rightful owner still can.
            assert await secret_delivery.reveal_for_user(session, user_id, ROLE) is not None
    finally:
        _cleanup(elevated_conn, user_id, other_id)
