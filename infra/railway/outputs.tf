# Outputs for the Railway deployment (M7.4).
#
# The secret outputs let the operator retrieve generated credentials without
# digging through state — e.g. `tofu output -raw stele_analyst_password` to hand
# an analyst their direct-to-warehouse login (the M3.5 DB-credential model). They
# are marked sensitive, so `tofu output` redacts them unless `-raw` is used.

output "web_url" {
  description = "Public URL of the web service, or null when no Railway domain is attached."
  value       = var.web_subdomain != "" ? "https://${railway_service_domain.web[0].domain}" : null
}

output "postgres_internal_host" {
  description = "Private-network host:port for Postgres (reachable from other services in the project)."
  value       = "${local.pg_host}:${local.pg_port}"
}

output "postgres_proxy" {
  description = "Public host:port of the Postgres TCP proxy for analyst/reviewer direct access, or null when enable_postgres_proxy is false."
  value       = var.enable_postgres_proxy ? "${railway_tcp_proxy.postgres[0].domain}:${railway_tcp_proxy.postgres[0].proxy_port}" : null
}

# These read the EFFECTIVE-password locals (override-or-generated), not the raw
# random_password, so after a rotation (ALTER ROLE + set the matching
# *_password_override + apply) the output reads back the live password — for the
# analyst/reviewer group roles the output IS the credential-delivery path.
output "admin_database_password" {
  description = "Postgres owner (admin) password — used by the migrate/bootstrap path."
  value       = local.admin_password
  sensitive   = true
}

output "stele_analyst_password" {
  description = "Password for the stele_analyst role (direct read access to marts)."
  value       = local.stele_analyst_password
  sensitive   = true
}

output "stele_pii_reviewer_password" {
  description = "Password for the stele_pii_reviewer role (direct read access to pii)."
  value       = local.stele_pii_reviewer_password
  sensitive   = true
}
