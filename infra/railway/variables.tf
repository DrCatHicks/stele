# Inputs for the Railway deployment (M7.4).
#
# The first group is the cloud-neutral interface: any future AWS / GCP module
# under infra/ should accept these same names, so the caller's intent ("deploy
# branch X of repo Y to region Z as project P") is portable even though the
# resources behind them are not. The second group is Railway-specific.

# ---- Cloud-neutral interface -------------------------------------------------

variable "project_name" {
  description = "Name of the project / deployment."
  type        = string
  default     = "stele"
}

variable "environment_name" {
  description = "Name of the default environment (e.g. production, staging)."
  type        = string
  default     = "production"
}

variable "region" {
  description = "Region to run the workloads in. Railway metal regions: us-west2, us-east4, europe-west4, asia-southeast1."
  type        = string
  default     = "us-east4"
}

variable "source_repo" {
  description = "GitHub repo (owner/name) Railway builds the web service from. The repo must have the Railway GitHub app installed."
  type        = string
  default     = "countercheck/stele"
}

variable "source_repo_branch" {
  description = "Branch Railway deploys."
  type        = string
  default     = "main"
}

variable "database_name" {
  description = "Postgres database the app and migrations use."
  type        = string
  default     = "stele"
}

# ---- Railway-specific --------------------------------------------------------

variable "workspace_id" {
  description = "Railway workspace to create the project in. Empty = the token's default workspace."
  type        = string
  default     = ""
}

variable "postgres_image" {
  description = "Docker image for the managed Postgres service. Railway's SSL-enabled Postgres 16 image."
  type        = string
  default     = "ghcr.io/railwayapp-templates/postgres-ssl:16"
}

variable "web_subdomain" {
  description = "Subdomain for the Railway-provided *.up.railway.app domain on the web service. Empty = no public domain (internal only)."
  type        = string
  default     = ""
}

variable "postgres_volume_mount_path" {
  description = "Where the Postgres data volume mounts. Must match the image's PGDATA parent."
  type        = string
  default     = "/var/lib/postgresql/data"
}

variable "cookie_secure" {
  description = "Whether the session cookie sets the Secure flag (HTTPS only). True in any real deploy; the web service serves over Railway's HTTPS edge."
  type        = bool
  default     = true
}

variable "etl_cron_schedule" {
  description = "Cron expression (UTC) for the ETL service's scheduled `dbt build`. A full-refresh build at demo scale (10-20k respondents) is cheap; default is once daily at 06:00 UTC."
  type        = string
  default     = "0 6 * * *"
}
