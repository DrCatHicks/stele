# D7 — Prebuilt image (build once, run the tested bytes everywhere)

Eliminates the deploy-time "build twice." Before D7, each Railway app service
(`web` and the `etl` cron) built the Dockerfile from the repo independently, and CI
built a *third* copy that it only smoke-tested and threw away — three builds of one
image, and the two Railway-built copies could drift from the CI-tested one.

Now **CI builds the image once, tests it, and pushes it to GHCR**, and both Railway
services pull that exact artifact. One build, one set of tested bytes, run
everywhere.

## What changed

| Concern | Before (M7.4/M7.5) | After (D7) |
|---|---|---|
| Image source | `web` + `etl` each `source_repo`-build the Dockerfile | both pull `ghcr.io/countercheck/stele:<tag>` |
| Where it's built | Railway, twice | CI, once (the smoke-tested image *is* the artifact) |
| `etl` start verb | `railway.etl.json` `startCommand` | `STELE_ENTRYPOINT=etl` env var |
| `web` migrations | `railway.json` `preDeployCommand` | `STELE_MIGRATE_ON_START=1` (migrate-on-start) |
| `web` healthcheck | `railway.json` `healthcheckPath` | **Railway dashboard** (see below) |
| `etl` restart policy | `railway.etl.json` `restartPolicyType: NEVER` | **Railway dashboard** (see below) |
| Deploy trigger | `git push` → Railway builds + deploys | CI push to GHCR, then `tofu apply` rolls the tag |

`railway.json` and `railway.etl.json` are deleted — config-as-code is read from a
connected repo, which an image deploy doesn't have.

## Why the env-var stand-ins

The community provider (`terraform-community-providers/railway`) exposes **no**
attribute for a start command, pre-deploy command, healthcheck, or restart policy —
those only ever came from `railway.json`, which Railway reads *from the connected
repo*. An image-based service has no repo checkout, so that mechanism is gone. Two
of the four behaviors move into the image itself (settable via plain env vars, which
the provider *can* set); two have no image-side equivalent and become dashboard
settings.

- **`etl` verb** — the entrypoint (`scripts/docker-entrypoint.sh`) selects its verb
  from `$1`, else `$STELE_ENTRYPOINT`, else `web`. The cron service sets
  `STELE_ENTRYPOINT=etl`. The image carries **no `CMD`** on purpose: a baked
  `CMD ["web"]` is passed as a positional arg even on the ETL service (Railway runs
  the image's `ENTRYPOINT`+`CMD` when no start command is set), and the positional
  arg wins over the env var — so the cron would run uvicorn. With no `CMD`, nothing
  is injected and `STELE_ENTRYPOINT` is honored. Explicit `<verb>` (dev/CI,
  `railway run`) still wins.
- **`web` migrate-on-start** — when `STELE_MIGRATE_ON_START=1`, the `web` verb runs
  bootstrap + `alembic upgrade head` (as the admin identity from
  `STELE_ADMIN_DATABASE_URL`) before `exec`-ing uvicorn. At `num_replicas=1` this
  preserves the release-safety property: a failed migrate aborts under `set -e`
  before uvicorn binds, the new container never goes healthy, and Railway keeps the
  prior deploy serving. **>1 replica would race** (alembic isn't concurrent) — that
  is the trigger for a separate migrate service (roadmap **D3**).

## Two post-apply dashboard steps

The provider can't set these on an image service, so set them once in the Railway
dashboard after the first `apply` (they persist across redeploys):

1. **web → Settings → Deploy → Healthcheck Path** = `/api/health`. Without it
   Railway considers the container live as soon as it stays up; the healthcheck adds
   app-readiness gating on top of the crash-on-bad-migrate gate.
2. **etl → Settings → Deploy → Restart Policy** = **Never**. A failed cron run must
   not crash-loop — it re-runs on the next tick and the failure is recorded in
   `ops.etl_runs`. The provider default would be `ON_FAILURE`.

These two settings are the accepted IaC-fidelity cost of the prebuilt-image model;
they're the only deploy config not captured in `main.tf`.

## Registry: public now, private later

The image is a **public** GHCR package — the repo is public, so the image exposes
nothing the source doesn't, and Railway pulls it anonymously
(`image_registry_username`/`image_registry_password` stay empty). To make it private
later: flip the GHCR package visibility, set those two variables (username = a GitHub
user/org, password = a `read:packages` PAT), and `tofu apply`. No module change.

## Provenance

The image has no `.git` and no git binary, and `RAILWAY_GIT_COMMIT_SHA` is injected
only into *repo-built* services — not image deploys. So CI bakes `GIT_SHA=<commit>`
into the image at build time, and `api/etl/runner.git_sha()` reads it for
`ops.etl_runs` provenance. The build arg is now the sole provenance source.

## Deploy flow

1. Push to `main` → CI `docker-build` job builds, smoke-tests, and pushes
   `ghcr.io/countercheck/stele:main` and `:<commit-sha>`.
2. Roll it out:
   ```bash
   cd infra/railway
   tofu apply                              # tracks the floating :main tag
   tofu apply -var image_tag=<commit-sha>  # pin a specific, reproducible build
   ```
   Changing the tag string is what makes the provider update the service and Railway
   redeploy. (A bare `apply` against an unchanged `:main` string is a no-op; pin a
   sha to force a known rollout.)

### Optional: auto-deploy on merge to main

The CI `docker-build` job has a final **`Trigger Railway redeploy`** step that
re-pulls the just-pushed `:main` image on both services, so a merge to `main` goes
live without a manual `tofu apply`. It is **dormant until you set a `RAILWAY_TOKEN`
repo secret** — the step's `env.RAILWAY_TOKEN != ''` guard skips it when the secret
is empty, so CI stays green and `tofu apply` stays the deploy path until you opt in.

To enable it:

1. In the Railway dashboard, create a **project token** for this project's
   environment (Project → Settings → Tokens). This is *not* the workspace token tofu
   uses — the CLI reads `RAILWAY_TOKEN` as a project token (footgun documented in
   `infra/README.md`).
2. Add it as a GitHub Actions repo secret named `RAILWAY_TOKEN`
   (Settings → Secrets and variables → Actions).
3. The next push to `main` builds, pushes, **and** redeploys. Confirm the first run:
   the step should `railway redeploy` both services rather than skip.

`tofu apply` keeps working unchanged whether or not the secret is set — it uses a
different token in a different place (`infra/railway/.env`).

## What's verified where

- **CI `docker-build` (every PR)** — builds the image, smoke-tests both runtimes +
  dbt + alembic + SPA + oracle + `import api.main`, and proves the dispatch verbs
  resolve, *including the D7 stand-ins*: `STELE_ENTRYPOINT=etl` (no positional arg)
  → `run_etl.py`, and `web` + `STELE_MIGRATE_ON_START=1` → uvicorn.
- **CI on `main`** — additionally logs in to GHCR and pushes the image (the
  workflow's `GITHUB_TOKEN` with `packages: write`; no PAT).
- **`api/tests/test_docker_entrypoint.py`** — unit-tests the dispatcher's env-var
  verb selection, arg-beats-env precedence, and that migrate-on-start still resolves
  to uvicorn in print mode (no DB).
- **CI `infra`** — `tofu fmt -check` + `validate` proves the `source_image` wiring
  (incl. the conditional registry-credential nulls) type-checks against the provider
  schema. No `apply` (no Railway token in CI), as for every M7 story.
- **Manual (operator)** — the two dashboard settings above, and confirming the
  rolled image's `ops.etl_runs.git_sha` matches the deployed commit.

## Tradeoffs (accepted)

- **Migrate-on-start vs pre-deploy container** — equivalent safety at 1 replica;
  revisit at >1 replica or real PII via a separate migrate service (**D3**).
- **Healthcheck + restart policy as dashboard settings** — small drift risk (not in
  `main.tf`); documented here and in `infra/README.md`.
- **Deploy is a `tofu apply`, not an auto-deploy on `git push`** — a deliberate,
  pinnable rollout rather than push-to-deploy; better for choosing *when* to ship.
  Opt back into push-to-deploy with the dormant CI redeploy step above (set the
  `RAILWAY_TOKEN` repo secret).
