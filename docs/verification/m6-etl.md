# M6 ETL operational maturity — verification

Proves the FR-11 contract: the full marts schema rebuilds from `app.raw_responses`
with **one command**, every run is **logged** with row counts, timings, and
reproducibility metadata (`ops.etl_runs`), and dbt's artifacts are **archived per
run** (NFR-2). Response *content* comes solely from `raw_responses`; dbt also reads
one non-content metadata source — `pii.free_text_review_decisions` (the reviewer's
promote/reject decisions, no PII text; M3.4) — which the run log counts alongside it. The §3.7 custom-test suite runs as part of that single command, so a
rebuild that would corrupt the grain fails the command rather than landing.

| Story | Adds |
|---|---|
| M6.1 | `parent_question_integrity` dbt test (the last §3.7 custom test; guards invariant 5) |
| M6.2 | `ops.etl_runs` log + `api/etl/runner.py` wrapping `dbt build`; artifact archival |
| M6.3 | `make etl` single command; CI runs the real runner; this runbook |

## 1. One command rebuilds the marts (FR-11)

```bash
make etl          # logged rebuild from raw_responses (assumes migrated + seeded)
# or, from scratch:
make rebuild      # migrate → seed → make etl
```

`make etl` runs `scripts/run_etl.py`, which connects as the least-privileged
`stele_etl` role, runs `dbt build` (full-refresh — staging views + intermediate/mart
tables, no incremental), and on success prints the run id and the artifact dir:

```
ETL run 6f9f4c92-…-6a83e8552bac: success (dbt exit 0)
  artifacts: dbt/etl_artifacts/6f9f4c92-…-6a83e8552bac
```

The build line `PASS=56 WARN=0 ERROR=0` includes the custom invariant tests, so the
command is the gate: a grain-collapsing change makes `dbt build` non-zero, which the
runner records as `failed` and propagates as a non-zero exit (so `make etl`/CI fail).

## 2. Every run is logged with row counts + reproducibility metadata

`ops.etl_runs` is an operational log outside the analyst star schema (its own `ops`
schema). One row per invocation; `stele_etl` may INSERT/UPDATE but **not** DELETE —
it is append-then-resolve, never rewritten.

```bash
PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -x -c "
  SELECT run_id, status, completed_at - started_at AS duration,
         source_row_counts, dbt_version, git_sha
  FROM ops.etl_runs ORDER BY started_at DESC LIMIT 1;"
```

```
status            | success
duration          | 00:00:04.29…
source_row_counts | {"app.raw_responses": 20, "pii.free_text_review_decisions": 1}
dbt_version       | 1.11.11
git_sha           | 4002951…
```

`source_row_counts` is read at run *start* from the sources dbt actually declares
(`models/sources.yml`, so the log can't drift from what dbt reads); `mart_row_counts`
is read after a successful build:

```bash
PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -c "
  SELECT jsonb_pretty(mart_row_counts) FROM ops.etl_runs
  ORDER BY started_at DESC LIMIT 1;"
```

Counts above are from a polluted dev DB carrying earlier surveys; a fresh CI DB gives
the single-survey counts in `scripts/seed_example_survey.py`'s docstring. The shape —
not the magnitudes — is what this verifies.

## 3. dbt artifacts archived per run (NFR-2)

The run id is the index into the archive (no path column); `manifest.json` +
`run_results.json` land in a gitignored per-run dir:

```bash
ls dbt/etl_artifacts/$(
  PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -tAc \
    "SELECT run_id FROM ops.etl_runs ORDER BY started_at DESC LIMIT 1;")
# → manifest.json  run_results.json
```

## 4. A failed build is recorded, not lost

Archival is best-effort and never masks the outcome; a non-zero `dbt build` (or a
raised step — a missing dbt binary, a count error) resolves the row to `failed` with
**null** `mart_row_counts` (a failed build's marts are not a trustworthy snapshot),
rather than leaving it stuck at `running`. Covered by:

```bash
uv run pytest api/tests/test_run_etl.py -q
```

- `test_execute_run_failure_records_failed_with_null_marts` — non-zero exit → `failed`.
- `test_execute_run_resolves_row_when_dbt_build_raises` — a raised step still resolves.
- `test_cli_forwards_dbt_args_and_returns_exit_code` — the CLI propagates dbt's exit
  code, so `make etl` / CI fail on a failed build.

These integration tests run as the real `stele_etl` role (dbt stubbed), so a missing
INSERT/UPDATE grant on `ops.etl_runs` fails the test rather than hiding behind a
superuser.

## 5. The §3.7 custom-test suite is complete and green

All five custom tests from the design doc run inside the `make etl` build:

```bash
cd dbt && DBT_HOST=localhost DBT_USER=stele_etl DBT_PASSWORD=dev DBT_DBNAME=stele \
  uv run dbt build --profiles-dir . --select \
    option_fact_row_count_parity version_coverage polymorphic_value_invariant \
    parent_question_integrity shown_set_integrity
```

`parent_question_integrity` (M6.1) is the last of the five. It is vacuous today —
`dim_question` casts `parent_question_id`/`parent_question_rationale` null because
cross-version equivalence is deferred — but load-bearing the moment equivalence turns
on (invariant 5: a populated parent needs a rationale and a strictly-earlier
`first_published_at`).

## 6. Trust boundary holds under the ETL role

The warehouse role logs runs but never sees PII text or rewrites the log:

```bash
# No DELETE on the append-then-resolve log:
PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -c \
  "DELETE FROM ops.etl_runs WHERE false;" 2>&1 | grep -q "permission denied" \
  && echo "OK: stele_etl cannot DELETE ops.etl_runs"

# No read of the PII text store (model-C, M3.4):
PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -c \
  "SELECT count(*) FROM pii.free_text_responses;" 2>&1 | grep -q "permission denied" \
  && echo "OK: stele_etl denied pii.free_text_responses"

# Analysts can read the run log:
PGPASSWORD=dev psql -h localhost -U stele_analyst -d stele -c \
  "SELECT count(*) FROM ops.etl_runs;"
```

## 7. CI runs the real command

The `dbt` CI job's final step is `uv run python scripts/run_etl.py` (not a bare `dbt
build`), against a freshly migrated + seeded DB, connecting as `stele_etl`. So on
every PR the full FR-11 path — build, the custom tests, the `ops.etl_runs` write under
real grants, and artifact archival — runs end-to-end, not just the dbt-stubbed
integration tests.
