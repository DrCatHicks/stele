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
# ETL (a cron service running the `etl` verb) and an external analyst TCP proxy are
# deferred to M7.5 / M7.6.

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

  # SQLAlchemy/psycopg driver tag matches dev/CI (postgresql+psycopg://). The web
  # app uses it directly; bootstrap_roles.py strips the tag for libpq.
  admin_url     = "postgresql+psycopg://postgres:${random_password.admin.result}@${local.pg_host}:${local.pg_port}/${var.database_name}"
  stele_api_url = "postgresql+psycopg://stele_api:${random_password.stele_api.result}@${local.pg_host}:${local.pg_port}/${var.database_name}"
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
      name  = "POSTGRES_PASSWORD"
      value = random_password.admin.result
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
      value = random_password.stele_api.result
    },
    {
      name  = "STELE_ETL_PASSWORD"
      value = random_password.stele_etl.result
    },
    {
      name  = "STELE_ANALYST_PASSWORD"
      value = random_password.stele_analyst.result
    },
    {
      name  = "STELE_PII_REVIEWER_PASSWORD"
      value = random_password.stele_pii_reviewer.result
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

# Optional public domain on Railway's *.up.railway.app. Omitted when web_subdomain
# is empty (e.g. you attach a custom domain out of band).
resource "railway_service_domain" "web" {
  count          = var.web_subdomain != "" ? 1 : 0
  subdomain      = var.web_subdomain
  environment_id = railway_project.stele.default_environment.id
  service_id     = railway_service.web.id
}
