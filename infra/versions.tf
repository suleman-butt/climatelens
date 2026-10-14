terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  # Created by infra/bootstrap. State is versioned, so a bad apply can be rolled back.
  backend "gcs" {
    bucket = "climatelens-iu-tfstate"
    prefix = "main"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  # With personal (ADC) credentials, APIs such as Billing Budgets need an
  # explicit quota project.
  user_project_override = true
  billing_project       = var.project_id
}

data "google_project" "this" {
  project_id = var.project_id
}
