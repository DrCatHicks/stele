"""Publish-gate round-trip oracle adapter (M4.2, design doc §3.6 / FR-2).

The round-trip stage drives synthetic respondents through the real survey-core
engine across the survey's branches and fails publication if routing is broken.
The engine is JavaScript, so the actual walk lives in a thin Node oracle
(frontend/scripts/roundTrip.mjs) — the single module that touches survey-core.
This adapter shells out to it, reusing the frontend's installed survey-core so
the gate matches exactly what respondents run; it never re-implements visibleIf
in Python (that would drift from the runtime — the failure mode CLAUDE.md warns
against for SQL).

Two failure modes, kept distinct:
  - the definition fails the round-trip (unreachable question, expression error)
    → InvalidDefinition, a definition problem the author must fix (router → 422);
  - the oracle could not run (no Node, crash, timeout) → RoundTripUnavailable, an
    operational problem (router → 503). For a survey flagged for real
    respondents we fail closed rather than silently skipping the gate.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from api.survey_engine.validation import InvalidDefinition

# The oracle resolves survey-core from frontend/node_modules, so run it with the
# frontend as the working directory. Paths are relative to the repo root
# (api/survey_engine/round_trip.py → parents[2] == repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIR = _REPO_ROOT / "frontend"
_ORACLE_SCRIPT = _FRONTEND_DIR / "scripts" / "roundTrip.mjs"
_NODE_BIN = os.environ.get("STELE_ROUND_TRIP_NODE", "node")
_TIMEOUT_SECONDS = 30


class RoundTripUnavailable(Exception):
    """The round-trip oracle could not be executed (Node missing, crash, timeout)."""


def run_round_trip(definition: dict[str, Any]) -> None:
    """Run the headless round-trip gate over a definition.

    Returns None when the survey passes. Raises InvalidDefinition when the
    survey fails the round-trip, or RoundTripUnavailable when the oracle itself
    could not run.
    """
    if not _ORACLE_SCRIPT.exists():
        raise RoundTripUnavailable(f"round-trip oracle not found at {_ORACLE_SCRIPT}")

    try:
        # Safe: fixed argv (node binary + our script), shell=False, and the
        # operator-authored definition is passed on stdin, never as a shell arg.
        proc = subprocess.run(  # noqa: S603
            [_NODE_BIN, str(_ORACLE_SCRIPT)],
            input=json.dumps(definition),
            cwd=str(_FRONTEND_DIR),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise RoundTripUnavailable(
            f"node executable {_NODE_BIN!r} not found; cannot run the round-trip gate"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RoundTripUnavailable("round-trip gate timed out") from exc

    if proc.returncode != 0:
        # Cap stderr: it can be a long Node stack with absolute paths, and this
        # message surfaces in the 503 response (operator-only, but keep it tidy).
        detail = proc.stderr.strip()[:300]
        raise RoundTripUnavailable(f"round-trip oracle exited {proc.returncode}: {detail}")

    try:
        verdict = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RoundTripUnavailable(
            f"round-trip oracle returned non-JSON output: {proc.stdout[:200]!r}"
        ) from exc

    if not verdict.get("ok"):
        errors = verdict.get("errors") or ["round-trip validation failed"]
        raise InvalidDefinition("; ".join(errors))


def is_available() -> bool:
    """True only when the oracle can actually run: the script, a Node binary on
    PATH, AND the frontend's survey-core install (the oracle imports it). Node is
    present on most CI runners even without the frontend deps, so checking for
    survey-core is what lets the end-to-end tests skip cleanly there."""
    import shutil

    survey_core = _FRONTEND_DIR / "node_modules" / "survey-core"
    return _ORACLE_SCRIPT.exists() and survey_core.exists() and shutil.which(_NODE_BIN) is not None
