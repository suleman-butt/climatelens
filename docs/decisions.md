# Architecture Decisions

## 2026-09-06: Local-first implementation

The project will be implemented and tested locally before cloud resources are used. This reduces cost and makes data, feature, model, and API behavior easier to verify independently.

## 2026-09-06: Four forecast targets

The first release keeps pollen, frost, solar, and wind so the project demonstrates three application perspectives. Pollen is subject to a feasibility check because DWD historical pollen data is a gridded index rather than consistent city-level ground truth. Any proxy will be labelled clearly.

## 2026-09-06: Simplified initial compute

The first cloud deployment will use one Cloud Run service with protected job endpoints. Separate Cloud Run Jobs can be introduced after the end-to-end workflow is stable or execution limits require them.

## 2026-09-06: Cloudflare DNS

Cloudflare remains authoritative for `sulemanb.com`. The `climatelens.sulemanb.com` record will point to Cloud Run while Google manages the HTTPS certificate.

## 2026-09-06: Frozen city -> station mapping in code

`config.CITIES` hardcodes the 12 city -> DWD station assignments (primary +
solar, see below) with the selection date. It is never resolved at runtime.
`climatelens stations`
exists only to *check* the frozen table against live DWD and propose changes,
which are applied by editing `config.py` and logging the change. Rationale: a
reproducible station set matters more than always using the currently-closest
station (spec 4.1).

## 2026-09-06: Two DWD stations per city

Only 56 DWD stations nationwide report daily global radiation, against 1284 for
`CLIMATE_SUMMARY`, so the spec's rule (temperature + wind + radiation within
15 km) cannot be met for most large cities. Each city therefore has:

- a **primary station** - nearest active station with >= 3 years of history that
  actually reports wind and pressure (not just temperature and precipitation),
  used for temperature, wind, humidity, pressure, sunshine and cloud;
- a **solar station** - nearest active radiation station, used only for the solar
  target.

For 5 cities the solar station is 17-60 km away (Cologne 60 km is the worst).
Daily global radiation is spatially smooth, so this is treated as an accepted
data-availability limitation and documented per city, analogous to the pollen
proxy - not a defect.

`Dortmund` was replaced by `Bremen`: no station reporting wind + pressure exists
within 30 km of Dortmund, whereas Bremen (11th most populous city) has one
station 3.8 km from centre covering the full climate set and radiation.

## 2026-09-06: wetterdienst pinned to 0.136.0, not the spec's 0.97.0

`wetterdienst==0.97.0` can no longer fetch from `opendata.dwd.de` (malformed
metadata URL + outdated directory-listing parser). Upgraded to 0.136.0 (pulling
`pyarrow` to 25.0.1). A pinned dependency that cannot retrieve data provides no
reproducibility, so this is a deliberate, documented deviation. The fetch layer
was rewritten for the 0.136 API.

## 2026-09-06: No service-account keys

GitHub Actions will authenticate to Google Cloud using Workload Identity Federation. Long-lived service-account JSON keys are excluded to reduce credential-leakage risk.
