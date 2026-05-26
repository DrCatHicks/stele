# syntax=docker/dockerfile:1
#
# Production image for the survey engine (M7.2). One image, three entrypoints:
#
#   docker run … web        # uvicorn serving the API under /api + the SPA at /
#   docker run … migrate    # alembic upgrade head
#   docker run … etl        # the logged dbt build (scripts/run_etl.py)
#
# (Dispatcher: scripts/docker-entrypoint.sh.)
#
# Why a Node base for a mostly-Python service: the publish gate drives synthetic
# respondents through the real SurveyJS engine via a Node oracle
# (frontend/scripts/roundTrip.mjs → survey-core). That oracle runs *at request
# time* whenever a researcher publishes a real-respondent survey, so the runtime
# needs Node + survey-core present or publishing fails closed (503). Rather than
# bolt Node onto a Python image, we start from Node and let uv supply + manage
# Python 3.12 and every Python dependency (including dbt). One base, no fragile
# cross-image binary copying.

# ---- Stage 1: build the SPA -------------------------------------------------
# Produces the static bundle FastAPI serves, and the node_modules the oracle
# imports at runtime (carried into the runtime stage wholesale — see below).
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /build/frontend
RUN corepack enable

# Install deps first so this layer caches on the lockfile, not on source edits.
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

# ---- Stage 2: runtime -------------------------------------------------------
FROM node:20-bookworm-slim AS runtime

# uv provides + manages Python and installs all Python deps. Using :latest for
# the uv binary itself is low-risk — uv.lock pins every dependency and we sync
# with --frozen, so uv only reads the lock, never re-resolves. (Pin to a specific
# uv tag once the first green build in M7.4 confirms a working one.)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# UV_PYTHON=3.12           pin the minor version. The Node base has no system
#                          Python, so uv downloads a managed one; left to >=3.12
#                          (pyproject) it grabs the newest (3.14), where dbt's
#                          mashumaro fails to import. Dev + CI run on system 3.12;
#                          pin so the image matches them.
# UV_PYTHON_INSTALL_DIR    where uv puts the Python it downloads
# UV_PROJECT_ENVIRONMENT   the venv uv sync builds; we put its bin on PATH so
#                          uvicorn/alembic/dbt/python resolve without `uv run`
# UV_COMPILE_BYTECODE      precompile .pyc at build time (faster cold start)
# UV_LINK_MODE=copy        copy from the cache into the venv (no hardlink warning
#                          across filesystems in the image)
ENV UV_PYTHON=3.12 \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependency layer: keyed on the lockfiles only, so source edits don't bust it.
# --no-install-project installs deps (and downloads Python 3.12) but not the
# `stele` package itself — that needs api/, copied below. --no-dev drops
# pytest/ruff/mypy. --frozen fails if uv.lock is stale.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App source. api/ lands at /app/api so round_trip.py's parents[2] resolves to
# /app and finds /app/frontend + /app/dbt; dbt/ + scripts/ back the etl
# entrypoint; api/alembic backs migrate.
COPY api/ ./api/
COPY dbt/ ./dbt/
COPY scripts/ ./scripts/
# The shared schemas+grants SQL that the migrate entrypoint's bootstrap step
# (scripts/bootstrap_roles.py) applies. It resolves this by its repo-relative
# path, so copy it to the same place under /app. Only the shared file is needed
# in prod — 01-roles.sql creates roles with the throwaway dev password and is
# dev/CI only (prod creates roles from secrets).
COPY .devcontainer/postgres-init/02-schemas-and-grants.sql ./.devcontainer/postgres-init/02-schemas-and-grants.sql

# Install the project now that api/ exists. Dev and CI run scripts via `uv run`
# with the project installed, so `import api` resolves from site-packages; match
# that here (rather than leaning on cwd) so scripts/run_etl.py behaves identically.
RUN uv sync --frozen --no-dev

# Built SPA + the oracle's runtime deps. All under /app/frontend so the FastAPI
# StaticFiles mount (STELE_FRONTEND_DIST) and round_trip.py both find them.
# node_modules is copied whole (carries survey-core); roundTrip.mjs is the oracle.
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
COPY --from=frontend-build /build/frontend/node_modules ./frontend/node_modules
COPY --from=frontend-build /build/frontend/scripts ./frontend/scripts
COPY --from=frontend-build /build/frontend/package.json ./frontend/package.json

# The SPA is always present in this image, so serve it by default. A deploy can
# still override (unset it) to run API-only.
ENV STELE_FRONTEND_DIST=/app/frontend/dist

# Build-time provenance. The image carries no .git and no git binary, so the ETL
# runner's `git rev-parse` fails and falls back to $GIT_SHA — pass the commit sha
# at build time (e.g. --build-arg GIT_SHA=$RAILWAY_GIT_COMMIT_SHA) so ops.etl_runs
# records real provenance instead of NULL.
ARG GIT_SHA=""
ENV GIT_SHA=${GIT_SHA}

COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Drop to the unprivileged `node` user (uid 1000, present in the base image): the
# app needs no root at runtime. The etl entrypoint writes under
# /app/dbt/{target,etl_artifacts}, so hand /app to that user. The venv and the
# uv-managed Python (/opt/uv/python) are world-readable/executable, so they stay
# root-owned.
RUN chown -R node:node /app
USER node

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["web"]
