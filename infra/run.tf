locals {
  # Audience Cloud Scheduler puts in its OIDC tokens; the app accepts only this.
  jobs_audience = "climatelens-jobs"
}

resource "google_cloud_run_v2_service" "app" {
  name                = "climatelens"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.runtime.email
    # /jobs/train evaluates and retrains in the request (a few minutes).
    timeout                          = "900s"
    max_instance_request_concurrency = 20

    scaling {
      min_instance_count = 0 # scale to zero when idle: no cost without traffic
      max_instance_count = 2
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        cpu_idle          = true # CPU billed only while handling requests
        startup_cpu_boost = true
      }

      env {
        name  = "CLIMATELENS_ENV"
        value = "cloud"
      }
      env {
        name  = "CLIMATELENS_GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "CLIMATELENS_GCP_REGION"
        value = var.region
      }
      env {
        name  = "CLIMATELENS_DATA_BUCKET"
        value = google_storage_bucket.data.name
      }
      env {
        name  = "CLIMATELENS_MODELS_BUCKET"
        value = google_storage_bucket.models.name
      }
      env {
        name  = "CLIMATELENS_JOBS_OIDC_AUDIENCE"
        value = local.jobs_audience
      }
      env {
        name  = "CLIMATELENS_JOBS_OIDC_SERVICE_ACCOUNT"
        value = google_service_account.scheduler.email
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 2
        period_seconds        = 3
        failure_threshold     = 10
      }
    }
  }

  # CI deploys new images (Phase 8); Terraform owns everything else.
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [google_project_service.apis]
}

# The dashboard and read-only API are public; /jobs/* is protected by the app.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.app.name
  location = google_cloud_run_v2_service.app.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
