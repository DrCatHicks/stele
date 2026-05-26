# Provider + version pins for the Railway deployment (M7.4).
#
# Railway is the first concrete target; AWS / GCP modules are deferred and would
# live as sibling directories under infra/, satisfying the same cloud-neutral
# variable interface (see variables.tf). A single cross-cloud module is a myth —
# the shared surface is the variable names, not the resources.
terraform {
  required_version = ">= 1.6.0" # OpenTofu 1.6+

  required_providers {
    railway = {
      source  = "terraform-community-providers/railway"
      version = "~> 0.6"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

# The provider reads its API token from the RAILWAY_TOKEN environment variable.
# Use a workspace/account token (not a project token) so it can create projects.
# Never commit the token; export it in the shell that runs `tofu`.
provider "railway" {}
