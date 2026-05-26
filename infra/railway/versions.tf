# Provider + version pins for the Railway deployment (M7.4).
#
# Railway is the first concrete target; AWS / GCP modules are deferred and would
# live as sibling directories under infra/, satisfying the same cloud-neutral
# variable interface (see variables.tf). A single cross-cloud module is a myth —
# the shared surface is the variable names, not the resources.
terraform {
  required_version = ">= 1.6.0" # OpenTofu 1.6+

  # State holds every generated secret. For the demo it's local + gitignored
  # (infra/README.md § state holds secrets). Before real prod / multi-operator
  # use, move to an encrypted remote backend: uncomment + fill in, then
  # `tofu init -migrate-state`. See docs/verification/m7.6-demo-to-prod.md.
  # backend "s3" {
  #   bucket  = "stele-tofu-state"
  #   key     = "railway/production.tfstate"
  #   region  = "us-east-1"
  #   encrypt = true
  # }

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
