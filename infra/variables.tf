variable "project_id" {
  description = "GCP project that hosts ClimateLens."
  type        = string
  default     = "climatelens-iu"
}

variable "region" {
  description = "Region for every regional resource."
  type        = string
  default     = "europe-west3"
}

variable "budget_amount" {
  description = "Monthly budget alert amount, in the billing account's currency."
  type        = number
  default     = 10
}

variable "gitlab_project_path" {
  description = "GitLab project allowed to deploy through Workload Identity Federation."
  type        = string
  default     = "sulemanb/devops-cloud/climatelens"
}

variable "image" {
  description = <<-EOT
    Container image for the first deployment. CI deploys new images afterwards,
    so later changes to the service image are ignored by Terraform.
  EOT
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "ingest_schedule" {
  description = <<-EOT
    Cron (Europe/Berlin) for the daily DWD ingest + feature refresh. DWD
    publishes a day's observations during the following day (seen between
    07:30 and 12:46 UTC), so the run is in the afternoon to pick up yesterday.
  EOT
  type        = string
  default     = "30 15 * * *"
}

variable "train_schedule" {
  description = "Cron (Europe/Berlin) for the monthly retrain."
  type        = string
  default     = "0 11 1 * *"
}

variable "custom_domain" {
  description = "Public hostname served by Firebase Hosting in front of Cloud Run (DNS at Cloudflare, DNS-only)."
  type        = string
  default     = "climatelens.sulemanb.com"
}
