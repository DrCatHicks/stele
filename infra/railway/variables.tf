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

variable "image" {
  description = "Container image (without tag) both the web and ETL services run. Built and pushed once by CI (D7); the services pull it instead of each rebuilding the Dockerfile from source. GHCR namespace defaults to this repo."
  type        = string
  default     = "ghcr.io/countercheck/stele"
}

variable "image_tag" {
  description = "Tag of var.image to deploy. CI pushes a floating `main` tag plus an immutable `<commit-sha>` tag on every push to main. Default `main` tracks the branch; pin to a sha (tofu apply -var image_tag=<sha>) for a reproducible rollout — changing this string is what triggers Railway to redeploy the new image."
  type        = string
  default     = "main"
}

variable "deploy_latest" {
  description = "Opt-in 'ship the current build' switch. Off (default): the services deploy var.image:var.image_tag verbatim, so a plain `tofu apply` is a no-op even after CI re-pushes the floating :main tag (the string is unchanged) — deploys stay deterministic. On (`tofu apply -var deploy_latest=true`): resolve the live digest of var.image:var.image_tag (default :main) and deploy that immutable digest, so apply ships whatever main currently points at, redeploying only when the digest actually moved. Pinning -var image_tag=<sha> still works under either setting."
  type        = bool
  default     = false
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

# The app image is published to a PUBLIC GHCR package, so Railway pulls it without
# credentials and these stay empty (the repo is public — the image exposes nothing
# the source doesn't). To make it private later: flip the GHCR package visibility,
# then set these two (username = a GitHub user/org, password = a read:packages PAT)
# and re-apply. No module change — just config. Empty = anonymous pull.
variable "image_registry_username" {
  description = "Registry username for pulling a PRIVATE app image (GitHub username/org). Empty = anonymous pull (public package)."
  type        = string
  default     = ""
}

variable "image_registry_password" {
  description = "Registry password/token for pulling a PRIVATE app image (a read:packages PAT). Empty = anonymous pull (public package)."
  type        = string
  default     = ""
  sensitive   = true
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

variable "enable_postgres_proxy" {
  description = "Expose Postgres to the public internet via a Railway TCP proxy, so analysts/reviewers can connect with their M3.5 per-user credentials from outside the project's private network. Default OFF: a standing public 5432 is a deliberate, documented exposure, flipped on only when someone actually needs direct warehouse access. See docs/verification/m7.6-demo-to-prod.md."
  type        = bool
  default     = false
}

# ---- Operator-supplied secret overrides (rotation / real-prod path) ----------
#
# By default every role password is GENERATED (random_password, in state) — the
# convenient demo path. But because bootstrap_roles.py never re-passwords an
# existing role (M7.3: re-deploys need no secrets), regenerating the tofu secret
# alone would NOT change the live Postgres password — it would only update the
# connection strings, breaking auth. So rotation is a two-step: ALTER ROLE in
# Postgres (scripts/rotate_role_password.py), then feed the new password back here
# so the services' connection strings match. A non-empty override wins over the
# generated value (see locals in main.tf). These also serve the "operator-supplied
# secrets, never born in state" posture for real production. Empty = generated.
# See docs/verification/m7.6-demo-to-prod.md § Secret rotation.

variable "admin_password_override" {
  description = "Operator-supplied admin/owner (postgres) password. Empty = generate one. Set to the value rotate_role_password.py applied, then apply, so the migrate path keeps working."
  type        = string
  default     = ""
  sensitive   = true
}

variable "stele_api_password_override" {
  description = "Operator-supplied stele_api password. Empty = generate one. Set to the rotated value so the web runtime connection string matches the live role."
  type        = string
  default     = ""
  sensitive   = true
}

variable "stele_etl_password_override" {
  description = "Operator-supplied stele_etl password. Empty = generate one. Set to the rotated value so the ETL cron's connection string + dbt profile match the live role."
  type        = string
  default     = ""
  sensitive   = true
}

variable "stele_analyst_password_override" {
  description = "Operator-supplied stele_analyst group-role password. Empty = generate one. Set to the rotated value to keep `tofu output stele_analyst_password` in sync with the live role."
  type        = string
  default     = ""
  sensitive   = true
}

variable "stele_pii_reviewer_password_override" {
  description = "Operator-supplied stele_pii_reviewer group-role password. Empty = generate one. Set to the rotated value to keep `tofu output stele_pii_reviewer_password` in sync with the live role."
  type        = string
  default     = ""
  sensitive   = true
}

variable "provision_encryption_key" {
  description = <<-EOT
    Fernet key (STELE_ENCRYPTION_KEY) that the provisioning worker uses to encrypt a
    freshly-minted DB password at rest and the web service uses to decrypt it on the
    one-time reveal (design doc §3.10 revision). MUST be a urlsafe-base64 32-byte key:
    generate one with
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    Leaving it empty falls back to an INSECURE built-in dev key — never do that in a
    real deploy. Operator-supplied (never born in tofu state), like the password
    overrides above.
  EOT
  type        = string
  default     = ""
  sensitive   = true
}
