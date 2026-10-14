output "service_url" {
  value = google_cloud_run_v2_service.app.uri
}

output "image_repo" {
  description = "Push images here: <image_repo>:<tag>"
  value       = local.image_repo
}

output "data_bucket" {
  value = google_storage_bucket.data.name
}

output "models_bucket" {
  value = google_storage_bucket.models.name
}

output "deployer_service_account" {
  value = google_service_account.deployer.email
}

output "workload_identity_provider" {
  description = "Audience / provider for GitLab CI id_tokens (Phase 8)."
  value       = google_iam_workload_identity_pool_provider.gitlab.name
}
