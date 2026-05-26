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
#   migrate  STELE_DATABASE_URL (the admin identity that also bootstraps roles)
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
