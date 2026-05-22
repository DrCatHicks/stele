# Survey Engine

Survey engine and data warehouse — see `survey-engine-design-doc.md` for architecture.

## Layout

```
api/        Python API (response collection, withdrawal, PII routing)
frontend/   TypeScript SurveyJS runtime
dbt/        Transformations: raw_responses → stg → marts
```

## Local setup

### One-time

```bash
# Python (uv)
curl -LsSf https://astral.sh/uv/install.sh | sh    # install uv if needed
cd api && uv sync --all-extras

# Node (pnpm)
corepack enable                                     # ships with Node 16.10+
cd frontend && pnpm install

# dbt
pip install dbt-core dbt-postgres

# Pre-commit hooks
pip install pre-commit && pre-commit install
```

### Common commands

| Task | Command |
|---|---|
| Run API tests | `cd api && uv run pytest` |
| Lint Python | `cd api && uv run ruff check .` |
| Typecheck Python | `cd api && uv run mypy .` |
| Run frontend tests | `cd frontend && pnpm test` |
| Lint frontend | `cd frontend && pnpm lint` |
| Build frontend | `cd frontend && pnpm build` |
| Rebuild marts | `cd dbt && dbt run --profiles-dir .` |

## CI

GitHub Actions runs on every PR (`.github/workflows/ci.yml`):

- Python: ruff, mypy, pytest with Postgres service container
- TypeScript: eslint, prettier check, tsc, vitest, build
- dbt: parse + compile against a throwaway Postgres

Two additional workflows:

- `claude-review.yml` — Claude reviews PRs. Requires `ANTHROPIC_API_KEY` secret.
- `codeql.yml` — GitHub SAST for Python and TS, on PRs and weekly.

## Required repo setup

After pushing the initial commit, configure these in GitHub:

1. **Secrets** (Settings → Secrets and variables → Actions):
   - `ANTHROPIC_API_KEY` — for Claude PR review
2. **Branch protection** (Settings → Branches → add rule for `main`):
   - Require pull request before merging
   - Require status checks to pass: select all `CI · *` jobs and CodeQL
   - Require conversation resolution before merging
   - Require linear history (optional, but tidier)
3. **CODEOWNERS** — edit `.github/CODEOWNERS` to replace `@your-username`.
