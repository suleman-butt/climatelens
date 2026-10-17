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

output "hosting_default_url" {
  description = "Firebase Hosting URL (works before the custom domain is live)."
  value       = "https://${google_firebase_hosting_site.site.site_id}.web.app"
}

output "custom_domain_dns_records" {
  description = "Records to create at Cloudflare (DNS-only) for the custom domain."
  value       = google_firebase_hosting_custom_domain.domain.required_dns_updates
}
