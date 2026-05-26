#!/usr/bin/env bash
#
# Entrypoint dispatcher for the production image (M7.2). Maps a short verb to the
# real process and exec's it, so the chosen process becomes PID 1 and receives
# signals directly (clean shutdown on SIGTERM from the orchestrator).
#
#   web       uvicorn serving the composed app (API under /api + SPA at /)
#   migrate   alembic upgrade head
#   etl       the logged dbt build (scripts/run_etl.py)
#
# Trailing arguments pass through, e.g. `etl -- --select dim_question`.
#
# Connection/secret env (supplied by the deploy, not baked in):
#   web      STELE_DATABASE_URL (stele_api role), STELE_SESSION_SECRET, STELE_COOKIE_SECURE
#   migrate  the admin identity that also bootstraps roles, on
#            STELE_ADMIN_DATABASE_URL (preferred) or STELE_DATABASE_URL — so a deploy
#            that runs migrate as a pre-deploy step in the *web* service can keep
#            STELE_DATABASE_URL=stele_api for the web process and set the admin
#            connection only on STELE_ADMIN_DATABASE_URL. Dev/CI set just
#            STELE_DATABASE_URL (one admin identity). Both bootstrap_roles.py and
#            alembic resolve this precedence, so bootstrap-er still == migrator.
#            STELE_{API,ETL,ANALYST,PII_REVIEWER}_PASSWORD on the FIRST deploy so
#            bootstrap_roles.py can create the four roles (re-deploys need none);
#            STELE_SKIP_BOOTSTRAP=1 to skip when roles are managed out of band
#   etl      DBT_HOST/DBT_USER/DBT_PASSWORD/DBT_DBNAME + STELE_ETL_DATABASE_URL;
#            GIT_SHA for run provenance (no git binary in the image — baked at build)
set -euo pipefail

APP_DIR="${STELE_APP_DIR:-/app}"
API_DIR="${STELE_API_DIR:-/app/api}"
PORT="${PORT:-8000}"

cmd="${1:-web}"
if [[ $# -gt 0 ]]; then shift; fi

case "$cmd" in
  web)
    cd "$APP_DIR"
    set -- uvicorn api.main:app --host 0.0.0.0 --port "$PORT" "$@"
    ;;
  migrate)
    # Bootstrap roles, schemas, and grants as THIS (admin) identity *before*
    # migrating, so the grant SQL's ALTER DEFAULT PRIVILEGES — which is
    # grantor-specific — reaches the tables alembic creates in the same run
    # (bootstrap-er must equal migrator). Idempotent and prod-only; skipped in the
    # print-mode test hook (no DB) and via STELE_SKIP_BOOTSTRAP=1 when roles are
    # managed out of band. Runs from the app root: bootstrap_roles.py reads the
    # shared grant SQL by a repo-relative path. set -e fails the migrate closed if
    # bootstrap fails, before any schema change.
    if [[ -z "${STELE_ENTRYPOINT_PRINT:-}" && "${STELE_SKIP_BOOTSTRAP:-}" != "1" ]]; then
      ( cd "$APP_DIR" && python scripts/bootstrap_roles.py )
    fi
    # alembic.ini sets script_location via %(here)s (absolute), so cwd only
    # affects prepend_sys_path; cd into api/ to mirror the dev/CI invocation.
    cd "$API_DIR"
    set -- alembic upgrade head "$@"
    ;;
  etl)
    # run_etl.py reads git_sha from the repo root and sets dbt's own cwd, so it
    # must run from the app root (mirrors the CI step).
    cd "$APP_DIR"
    set -- python scripts/run_etl.py "$@"
    ;;
  *)
    echo "docker-entrypoint: unknown command '$cmd' (expected: web | migrate | etl)" >&2
    exit 64
    ;;
esac

if [[ -n "${STELE_ENTRYPOINT_PRINT:-}" ]]; then
  # Inspection/test hook: report the resolved working dir + argv instead of
  # exec'ing. Lets the dispatcher be unit-tested without Docker or a real venv.
  printf 'cwd=%s argv=%s\n' "$(pwd)" "$*"
  exit 0
fi

exec "$@"
