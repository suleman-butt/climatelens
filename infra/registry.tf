resource "google_artifact_registry_repository" "images" {
  repository_id = "climatelens"
  location      = var.region
  format        = "DOCKER"
  description   = "ClimateLens container images"

  # Images are ~0.5 GB compressed; keep the 10 most recent, delete the rest.
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30 days
    }
  }

  depends_on = [google_project_service.apis]
}

locals {
  image_repo = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/climatelens"
}
