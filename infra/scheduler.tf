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

# Keep-warm: a cheap GET on the public /health endpoint during the day so
# visitors rarely meet a cold start. Each ping is billed for well under a
# second of request time; this is the 3rd job, inside Scheduler's free tier.
resource "google_cloud_scheduler_job" "keep_warm" {
  name             = "climatelens-keep-warm"
  description      = "Ping /health during the day to keep one instance warm"
  region           = var.region
  schedule         = var.keep_warm_schedule
  time_zone        = "Europe/Berlin"
  attempt_deadline = "30s"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "GET"
    uri         = "${google_cloud_run_v2_service.app.uri}/health"
  }

  depends_on = [google_project_service.apis]
}
