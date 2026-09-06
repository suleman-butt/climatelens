# Architecture Decisions

## 2026-09-06: Local-first implementation

The project will be implemented and tested locally before cloud resources are used. This reduces cost and makes data, feature, model, and API behavior easier to verify independently.

## 2026-09-06: Four forecast targets

The first release keeps pollen, frost, solar, and wind so the project demonstrates three application perspectives. Pollen is subject to a feasibility check because DWD historical pollen data is a gridded index rather than consistent city-level ground truth. Any proxy will be labelled clearly.

## 2026-09-06: Simplified initial compute

The first cloud deployment will use one Cloud Run service with protected job endpoints. Separate Cloud Run Jobs can be introduced after the end-to-end workflow is stable or execution limits require them.

## 2026-09-06: Cloudflare DNS

Cloudflare remains authoritative for `sulemanb.com`. The `climatelens.sulemanb.com` record will point to Cloud Run while Google manages the HTTPS certificate.

## 2026-09-06: No service-account keys

GitHub Actions will authenticate to Google Cloud using Workload Identity Federation. Long-lived service-account JSON keys are excluded to reduce credential-leakage risk.
