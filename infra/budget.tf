# Created before any API that can cost money is enabled (see apis.tf).
resource "google_billing_budget" "monthly" {
  billing_account = data.google_project.this.billing_account
  display_name    = "${var.project_id} monthly budget"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
    # Count usage before credits: on a trial account the credits would otherwise
    # cover everything and the alert would never fire.
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      # Currency follows the billing account.
      units = tostring(var.budget_amount)
    }
  }

  # Billing account admins (the project owner) get these by email.
  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }
}
