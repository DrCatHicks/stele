# infra/ — Infrastructure as code (OpenTofu)

Deployment modules for the survey engine. **Railway is the first target** (M7.4);
AWS and GCP/Cloud SQL modules are deferred and would land as sibling directories
(`infra/aws/`, `infra/gcp/`) satisfying the same cloud-neutral variable interface
(`project_name`, `environment_name`, `region`, `image`, `image_tag`,
`database_name`). A single cross-cloud module is a myth — what's portable is the
caller's intent, not the resources.

```
infra/
  railway/        Railway: project + managed Postgres + web + ETL cron + secrets
```

## Railway — apply

Prereqs: [OpenTofu](https://opentofu.org) ≥ 1.6 and a Railway **workspace/account**
token (a project token can't create projects). Both app services pull a prebuilt
image from GHCR (D7), so Railway never builds from source — the Railway GitHub app
is no longer required. CI must have published the image first (a push to `main`
builds, tests, and pushes `ghcr.io/countercheck/stele:main`); the package is public,
so Railway pulls it without credentials.

The provider reads its auth from the `RAILWAY_TOKEN` env var. Keep it in a
gitignored `.env` you source, rather than a bare `export` that dies with the shell
(Railway shows account tokens only once) — see `.env.example`:

```bash
cd infra/railway
cp .env.example .env                            # paste your workspace token, gitignored
cp terraform.tfvars.example terraform.tfvars    # edit as needed
source .env                                     # exports RAILWAY_TOKEN
tofu init
tofu plan
tofu apply
```

`apply` creates the project, a Postgres service (Railway's SSL Postgres image on a
persistent volume), a web service, and an ETL **cron** service. Both app services
pull the **same prebuilt image** (`var.image:var.image_tag`); the cron selects the
`etl` verb via `STELE_ENTRYPOINT=etl` and runs on `etl_cron_schedule` (default daily
06:00 UTC). On the web service's start, `STELE_MIGRATE_ON_START=1` bootstraps the
four `stele_*` roles from the generated passwords and runs `alembic upgrade head` as
the admin identity, before uvicorn binds (the image-deploy stand-in for the old
`railway.json` pre-deploy command). The cron service connects only as
least-privilege `stele_etl` and never bootstraps; see
`docs/verification/m7.5-etl-cron.md` for the ephemeral-FS artifact handling.

**Two post-apply dashboard steps** the OpenTofu provider can't express on an
image-based service: set the web service's healthcheck path to `/api/health`, and
the ETL service's restart policy to **Never** (so a failed run waits for the next
tick instead of crash-looping). Full procedure + rationale:
`docs/verification/d7-prebuilt-image.md`.

### Rolling out a new build

CI builds, tests, and pushes the image on every push to `main` (tags `:main` and
`:<commit-sha>`). To deploy it, point the services at the new image and apply —
changing the deployed image string is what tells Railway to redeploy:

```bash
tofu apply                              # deploy :main verbatim — NO-OP if unchanged*
tofu apply -var deploy_latest=true      # resolve :main's current digest and ship it
tofu apply -var image_tag=<commit-sha>  # pin a specific, reproducible build
```

\* A bare `apply` deploys the `:main` **tag string**, which never changes when CI
re-pushes the floating tag — so it won't roll a new build on its own (deploys stay
deterministic). To ship whatever `main` currently points at, add
`-var deploy_latest=true`: it resolves the live `:main` digest from GHCR and deploys
that immutable `…@sha256:…` reference, giving OpenTofu a real diff exactly when the
image moved (and a clean no-op when it hasn't). Pinning `-var image_tag=<sha>` is the
reproducible/rollback path. The digest lookup uses the `kreuzwerker/docker` provider,
pulls the public package anonymously, and is gated by `count` so a default apply never
contacts the registry.

To make a merge to `main` go live automatically (instead of a manual `tofu apply`),
set a `RAILWAY_TOKEN` **project** token as a GitHub Actions repo secret — that
activates the dormant `Trigger Railway redeploy` CI step. It stays skipped (CI green,
`tofu apply` the deploy path) until the secret exists. See
`docs/verification/d7-prebuilt-image.md` § *Optional: auto-deploy on merge to main*.

Retrieve a generated credential (e.g. to hand an analyst their warehouse login):

```bash
tofu output -raw stele_analyst_password
```

## Operating the deployment — the Railway CLI (separate from tofu)

`tofu` provisions; **operating** the running deployment (seeding the admin, one-off
shells into a service) needs the [Railway CLI](https://docs.railway.com/guides/cli),
which is a separate install and a separate auth from the provider:

```bash
npm i -g @railway/cli      # or: brew install railway
railway login              # browser auth, cached in ~/.railway (preferred for `ssh`)
railway link               # pick this project → environment → service
```

**Tokens differ between the two tools — this is a footgun.** The OpenTofu provider
reads a workspace/account token from `RAILWAY_TOKEN`. The **CLI** treats
`RAILWAY_TOKEN` as a *project* token and reads an account token from
`RAILWAY_API_TOKEN` instead — so a workspace token left in `RAILWAY_TOKEN` makes
the CLI fail with "Invalid RAILWAY_TOKEN". Either `railway login` (browser, ignores
the env var) or export the account token as `RAILWAY_API_TOKEN` for the CLI. Don't
`source infra/railway/.env` in a shell where you run the CLI — keep tofu and CLI in
separate shells.

To run a command **inside** a running service's container (where the entrypoint
script exists and `postgres.railway.internal` resolves), use `railway ssh`, **not**
`railway run` — `railway run` executes locally with the service's env injected, so
it can't reach the private-network Postgres or the in-image entrypoint:

```bash
railway ssh --service web        # drops into a shell in the web container
```

## Connecting to Postgres directly (analysts, reviewers)

Railway's Postgres sits on the project's **private network**, so there is no
public connection string until the opt-in TCP proxy is on. It is **off by
default** — a standing public 5432 is a deliberate exposure, so flip it on
while you need access and back off when you're done. Two paths in, by
audience: the operator shortcut below, or per-user logins for anyone else.

### Operator shortcut (group-role login)

As the deployer you already hold the role passwords (they're in tofu state),
so the fastest path is logging straight in as the group role. **Don't share
these** — they're operator-wide secrets with no per-person audit trail; for
anyone else, mint a per-user login (next subsection).

```bash
# 1. Enable the proxy for this session — -var keeps it out of tfvars,
#    so re-running stays idempotent.
tofu apply -var 'enable_postgres_proxy=true'
tofu output postgres_proxy                       # → <host>:<port>

# 2. Grab the role password.
tofu output -raw stele_analyst_password          # marts (the warehouse)
tofu output -raw stele_pii_reviewer_password     # pii   (free-text review)

# 3. Connect.
psql "postgresql://stele_analyst:<password>@<host>:<port>/stele"

# 4. Close the public endpoint when you're done.
tofu apply -var 'enable_postgres_proxy=false'
```

Pick the role by what you need to read: `stele_analyst` reaches `marts` only,
`stele_pii_reviewer` reaches `pii` only — that one-schema-each ceiling is
what keeps a leaked credential low-stakes (CLAUDE.md *Schemas* table). Both
group roles are LOGIN and hold the schema grant directly, so no `SET ROLE`
step is needed.

### Per-user logins (for colleagues, auditable, revocable)

Mint a personal NOINHERIT login role with the M3.5 provisioning CLI, deliver
the one-time password, and they connect to the same proxy host:port. The
login is privilege-less until they `SET ROLE stele_analyst;` (or
`…_pii_reviewer`) after connecting. Full provision + revoke flow:
`docs/verification/m7.6-demo-to-prod.md`.

### Gotchas

- Use the plain `postgresql://` driver tag for psql — *not* the
  `postgresql+psycopg://` tag the app's env vars use (that one's
  SQLAlchemy-only).
- Default DB name is `stele` (`variables.tf` `database_name`); check your
  `terraform.tfvars` if you overrode it.

## Demo seed and secret rotation

Seeding the initial admin (`seed` entrypoint verb), rotating the generated role
passwords (`scripts/rotate_role_password.py` + the `*_password_override`
variables), and per-user credential provisioning/revoke are covered in
`docs/verification/m7.6-demo-to-prod.md`, along with the full demo→prod
checklist.

## State holds secrets

The module **generates** all role passwords and the session secret
(`random_password`), so they live in tofu state. For the demo the state is **local
and gitignored** (`.gitignore` excludes `*.tfstate*` and `*.tfvars`). Before real
production or any multi-operator use, move to an encrypted remote backend — this is
on the same "revisit before real PII" checklist (`docs/verification/m7.6-demo-to-prod.md`)
as EU data residency. To supply role passwords instead of generating them (rotation
or a no-secrets-in-state posture), set the `*_password_override` variables.

## What's validated where

CI (`infra` job) runs `tofu fmt -check` + `tofu validate` on every PR — it proves
the module is well-formed and type-checks, but does **not** apply (no Railway
account/token in CI), mirroring how the Docker image and role bootstrap are
CI-validated without a live deploy. A real `tofu apply` is an operator step.

See `docs/verification/m7.4-railway.md` for the deploy model rationale, the
admin-connection tradeoff, and the deferred ETL-cron / analyst-proxy scope.
