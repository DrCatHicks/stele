"""The `patterns/` library is a set of *validated fixtures* (M4.3, design doc §3.6).

The annotated SurveyJS examples in `patterns/` double as researcher reference and
LLM-authoring context, so they must always be publishable by the real gate — an
example that would be rejected at publish time is worse than none. These tests run
every pattern through the actual publish gate so the library can't quietly rot:

  - `validate_definition` (schema + lint + PII) always runs — no Node needed;
  - the survey-core round-trip oracle runs when the toolchain is installed
    (same skip-guard as test_round_trip.py; CI's vitest job covers oracle logic).

If the gate tightens or the supported-type surface changes, these fail until the
patterns are brought back in line — which is the point.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.survey_engine import round_trip
from api.survey_engine.validation import validate_definition

# The autouse `_stub_round_trip` fixture (conftest) no-ops the oracle for the
# suite; capture the real adapter at import time so the round-trip check below
# exercises the genuine Node + survey-core walk.
_REAL_RUN_ROUND_TRIP = round_trip.run_round_trip

_PATTERNS_DIR = Path(__file__).resolve().parents[2] / "patterns"
_PATTERN_FILES = sorted(_PATTERNS_DIR.glob("*.json"))

# IDs keep a failure message pointing at the offending file, not an index.
_pattern = pytest.mark.parametrize(
    "pattern_path", _PATTERN_FILES, ids=[p.name for p in _PATTERN_FILES]
)


def test_patterns_dir_is_populated() -> None:
    # A glob that silently matches nothing would make every parametrized test
    # below vacuously pass — guard against an empty/moved directory.
    assert _PATTERN_FILES, f"no pattern .json files found in {_PATTERNS_DIR}"


@_pattern
def test_pattern_is_valid_json(pattern_path: Path) -> None:
    definition = json.loads(pattern_path.read_text())
    assert isinstance(definition, dict), "a pattern must be a SurveyJS definition object"


@_pattern
def test_pattern_passes_publish_lint(pattern_path: Path) -> None:
    # The synchronous gate stages (schema + lint + PII). Raises InvalidDefinition
    # on any failure, failing the test with the gate's own message.
    validate_definition(json.loads(pattern_path.read_text()))


@pytest.mark.skipif(
    not round_trip.is_available(),
    reason="Node + survey-core round-trip oracle not available",
)
@_pattern
def test_pattern_passes_round_trip(pattern_path: Path) -> None:
    # The real oracle: every branch reachable, no expression errors. Raises
    # InvalidDefinition (unreachable/expression) or RoundTripUnavailable.
    _REAL_RUN_ROUND_TRIP(json.loads(pattern_path.read_text()))
