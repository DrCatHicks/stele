"""FastAPI application entrypoint.

Two apps live here. ``api_app`` carries every route (auth, surveys, admin, …) at
its own prefix and is what the test suite drives directly. ``app`` is the
production composition: it mounts ``api_app`` under ``/api`` and — when a built
frontend is configured via ``STELE_FRONTEND_DIST`` — serves the SurveyJS SPA from
the same origin for everything else.

Serving both from one origin (rather than split subdomains) is deliberate: the
session cookie stays same-site with no CORS, and the SPA's ``/admin/*`` routes no
longer collide with the API's ``/admin/*`` endpoints because the API now lives
under ``/api`` exclusively. In dev, ``STELE_FRONTEND_DIST`` is unset and Vite
serves the SPA + proxies ``/api`` to this process (see frontend/vite.config.ts).
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from sqlalchemy import text

from api.auth.provisioning_router import router as db_credentials_router
from api.auth.router import router as auth_router
from api.db import SessionDep
from api.survey_engine.gdpr_router import router as gdpr_router
from api.survey_engine.pii_review_router import router as pii_review_router
from api.survey_engine.respondents_router import router as respondents_router
from api.survey_engine.router import router as surveys_router


def create_api_app() -> FastAPI:
    """Build the API application. Routes keep their bare prefixes; the ``/api``
    namespace is applied by the mount in :func:`create_app`, so tests can drive
    this app directly without restating the prefix on every path."""
    api = FastAPI(title="Survey Engine API")
    api.include_router(auth_router)
    api.include_router(db_credentials_router)
    api.include_router(surveys_router)
    api.include_router(respondents_router)
    api.include_router(gdpr_router)
    api.include_router(pii_review_router)

    @api.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/surveys/count")
    async def surveys_count(session: SessionDep) -> dict[str, int]:
        result = await session.execute(text("SELECT count(*) FROM app.survey_definitions"))
        return {"count": int(result.scalar_one())}

    return api


def _serve_spa(root: FastAPI, dist_dir: Path) -> None:
    """Serve the built SPA from ``dist_dir`` for any non-``/api`` GET.

    A real built asset (``/assets/index-*.js``, ``/favicon.ico``) is returned
    when the file exists; otherwise ``index.html`` is handed back so client-side
    routes (e.g. a hard refresh on ``/admin/pii-review``) resolve. The ``/api``
    mount is registered first, so it always wins over this catch-all."""
    dist = dist_dir.resolve()
    index = dist / "index.html"
    # Fail loud at startup on a misconfigured dist (wrong path, or a frontend that
    # was never built) rather than 500ing every request later with a traceback.
    if not index.is_file():
        raise RuntimeError(
            f"STELE_FRONTEND_DIST={dist_dir!r} has no index.html "
            f"(resolved {index}); build the frontend or fix the path."
        )

    @root.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        candidate = (dist / full_path).resolve()
        # is_relative_to guards against path traversal (../../etc/passwd).
        if full_path and candidate.is_file() and candidate.is_relative_to(dist):
            return FileResponse(candidate)
        return FileResponse(index)


def create_app() -> FastAPI:
    """Production composition: API under ``/api`` + (optionally) the SPA at ``/``."""
    root = FastAPI(title="Survey Engine")
    root.mount("/api", api_app)
    dist = os.environ.get("STELE_FRONTEND_DIST")
    if dist:
        _serve_spa(root, Path(dist))
    return root


# Module-level singletons. ``api_app`` is what the test suite imports and drives;
# ``app`` is what uvicorn serves in dev and prod.
api_app = create_api_app()
app = create_app()
