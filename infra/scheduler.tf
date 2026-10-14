# Daily: fetch the latest DWD days and rebuild the feature table.
resource "google_cloud_scheduler_job" "ingest" {
  name             = "climatelens-daily-ingest"
  description      = "Fetch recent DWD observations and refresh features"
  region           = var.region
  schedule         = var.ingest_schedule
  time_zone        = "Europe/Berlin"
  attempt_deadline = "600s"

  retry_config {
    retry_count          = 2
    min_backoff_duration = "300s"
  }

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.app.uri}/jobs/ingest"
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = local.jobs_audience
    }
  }

  depends_on = [google_project_service.apis]
}

# Monthly: walk-forward evaluation, refit and publish a new model bundle.
resource "google_cloud_scheduler_job" "train" {
  name             = "climatelens-monthly-train"
  description      = "Evaluate, retrain and publish a new model bundle"
  region           = var.region
  schedule         = var.train_schedule
  time_zone        = "Europe/Berlin"
  attempt_deadline = "1800s"

  retry_config {
    retry_count          = 1
    min_backoff_duration = "600s"
  }

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.app.uri}/jobs/train"
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = local.jobs_audience
    }
  }

  depends_on = [google_project_service.apis]
}
