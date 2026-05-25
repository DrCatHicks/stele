"""Tests for the production app composition (api.main:app).

The API app is mounted under ``/api``; when ``STELE_FRONTEND_DIST`` points at a
built SPA, everything else serves the SPA (real assets when they exist, else
``index.html`` so client-side routes resolve on hard refresh). These tests drive
the *composed* app directly — no DB needed, since they only touch /api/health and
the static layer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.main import create_app

INDEX_HTML = "<!doctype html><html><body><div id=root></div></body></html>"
ASSET_JS = "console.log('built asset');"


@pytest.fixture
def dist_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A built-frontend directory wired in via STELE_FRONTEND_DIST."""
    (tmp_path / "index.html").write_text(INDEX_HTML)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text(ASSET_JS)
    monkeypatch.setenv("STELE_FRONTEND_DIST", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def composed_client(dist_dir: Path) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_api_mounted_under_api_prefix(composed_client: AsyncClient) -> None:
    # The API answers under /api …
    response = await composed_client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_bare_api_path_is_not_the_api(composed_client: AsyncClient) -> None:
    # … and NOT at the bare path (which now belongs to the SPA fallback).
    response = await composed_client.get("/health")
    assert response.status_code == 200
    assert response.text == INDEX_HTML


async def test_spa_served_at_root(composed_client: AsyncClient) -> None:
    response = await composed_client.get("/")
    assert response.status_code == 200
    assert response.text == INDEX_HTML


async def test_spa_fallback_for_client_route(composed_client: AsyncClient) -> None:
    # A deep client-side route (hard refresh) gets index.html, not a 404.
    response = await composed_client.get("/admin/pii-review")
    assert response.status_code == 200
    assert response.text == INDEX_HTML


async def test_real_asset_is_served(composed_client: AsyncClient) -> None:
    response = await composed_client.get("/assets/app.js")
    assert response.status_code == 200
    assert response.text == ASSET_JS


async def test_unknown_api_path_404_not_spa(composed_client: AsyncClient) -> None:
    # An unmatched /api route must 404 from the API, never leak the SPA shell —
    # otherwise a typo'd endpoint returns 200 HTML and masks the bug.
    response = await composed_client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.text != INDEX_HTML


async def test_path_traversal_falls_back_to_index(composed_client: AsyncClient) -> None:
    # A path escaping the dist dir must not serve an arbitrary file.
    response = await composed_client.get("/../../etc/passwd")
    assert response.status_code == 200
    assert response.text == INDEX_HTML


def test_misconfigured_dist_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A dist dir with no index.html (wrong path / unbuilt frontend) must fail at
    # composition time, not 500 every request later.
    monkeypatch.setenv("STELE_FRONTEND_DIST", str(tmp_path))
    with pytest.raises(RuntimeError, match=r"no index\.html"):
        create_app()


async def test_no_spa_routes_without_dist(monkeypatch: pytest.MonkeyPatch) -> None:
    # With no built frontend configured (dev/CI), only /api is mounted; the root
    # is unclaimed rather than serving a phantom SPA.
    monkeypatch.delenv("STELE_FRONTEND_DIST", raising=False)
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/health")).status_code == 200
        assert (await c.get("/")).status_code == 404
