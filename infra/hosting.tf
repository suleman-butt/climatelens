# Custom domain: Cloud Run domain mapping is not available in europe-west3, so
# Firebase Hosting fronts the service. Hosting forwards every request to Cloud
# Run, provisions and renews a Google-managed certificate for the custom
# domain, and does not cache dynamic responses (Cache-Control: private).
# Cloudflare only holds the DNS records Firebase asks for (DNS-only, no proxy).

resource "google_project_service" "firebase" {
  for_each           = toset(["firebase.googleapis.com", "firebasehosting.googleapis.com"])
  service            = each.value
  disable_on_destroy = false

  depends_on = [google_billing_budget.monthly]
}

resource "google_firebase_project" "this" {
  provider = google-beta
  project  = var.project_id

  depends_on = [google_project_service.firebase]
}

resource "google_firebase_hosting_site" "site" {
  provider = google-beta
  project  = var.project_id
  site_id  = var.project_id

  depends_on = [google_firebase_project.this]
}

# Every path goes to the Cloud Run service; Hosting serves no static files.
resource "google_firebase_hosting_version" "cloud_run" {
  provider = google-beta
  site_id  = google_firebase_hosting_site.site.site_id

  config {
    rewrites {
      glob = "**"
      run {
        service_id = google_cloud_run_v2_service.app.name
        region     = var.region
      }
    }
  }
}

resource "google_firebase_hosting_release" "cloud_run" {
  provider     = google-beta
  site_id      = google_firebase_hosting_site.site.site_id
  version_name = google_firebase_hosting_version.cloud_run.name
  message      = "Route all requests to the climatelens Cloud Run service"
}

resource "google_firebase_hosting_custom_domain" "domain" {
  provider      = google-beta
  project       = var.project_id
  site_id       = google_firebase_hosting_site.site.site_id
  custom_domain = var.custom_domain
  # DNS is set by hand at Cloudflare from the records below; do not block apply.
  wait_dns_verification = false
}
