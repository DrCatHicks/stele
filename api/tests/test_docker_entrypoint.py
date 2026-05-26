"""Tests for the production image's entrypoint dispatcher (M7.2).

The dispatcher (scripts/docker-entrypoint.sh) maps a short verb — web / migrate /
etl — to the real process it exec's in the container. We can't build the image
here, but the dispatch *logic* is the part that breaks silently (wrong module
path, dropped pass-through args, a typo'd verb that exit-codes wrong), so we drive
the script directly with its STELE_ENTRYPOINT_PRINT hook, which reports the
resolved working dir + argv instead of exec'ing. STELE_APP_DIR / STELE_API_DIR
point the cd targets at a real temp dir so the script runs anywhere.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "docker-entrypoint.sh"


def _run(
    *args: str, app_dir: Path, api_dir: Path | None = None, port: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke the dispatcher in print mode. app_dir backs the web/etl cd target;
    api_dir (defaulting to app_dir) backs the migrate cd target — pass them
    distinct to pin which verb lands where."""
    env = {
        "STELE_ENTRYPOINT_PRINT": "1",
        "STELE_APP_DIR": str(app_dir),
        "STELE_API_DIR": str(api_dir if api_dir is not None else app_dir),
        "PATH": "/usr/bin:/bin",
    }
    if port is not None:
        env["PORT"] = port
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_default_command_is_web(tmp_path: Path) -> None:
    # No argument → web (the CMD default in the image).
    result = _run(app_dir=tmp_path)
    assert result.returncode == 0
    assert "argv=uvicorn api.main:app --host 0.0.0.0 --port 8000" in result.stdout
    assert f"cwd={tmp_path}" in result.stdout


def test_web_honors_port_env(tmp_path: Path) -> None:
    # The orchestrator injects $PORT; uvicorn must bind it.
    result = _run("web", app_dir=tmp_path, port="3000")
    assert result.returncode == 0
    assert "--port 3000" in result.stdout


def test_migrate_runs_alembic_in_api_dir(tmp_path: Path) -> None:
    # Distinct app/api dirs pin that migrate cd's to the *api* dir, not the app
    # root (alembic.ini lives in api/) — mirrors the dev/CI invocation.
    app_dir = tmp_path / "app"
    api_dir = tmp_path / "app" / "api"
    api_dir.mkdir(parents=True)
    result = _run("migrate", app_dir=app_dir, api_dir=api_dir)
    assert result.returncode == 0
    assert "argv=alembic upgrade head" in result.stdout
    assert f"cwd={api_dir}" in result.stdout
    assert f"cwd={app_dir}\n" not in result.stdout


def test_etl_runs_the_logged_runner(tmp_path: Path) -> None:
    result = _run("etl", app_dir=tmp_path)
    assert result.returncode == 0
    assert "argv=python scripts/run_etl.py" in result.stdout


def test_trailing_args_pass_through(tmp_path: Path) -> None:
    # `etl -- --select dim_question` must forward the dbt selector untouched.
    result = _run("etl", "--", "--select", "dim_question", app_dir=tmp_path)
    assert result.returncode == 0
    assert "argv=python scripts/run_etl.py -- --select dim_question" in result.stdout


def test_unknown_command_fails_loud(tmp_path: Path) -> None:
    result = _run("frobnicate", app_dir=tmp_path)
    assert result.returncode == 64  # EX_USAGE
    assert "unknown command 'frobnicate'" in result.stderr


@pytest.mark.parametrize("verb", ["web", "migrate", "etl"])
def test_known_verbs_exit_zero_in_print_mode(verb: str, tmp_path: Path) -> None:
    assert _run(verb, app_dir=tmp_path).returncode == 0
