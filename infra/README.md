# infra/ — Infrastructure as code (OpenTofu)

Deployment modules for the survey engine. **Railway is the first target** (M7.4);
AWS and GCP/Cloud SQL modules are deferred and would land as sibling directories
(`infra/aws/`, `infra/gcp/`) satisfying the same cloud-neutral variable interface
(`project_name`, `environment_name`, `region`, `source_repo`, `source_repo_branch`,
`database_name`). A single cross-cloud module is a myth — what's portable is the
caller's intent, not the resources.

```
infra/
  railway/        Railway: project + managed Postgres + web + ETL cron + secrets
```

## Railway — apply

Prereqs: [OpenTofu](https://opentofu.org) ≥ 1.6, a Railway **workspace/account**
token (a project token can't create projects), and the Railway GitHub app
installed on the repo named in `source_repo`.

```bash
export RAILWAY_TOKEN=...                 # workspace/account token
cd infra/railway
cp terraform.tfvars.example terraform.tfvars   # edit as needed
tofu init
tofu plan
tofu apply
```

`apply` creates the project, a Postgres service (Railway's SSL Postgres image on a
persistent volume), a web service Railway builds from the repo Dockerfile, and an
ETL **cron** service (the same image, started on the `etl` verb on
`etl_cron_schedule`, default daily 06:00 UTC). On the web service's first deploy,
the `migrate` pre-deploy command (see the repo-root `railway.json`) bootstraps the
four `stele_*` roles from the generated passwords and runs `alembic upgrade head`
as the admin identity, before the web process starts. The cron service connects
only as least-privilege `stele_etl`; see `docs/verification/m7.5-etl-cron.md` for
its config (`railway.etl.json`) and the ephemeral-FS artifact handling.

Retrieve a generated credential (e.g. to hand an analyst their warehouse login):

```bash
tofu output -raw stele_analyst_password
```

## Analyst access, demo seed, secret rotation

External analyst/reviewer database access (the opt-in `enable_postgres_proxy` TCP
proxy), seeding the initial admin (`seed` entrypoint verb), and rotating the
generated role passwords (`scripts/rotate_role_password.py` + the
`*_password_override` variables) are covered in
`docs/verification/m7.6-demo-to-prod.md`, along with the full demo→prod checklist.

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
