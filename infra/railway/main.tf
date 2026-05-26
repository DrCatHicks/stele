# Railway deployment for the survey engine (M7.4).
#
# Shape: one project, one managed Postgres service (Railway's SSL Postgres image +
# a persistent volume), one web service Railway builds from the repo's Dockerfile.
# Migrations are NOT a resource here — they run as the web service's pre-deploy
# command (railway.json: preDeployCommand = "migrate"), so every release bootstraps
# roles + runs `alembic upgrade head` as the admin identity before traffic shifts.
#
# Two identities reach Postgres from the one web service, by design (M7.3 + M7.4):
#   - the web *process* connects as least-privilege stele_api  (STELE_DATABASE_URL)
#   - the pre-deploy *migrate* connects as the admin/owner role (STELE_ADMIN_DATABASE_URL)
# bootstrap_roles.py and alembic both prefer STELE_ADMIN_DATABASE_URL, so the runtime
# never holds more than stele_api. The admin connection string is present in the web
# service's env (the pre-deploy needs it) — a known tradeoff, see docs/verification/m7.4-railway.md.
#
# ETL runs as a cron service (M7.5) — the same image, started on the `etl` verb on
# a schedule (var.etl_cron_schedule), connecting as least-privilege stele_etl.
#
# Analysts/reviewers reach Postgres directly (M3.5 per-user roles) through an
# opt-in public TCP proxy (M7.6, var.enable_postgres_proxy), and the generated role
# passwords are rotatable via the *_password_override variables — see the override
# locals below and docs/verification/m7.6-demo-to-prod.md.

# ---- Generated secrets -------------------------------------------------------
# DB passwords are alphanumeric (special = false) so they need no URL-escaping in
# the connection strings below. The session secret is not embedded in a URL, so it
# may use the full character set. All live in tofu state — keep the state local and
# gitignored for the demo (see infra/README.md).

resource "random_password" "admin" {
  length  = 32
  special = false
}

resource "random_password" "stele_api" {
  length  = 32
  special = false
}

resource "random_password" "stele_etl" {
  length  = 32
  special = false
}

resource "random_password" "stele_analyst" {
  length  = 32
  special = false
}

resource "random_password" "stele_pii_reviewer" {
  length  = 32
  special = false
}

resource "random_password" "session_secret" {
  length = 64
}

locals {
  pg_host = "${railway_service.postgres.name}.railway.internal"
  pg_port = 5432

  # Effective password = the operator's override if set, else the generated one.
  # A rotation flows: rotate_role_password.py ALTERs the live role, then the
  # operator sets the matching *_password_override and applies, so the connection
  # strings below pick up the new value (the generated random_password stays inert
  # once overridden). See variables.tf § secret overrides.
  admin_password              = var.admin_password_override != "" ? var.admin_password_override : random_password.admin.result
  stele_api_password          = var.stele_api_password_override != "" ? var.stele_api_password_override : random_password.stele_api.result
  stele_etl_password          = var.stele_etl_password_override != "" ? var.stele_etl_password_override : random_password.stele_etl.result
  stele_analyst_password      = var.stele_analyst_password_override != "" ? var.stele_analyst_password_override : random_password.stele_analyst.result
  stele_pii_reviewer_password = var.stele_pii_reviewer_password_override != "" ? var.stele_pii_reviewer_password_override : random_password.stele_pii_reviewer.result

  # SQLAlchemy/psycopg driver tag matches dev/CI (postgresql+psycopg://). The web
  # app uses it directly; bootstrap_roles.py strips the tag for libpq.
  admin_url     = "postgresql+psycopg://postgres:${local.admin_password}@${local.pg_host}:${local.pg_port}/${var.database_name}"
  stele_api_url = "postgresql+psycopg://stele_api:${local.stele_api_password}@${local.pg_host}:${local.pg_port}/${var.database_name}"
  # The ETL cron service's run-log writes (ops.etl_runs) go through this; dbt itself
  # reads the DBT_* vars below. Both connect as least-privilege stele_etl.
  stele_etl_url = "postgresql+psycopg://stele_etl:${local.stele_etl_password}@${local.pg_host}:${local.pg_port}/${var.database_name}"
}

# ---- Project + default environment -------------------------------------------

resource "railway_project" "stele" {
  name         = var.project_name
  private      = true
  workspace_id = var.workspace_id != "" ? var.workspace_id : null

  default_environment = {
    name = var.environment_name
  }
}

# ---- Managed Postgres --------------------------------------------------------

resource "railway_service" "postgres" {
  name         = "postgres"
  project_id   = railway_project.stele.id
  source_image = var.postgres_image

  volume = {
    name       = "${var.project_name}-pgdata"
    mount_path = var.postgres_volume_mount_path
  }

  regions = [{
    region       = var.region
    num_replicas = 1
  }]
}

resource "railway_variable_collection" "postgres" {
  environment_id = railway_project.stele.default_environment.id
  service_id     = railway_service.postgres.id

  variables = [
    {
      name  = "POSTGRES_USER"
      value = "postgres"
    },
    {
      # Establishes the superuser password at initdb (first boot on an empty
      # volume); the Railway image ignores changes after that, so admin rotation
      # is an ALTER ROLE (rotate_role_password.py) — the override just keeps this
      # value consistent with what the script set.
      name  = "POSTGRES_PASSWORD"
      value = local.admin_password
    },
    {
      name  = "POSTGRES_DB"
      value = var.database_name
    },
    {
      # Railway's Postgres template nests the data dir under the mount so the
      # volume root (with its lost+found) isn't PGDATA itself.
      name  = "PGDATA"
      value = "${var.postgres_volume_mount_path}/pgdata"
    },
  ]
}

# ---- Web (API + SPA, built from the repo Dockerfile) -------------------------

resource "railway_service" "web" {
  name               = "web"
  project_id         = railway_project.stele.id
  source_repo        = var.source_repo
  source_repo_branch = var.source_repo_branch
  # railway.json (repo root) carries the build + deploy config the provider can't
  # express: the Dockerfile builder, healthcheckPath, restartPolicy, and the
  # preDeployCommand that runs migrations before each release.
  config_path = "railway.json"

  regions = [{
    region       = var.region
    num_replicas = 1
  }]
}

resource "railway_variable_collection" "web" {
  environment_id = railway_project.stele.default_environment.id
  service_id     = railway_service.web.id

  variables = [
    # Runtime: the web process connects as least-privilege stele_api.
    {
      name  = "STELE_DATABASE_URL"
      value = local.stele_api_url
    },
    # Pre-deploy migrate: bootstrap_roles.py + alembic prefer this admin identity.
    {
      name  = "STELE_ADMIN_DATABASE_URL"
      value = local.admin_url
    },
    # First-deploy role bootstrap reads these to CREATE ROLE; re-deploys ignore
    # them (existing roles are never re-passworded).
    {
      name  = "STELE_API_PASSWORD"
      value = local.stele_api_password
    },
    {
      name  = "STELE_ETL_PASSWORD"
      value = local.stele_etl_password
    },
    {
      name  = "STELE_ANALYST_PASSWORD"
      value = local.stele_analyst_password
    },
    {
      name  = "STELE_PII_REVIEWER_PASSWORD"
      value = local.stele_pii_reviewer_password
    },
    {
      name  = "STELE_SESSION_SECRET"
      value = random_password.session_secret.result
    },
    {
      name  = "STELE_COOKIE_SECURE"
      value = var.cookie_secure ? "true" : "false"
    },
    {
      name  = "PORT"
      value = "8000"
    },
  ]
}

# ---- ETL (cron service running the `etl` verb) -------------------------------
#
# Same image as web, built from the repo, but started on the `etl` verb on a
# schedule. Railway runs the container at var.etl_cron_schedule and lets it exit;
# the run is logged to ops.etl_runs in managed Postgres (durable), since the
# container filesystem — and the on-disk dbt artifact archive — are ephemeral.
#
# This service points at its OWN config file (railway.etl.json), not web's
# railway.json: it must NOT inherit web's healthcheck or migrate pre-deploy, and
# its start command overrides the image ENTRYPOINT to dispatch `etl`. (Railway's
# custom start command replaces the ENTRYPOINT in exec form — same gotcha M7.4's
# migrate pre-deploy handled — so railway.etl.json gives the full
# `bash docker-entrypoint.sh etl` invocation.) Migrations stay web's pre-deploy
# job; the cron connects only as least-privilege stele_etl and never bootstraps.
resource "railway_service" "etl" {
  name               = "etl"
  project_id         = railway_project.stele.id
  source_repo        = var.source_repo
  source_repo_branch = var.source_repo_branch
  config_path        = "railway.etl.json"
  cron_schedule      = var.etl_cron_schedule

  regions = [{
    region       = var.region
    num_replicas = 1
  }]
}

resource "railway_variable_collection" "etl" {
  environment_id = railway_project.stele.default_environment.id
  service_id     = railway_service.etl.id

  variables = [
    # The runner's ops.etl_runs writes connect as least-privilege stele_etl.
    {
      name  = "STELE_ETL_DATABASE_URL"
      value = local.stele_etl_url
    },
    # dbt's postgres profile (dbt/profiles.yml) reads these; same stele_etl role.
    # The profile's port is fixed at 5432, matching Railway's internal Postgres.
    {
      name  = "DBT_HOST"
      value = local.pg_host
    },
    {
      name  = "DBT_USER"
      value = "stele_etl"
    },
    {
      name  = "DBT_PASSWORD"
      value = local.stele_etl_password
    },
    {
      name  = "DBT_DBNAME"
      value = var.database_name
    },
    # GIT_SHA is not set here on purpose: Railway auto-injects
    # RAILWAY_GIT_COMMIT_SHA into repo-built services, which the runner reads for
    # ops.etl_runs provenance (api/etl/runner.py git_sha()).
  ]
}

# Optional public domain on Railway's *.up.railway.app. Omitted when web_subdomain
# is empty (e.g. you attach a custom domain out of band).
resource "railway_service_domain" "web" {
  count          = var.web_subdomain != "" ? 1 : 0
  subdomain      = var.web_subdomain
  environment_id = railway_project.stele.default_environment.id
  service_id     = railway_service.web.id
}

# ---- Optional public Postgres TCP proxy (analyst/reviewer direct access) -----
#
# Analysts and reviewers query Postgres directly with the per-user login roles the
# M3.5 provisioning CLI mints (members of stele_analyst / stele_pii_reviewer,
# NOINHERIT). Railway's Postgres sits on the project's private network, so those
# credentials need a path in from outside — a TCP proxy maps a public host:port to
# the service's internal 5432. Auth is still Postgres password + the least-priv
# group role, but it is a public endpoint, so it's OFF by default (M7.6): turn it
# on only when someone actually needs warehouse access, and treat it as a revisit
# item before real PII (docs/verification/m7.6-demo-to-prod.md). Retrieve the
# public host:port with `tofu output postgres_proxy`.
resource "railway_tcp_proxy" "postgres" {
  count            = var.enable_postgres_proxy ? 1 : 0
  environment_id   = railway_project.stele.default_environment.id
  service_id       = railway_service.postgres.id
  application_port = local.pg_port
}
