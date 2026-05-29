"""Privileged worker that drains the DB-credential provisioning outbox (§3.10).

The whole point of §3.10 is that ``stele_api`` has no role-DDL privilege. So the
API never mints a Postgres login; it INSERTs a row into ``app.provision_requests``
and this worker — a *separate* process holding an elevated connection (CREATEROLE
+ ADMIN OPTION on the group roles, via ``STELE_PROVISION_DATABASE_URL``) — drains
the queue and runs the ``CREATE ROLE`` / ``GRANT``. The worker has no inbound
network surface: it is driven entirely by the outbox, never called.

Each request is claimed ``FOR UPDATE SKIP LOCKED`` and handled in one transaction:
the DDL + registry row + (for provision/rotate) the encrypted one-time secret all
commit together, or — on any failure — the work is rolled back and the row is
marked ``failed`` with the reason, so a poison request can't wedge the queue.

The role DDL itself lives in ``api.auth.provisioning`` (shared verbatim with the
operator CLI, so the two never drift); the encrypted password handoff lives in
``api.auth.secret_delivery``.

Run continuously (the Railway worker service) or once (``--once``, for cron/tests):

    python -m api.credential_worker            # poll loop
    python -m api.credential_worker --once     # process one request, then exit
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from dataclasses import dataclass

import psycopg

from api.auth import provisioning, secret_delivery

log = logging.getLogger("stele.credential_worker")

# Default seconds between empty polls. Short enough that an admin's grant feels
# near-immediate; LISTEN/NOTIFY is a later optimization if this proves too chatty.
_DEFAULT_POLL_INTERVAL = 2.0


@dataclass(frozen=True)
class _Request:
    id: int
    action: str
    access: str | None
    subject_label: str | None
    target_user_id: int | None
    requested_by: int | None
    login_role: str | None


def _claim_next(conn: psycopg.Connection) -> _Request | None:
    """Lock and return the oldest pending request, or None if the queue is empty.

    FOR UPDATE SKIP LOCKED so a second worker (or a retry) never double-processes
    a row; the lock is held until the surrounding transaction ends.
    """
    row = conn.execute(
        "SELECT id, action, access, subject_label, target_user_id, requested_by, login_role "
        "FROM app.provision_requests WHERE status = 'pending' "
        "ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1"
    ).fetchone()
    return _Request(*row) if row is not None else None


def _do_provision(conn: psycopg.Connection, req: _Request) -> str:
    if req.access is None or req.subject_label is None or req.target_user_id is None:
        raise provisioning.ProvisioningError(
            "provision request needs access, subject_label, and target_user_id"
        )
    group_role = provisioning.group_role_for(req.access)
    if not provisioning.role_exists(conn, group_role):
        raise provisioning.ProvisioningError(f"group role {group_role!r} does not exist")
    if provisioning.active_credential(conn, req.subject_label, req.access) is not None:
        raise provisioning.ProvisioningError(
            f"{req.subject_label!r} already has an active {req.access} credential"
        )
    password = provisioning.generate_password()
    login_role = provisioning.provision_in_tx(
        conn, req.access, req.subject_label, password=password, provisioned_by=req.requested_by
    )
    secret_delivery.store_secret_in_tx(
        conn, target_user_id=req.target_user_id, login_role=login_role, password=password
    )
    return login_role


def _do_rotate(conn: psycopg.Connection, req: _Request) -> str:
    if req.login_role is None or req.target_user_id is None:
        raise provisioning.ProvisioningError("rotate request needs login_role and target_user_id")
    row = conn.execute(
        "SELECT status FROM app.db_credential_grants WHERE login_role = %s",
        (req.login_role,),
    ).fetchone()
    if row is None or row[0] != "active":
        raise provisioning.ProvisioningError(f"no active credential for {req.login_role!r}")
    password = provisioning.rotate_in_tx(conn, req.login_role)
    secret_delivery.store_secret_in_tx(
        conn, target_user_id=req.target_user_id, login_role=req.login_role, password=password
    )
    return req.login_role


def _do_revoke(conn: psycopg.Connection, req: _Request) -> str:
    if req.login_role is None:
        raise provisioning.ProvisioningError("revoke request needs login_role")
    provisioning.revoke_in_tx(conn, req.login_role)
    return req.login_role


def _process_action(conn: psycopg.Connection, req: _Request) -> str:
    """Execute one request's DDL; return the login role acted on."""
    if req.action == "provision":
        return _do_provision(conn, req)
    if req.action == "rotate":
        return _do_rotate(conn, req)
    if req.action == "revoke":
        return _do_revoke(conn, req)
    raise provisioning.ProvisioningError(f"unknown action {req.action!r}")


def _finish(
    conn: psycopg.Connection,
    request_id: int,
    *,
    status: str,
    login_role: str | None,
    error: str | None,
) -> None:
    conn.execute(
        "UPDATE app.provision_requests "
        "SET status = %s, login_role = COALESCE(%s, login_role), "
        "    error_detail = %s, processed_at = now() "
        "WHERE id = %s",
        (status, login_role, error, request_id),
    )


def run_once(conn: psycopg.Connection) -> bool:
    """Claim and process one pending request. Returns True if it did work.

    The DDL runs in a savepoint nested in the claim's transaction: on failure the
    savepoint rolls back (no half-created role) while the outer transaction still
    commits the ``failed`` status, so a permanently-failing request is recorded
    once and never re-claimed.
    """
    with conn.transaction():
        req = _claim_next(conn)
        if req is None:
            return False
        try:
            with conn.transaction():
                acted = _process_action(conn, req)
        except Exception as exc:  # any failure marks the row; the loop must not die
            log.warning("provision request %d failed: %s", req.id, exc)
            _finish(conn, req.id, status="failed", login_role=None, error=str(exc))
        else:
            log.info("provision request %d done (%s %s)", req.id, req.action, acted)
            _finish(conn, req.id, status="done", login_role=acted, error=None)
    return True


def run_forever(
    conn: psycopg.Connection,
    *,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    stop: threading.Event | None = None,
) -> None:
    """Drain the queue, then poll for more until ``stop`` is set."""
    stop = stop or threading.Event()
    while not stop.is_set():
        if not run_once(conn):
            stop.wait(poll_interval)


def _install_signal_handlers(stop: threading.Event) -> None:
    def _handle(signum: int, _frame: object) -> None:
        log.info("received signal %d; shutting down after the current request", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def _run_loop(conninfo: str, *, poll_interval: float) -> None:
    """Long-running loop with reconnect, for the always-on worker service."""
    stop = threading.Event()
    _install_signal_handlers(stop)
    while not stop.is_set():
        try:
            with psycopg.connect(conninfo) as conn:
                run_forever(conn, poll_interval=poll_interval, stop=stop)
        except psycopg.OperationalError as exc:
            log.warning("worker DB connection lost: %s; reconnecting", exc)
            stop.wait(poll_interval)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DB-credential provisioning worker")
    parser.add_argument(
        "--once", action="store_true", help="process one pending request and exit (cron/tests)"
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=_DEFAULT_POLL_INTERVAL,
        help=f"seconds between empty polls (default {_DEFAULT_POLL_INTERVAL})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _build_parser().parse_args(argv)
    conninfo = provisioning.provision_conninfo()
    if args.once:
        with psycopg.connect(conninfo) as conn:
            run_once(conn)
        return 0
    _run_loop(conninfo, poll_interval=args.poll_interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
