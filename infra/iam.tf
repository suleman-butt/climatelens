# --- Service accounts: one per job, least privilege -------------------------------

# Identity the Cloud Run service runs as: reads and writes the two buckets only.
resource "google_service_account" "runtime" {
  account_id   = "climatelens-run"
  display_name = "ClimateLens Cloud Run runtime"
}

# Identity Cloud Scheduler uses to sign the OIDC tokens it sends to /jobs/*.
resource "google_service_account" "scheduler" {
  account_id   = "climatelens-scheduler"
  display_name = "ClimateLens Cloud Scheduler caller"
}

# Identity GitLab CI impersonates (via Workload Identity Federation) to deploy.
resource "google_service_account" "deployer" {
  account_id   = "climatelens-deployer"
  display_name = "ClimateLens GitLab CI deployer"
}

resource "google_storage_bucket_iam_member" "runtime_data" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime_models" {
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# Deployer: push images, roll out new revisions of this one service, and run
# them as the runtime identity - nothing else in the project.
resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  repository = google_artifact_registry_repository.images.name
  location   = google_artifact_registry_repository.images.location
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_cloud_run_v2_service_iam_member" "deployer_run" {
  name     = google_cloud_run_v2_service.app.name
  location = google_cloud_run_v2_service.app.location
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

# --- Workload Identity Federation: GitLab CI -> GCP without keys -------------------

resource "google_iam_workload_identity_pool" "gitlab" {
  workload_identity_pool_id = "gitlab"
  display_name              = "GitLab CI"
  description               = "Short-lived credentials for GitLab CI pipelines"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "gitlab" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.gitlab.workload_identity_pool_id
  workload_identity_pool_provider_id = "gitlab-com"
  display_name                       = "gitlab.com"

  oidc {
    issuer_uri = "https://gitlab.com"
  }

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.project_path"  = "assertion.project_path"
    "attribute.ref"           = "assertion.ref"
    "attribute.ref_type"      = "assertion.ref_type"
    "attribute.ref_protected" = "assertion.ref_protected"
  }

  # Only this project's pipelines on protected branches/tags (e.g. main) may
  # exchange their GitLab token; forks and unprotected branches are rejected.
  attribute_condition = "assertion.project_path == \"${var.gitlab_project_path}\" && assertion.ref_protected == \"true\""
}

resource "google_service_account_iam_member" "gitlab_impersonates_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.gitlab.name}/attribute.project_path/${var.gitlab_project_path}"
}
