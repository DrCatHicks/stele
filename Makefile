# Operational shortcuts for the survey engine. The authoritative command list is
# CLAUDE.md; these targets just give the common ones a single name. `make etl` is
# the FR-11 contract: rebuild the marts schema from raw_responses with one command,
# logged to ops.etl_runs.
#
# Connection vars default to the dev-container values and are overridable from the
# environment (CI/prod export their own), e.g. `DBT_USER=stele_etl make etl`.

# The ETL runner shells out to `dbt build`, which reads these from the dbt profile.
export DBT_HOST     ?= localhost
export DBT_USER     ?= stele_etl
export DBT_PASSWORD ?= dev
export DBT_DBNAME   ?= stele

# Bind the runner's run-log connection to the SAME database dbt builds: derive it
# from the DBT_* vars so an override like `DBT_DBNAME=foo make etl` can't split-brain
# the ops.etl_runs log (one DB) against the dbt build target (another). An explicit
# STELE_ETL_DATABASE_URL (CI/prod) still wins via ?=. Port matches the dbt profile.
export STELE_ETL_DATABASE_URL ?= postgresql://$(DBT_USER):$(DBT_PASSWORD)@$(DBT_HOST):5432/$(DBT_DBNAME)

.DEFAULT_GOAL := help
.PHONY: help etl rebuild migrate seed test lint

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-9s\033[0m %s\n", $$1, $$2}'

etl: ## Rebuild marts from raw_responses via the logged ETL runner (FR-11/§3.7)
	uv run python scripts/run_etl.py

# Recipe (not bare prerequisites) so the order holds under `make -j`: the steps
# are strictly sequential — migrate, then seed, then the logged ETL.
rebuild: ## Full from-scratch path: migrate → seed → logged ETL
	$(MAKE) migrate
	$(MAKE) seed
	$(MAKE) etl

migrate: ## Apply Alembic migrations up to head
	cd api && uv run alembic upgrade head

seed: ## Seed the example survey + responses (all routing states)
	uv run python scripts/seed_example_survey.py

test: ## Run the Python test suite
	uv run pytest

lint: ## Ruff check + format-check + invariant scan
	uv run ruff check . && uv run ruff format --check . && python3 scripts/check_invariants.py
