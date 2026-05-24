# M5 question-type breadth — verification

Proves the non-scalar question types land in the star schema **without collapsing
the grain**: a multi-select, ranked, matrix, or repeating-group answer fans out to
the right fact rows, each routing state stays distinguishable, and free-text inside
a repeating group is still screened per answer. Every new type is wired in the
three places CLAUDE.md requires — runtime (publish gate), round-trip oracle, and
dbt staging — and pinned by a singular test.

| Story | Type | Fact-grain effect |
|---|---|---|
| M5.1 | `checkbox` (multi-select) | one `option_key` row per chosen option |
| M5.2 | `ranking` | one `option_key` row per ranked option, each with a `rank` |
| M5.3 | `matrix` / `matrixdropdown` | one sub-question per cell (`m.row[.col]`) |
| M5.4 | `paneldynamic` (repeating group) | one sub-question per template element (`panel.element`), repeated per `occurrence` |

Scalar types (`rating`, `boolean`, numeric, date) are still rejected at publish —
they need the dead `value_numeric` / `value_date` columns and land in the scalar M5
story. The publish gate proves that: `test_not_yet_wired_types_rejected`.

Builds on the same seed + warehouse as `m1-slice.md`. Run its steps first, or just:

```bash
uv run python scripts/seed_example_survey.py
cd dbt && DBT_HOST=localhost DBT_USER=stele_etl DBT_PASSWORD=dev DBT_DBNAME=stele \
  uv run dbt build --profiles-dir .
```

A fresh CI database gives the exact counts in the seed's module docstring; a local
dev DB carrying earlier surveys will show larger totals, so the queries below scope
to a single question.

## 1. Multi-select fans out, one row per selection (M5.1)

`q3` is a checkbox; R1 picked `red`+`blue`, R2 picked `green`. The answer array
fans out in `int_response_selections` (one row per element, `WITH ORDINALITY`) so
each selection is its own `option_key` fact row — counts of selections, never of
respondents.

```bash
PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -c "
  SELECT o.value, count(*)
  FROM marts.fact_response_item fri
  JOIN marts.dim_question dq ON dq.question_id = fri.question_id
  JOIN marts.dim_option o ON o.option_key = fri.option_key
  WHERE dq.stable_name = 'q3'
  GROUP BY 1 ORDER BY 1;"
```

Expected: `blue 1 · green 1 · red 1`. Automated: `option_fact_row_count_parity`
(every closed-ended selection ↔ exactly one fact `option_key`).

## 2. Ranking carries a contiguous rank (M5.2)

`q4` is a ranking; the array order is the rank (position 1 = top). `rank` is
populated only on ranking rows and forms a contiguous `1..N` per respondent.

```bash
PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -c "
  SELECT o.value, fri.rank, count(*)
  FROM marts.fact_response_item fri
  JOIN marts.dim_question dq ON dq.question_id = fri.question_id
  JOIN marts.dim_option o ON o.option_key = fri.option_key
  WHERE dq.stable_name = 'q4'
  GROUP BY 1, 2 ORDER BY 1, 2;"
```

Automated: `ranking_rank_integrity` (rank non-null iff ranking row; contiguous
`1..N` per respondent-question).

## 3. A matrix is one sub-question per cell (M5.3)

`q5` (matrix) → `q5.taste`, `q5.price`; `q6` (matrixdropdown) → `q6.laptop.brand`,
`q6.laptop.os`. Each cell resolves to an `option_key` like a single-select, and a
cell left blank is a shown-skipped row — distinct from a routed-past matrix.

```bash
PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -c "
  SELECT dq.stable_name, fri.was_shown,
         (fri.option_key IS NOT NULL) AS answered, count(*)
  FROM marts.fact_response_item fri
  JOIN marts.dim_question dq ON dq.question_id = fri.question_id
  WHERE dq.matrix_name IN ('q5', 'q6')
  GROUP BY 1, 2, 3 ORDER BY 1, 2 DESC, 3 DESC;"
```

Automated: `matrix_cell_resolution`; the shown-set is resolved against the matrix's
own name (`shown_set_integrity` / `routed_past_not_in_shown_set` coalesce
`matrix_name`).

## 4. A repeating group fans out per occurrence (M5.4)

`q7` is a paneldynamic with two template cells — `q7.kind` (dropdown → `option_key`)
and `q7.nickname` (high-risk free text). The answer is an **array of objects**, one
per occurrence; the array position becomes the fact-grain `occurrence`. R1 added two
devices (occurrence 1 + 2), R2 one (occurrence 1, nickname left blank).

```bash
PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -c "
  SELECT dq.stable_name, fri.occurrence, o.value AS kind,
         fri.value_text_redacted, fri.was_shown
  FROM marts.fact_response_item fri
  JOIN marts.dim_question dq ON dq.question_id = fri.question_id
  LEFT JOIN marts.dim_option o ON o.option_key = fri.option_key
  WHERE dq.panel_name = 'q7'
  ORDER BY dq.stable_name, fri.occurrence;"
```

Two people's devices are separate rows at `occurrence` 1 and 2 — never conflated,
never summed into one. A panel that was shown-skipped (R3) or routed-past (R4)
collapses to a single `occurrence = 1` routing row, so the three routing states
survive at cell-and-occurrence granularity.

Automated:

- `paneldynamic_occurrence_integrity` — `occurrence > 1` only on a panel cell, and
  occurrences are contiguous `1..N` per (respondent, panel). A non-panel row that
  picked up an occurrence, or a fan-out gap, fails the build.
- `shown_set_integrity` / `routed_past_not_in_shown_set` — a panel cell is shown iff
  its panel is (the tests coalesce `panel_name`).

```bash
cd dbt && DBT_HOST=localhost DBT_USER=stele_etl DBT_PASSWORD=dev DBT_DBNAME=stele \
  uv run dbt build --profiles-dir . \
  --select paneldynamic_occurrence_integrity shown_set_integrity \
           routed_past_not_in_shown_set option_fact_row_count_parity
```

## 5. Free text inside a repeating group is screened per occurrence (M5.4)

`q7.nickname` is high-risk free text. Each occurrence's answer is copied to
`pii.free_text_responses` keyed by `(raw_response_id, question_name, occurrence)`,
so a reviewer can promote one device's nickname while redacting another. The marts
gate `value_text` per occurrence: unpromoted high-risk panel cells read
`value_text_redacted = true` with no text, exactly like a top-level high-risk
answer.

```bash
# As a PII-cleared role (stele_etl cannot read pii.free_text_responses by design):
PGPASSWORD=dev psql -h localhost -U stele_dev -d stele -c "
  SELECT question_name, occurrence, value_text
  FROM pii.free_text_responses
  WHERE question_name = 'q7.nickname'
  ORDER BY occurrence;"
```

Automated, all green on `dbt build`:

- `free_text_redaction_parity` — re-derives the `value_text` / redaction expectation
  from `int_response_answers` + the per-occurrence promotion decision.
- `promoted_free_text_in_marts` — a promoted+answered cell surfaces its text.
- API: `test_pii_review.py::test_panel_free_text_copies_one_pii_row_per_occurrence`
  and `::test_panel_free_text_occurrences_promoted_independently`.

The ETL role is denied `pii.free_text_responses` — the warehouse never sees the
text, only the redaction decision (model-C trust boundary, M3.4). Verify:

```bash
PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -c \
  "SELECT count(*) FROM pii.free_text_responses;" 2>&1 | grep -q "permission denied" \
  && echo "OK: stele_etl denied PII text" || echo "FAIL"
```

## 6. The three places, per type

Every supported non-scalar type is rejected at publish until it is wired in all
three. The publish-gate unit tests assert both directions — the supported surface
passes, the deferred surface (scalar cells, multi-select panel cells, …) is
rejected with a clear 422.

```bash
uv run pytest api/tests/test_publish_gate.py api/tests/test_pii_review.py -q
cd frontend && npx vitest run scripts/roundTrip.test.mjs
```

`patterns/` ships a validated fixture per type (`multi_select.json`, `ranked.json`,
`matrix.json`, `matrixdropdown.json`, `repeating_group.json`); `test_patterns.py`
runs every one through the real gate, so the reference examples can't rot if the
gate tightens.
