# M4 publish gate + branching — verification

Proves the automated publish gate (CLAUDE.md §"Publish gate", design-doc §3.6 /
FR-2) and that conditional-branching surveys keep their routing states
**distinguishable end-to-end** — shown-answered, shown-skipped and routed-past
never collapse to "missing" (invariant 3, CLAUDE.md §"silent defaults").

The gate runs in order on publish: **schema validation → lint → round-trip →
hash + freeze**. The first two land in M4.1, the round-trip oracle in M4.2,
the `patterns/` reference in M4.3; this runbook ties them together with the
routing-fidelity guarantee that was built back in M1.

Each criterion below maps to an automated test plus a reproducible command.
Builds on the same seed + warehouse as `m1-slice.md` — run its steps 1–3 first.

## 1. A dangling `visibleIf` is blocked with a clear error

The lint scans every `visibleIf` / `enableIf` for `{token}` references and
rejects any that name neither a question nor a `calculatedValue`. The reason
surfaces as the publish endpoint's 422 `detail`, so the editor shows it.

```bash
uv run pytest api/tests/test_publish_gate.py -k "dangling or visible_if or branching" -q
```

Covers: `test_dangling_visible_if_rejected` and
`test_enable_if_dangling_reference_rejected` (unit, on `validate_definition`);
`test_publish_rejects_dangling_visible_if_with_detail` (asserts `"ghost"` reaches
the 422 `detail`); `test_valid_visible_if_reference_passes` and
`test_visible_if_calculated_value_reference_passes` (no false rejects).

## 2. A valid branching survey passes the round-trip gate

The round-trip oracle (`frontend/scripts/roundTrip.mjs`, the sole module touching
survey-core) walks the enumerable `visibleIf` branch space and fails a survey
with an **unreachable** question; it is conservative — it only flags when all
drivers are enumerable and within bound, so it never false-rejects. The Python
adapter shells out: a failed round-trip → `InvalidDefinition` (422), an oracle
that can't run → `RoundTripUnavailable` (503, fail-closed).

```bash
# Needs Node + survey-core (cd frontend && npm install). Skips otherwise.
uv run pytest api/tests/test_round_trip.py -k "real_oracle" -q
```

`test_real_oracle_passes_valid_branching_survey` publishes a `{q1} = 'a'`-gated
survey through the real oracle (200); `test_real_oracle_blocks_unreachable_branch`
publishes a `{q1} = 'z'`-gated survey where `q1` has no `z` choice → 422
`"unreachable"`. The wiring tests in the same file monkeypatch the oracle and run
node-free, so the rest of the suite stays toolchain-independent.

> **CI note (honest scope):** the Python end-to-end oracle tests skip in the
> `python-test` job (no Node there); the oracle's own logic is covered by
> `frontend`'s vitest job, and the subprocess integration is verified locally
> and in this runbook. Same tradeoff documented for M4.2.

The shipped reference survey `patterns/branching.json` is itself a gate fixture —
`api/tests/test_patterns.py` runs every `patterns/*.json` through the real gate,
so the documented examples can't rot if the gate tightens.

## 3. Routing states are distinguishable in `marts`

`was_shown` comes **straight from the API-captured `shown_questions`** (invariant
3); routing is never reconstructed from `visibleIf` in SQL. So in the warehouse a
branching survey and a flat survey with the same shown-set are identical by
design — the branch decision lives at submit time, in the engine, not in dbt.
The seed's hand-set `shown_questions` therefore faithfully stand in for what the
SurveyJS engine captures from `visibleIf` at submit time.

After seeding (4 respondents) and `dbt build`, inspect `q2` — which spans all
three states — as the ETL role:

```bash
PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -c "
  SELECT fri.was_shown,
         (fri.option_key IS NOT NULL) AS answered,
         count(*)
  FROM marts.fact_response_item fri
  JOIN marts.dim_question dq ON dq.question_id = fri.question_id
  WHERE dq.stable_name = 'q2'
  GROUP BY 1, 2
  ORDER BY 1 DESC, 2 DESC;"
```

Expected — three distinct rows, never collapsed:

| was_shown | answered | count | state |
|---|---|---|---|
| `t` | `t` | 2 | shown & answered (R1→x, R2→y) |
| `t` | `f` | 1 | shown & skipped (R3) |
| `f` | `f` | 1 | routed past (R4) |

A shown-skipped cell (`was_shown=true, option_key null`) and a routed-past cell
(`was_shown=false`) are different facts about the same question — the M4
distinction. An analysis that treats both as "no answer" is choosing to; the
warehouse never makes that choice for it.

### Automated backing

Two paired singular tests pin `was_shown` to an exact bijection with presence in
the captured shown-set, so a regression in either direction fails `dbt build`:

- `shown_set_integrity` — every `was_shown=true` fact's question **is** in the
  submission's `shown_questions`.
- `routed_past_not_in_shown_set` — every `was_shown=false` fact's question is
  **absent** from it (added in M4.4). Catches a derivation that mislabels a
  shown question as routed-past — i.e. silently collapses shown-skipped into
  routed-past.

```bash
cd dbt && DBT_HOST=localhost DBT_USER=stele_etl DBT_PASSWORD=dev DBT_DBNAME=stele \
  uv run dbt build --profiles-dir . \
  --select shown_set_integrity routed_past_not_in_shown_set
```

Both must be green (`PASS=2 ... ERROR=0`).

## 4. Gate ordering and the sandbox escape hatch

- The gate runs **schema → lint → round-trip** before hash + freeze; an earlier
  stage's failure short-circuits, so the cheapest checks reject first.
- `for_real_respondents=false` skips the round-trip stage (fixtures, like the
  seed, don't need the Node toolchain). The flag is preserved across
  definition-only edits and inherited by cloned draft versions, so the gate
  decision is never silently reset — see `test_round_trip.py`
  (`test_sandbox_survey_skips_round_trip`,
  `test_definition_only_edit_preserves_sandbox_flag`,
  `test_clone_draft_version_inherits_flag`).
