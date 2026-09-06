# Architecture Decisions

## 2026-09-06: Local-first implementation

The project will be implemented and tested locally before cloud resources are used. This reduces cost and makes data, feature, model, and API behavior easier to verify independently.

## 2026-09-06: Three forecast targets, pollen dropped

The project ships **three** next-day targets across **two** angles:

| Target | Angle | Type | Source field(s) |
|---|---|---|---|
| Frost risk | Agriculture | Classification | `temperature_air_min_2m < 0` |
| Solar potential | Energy | Regression | `radiation_global` (J/cm2/day -> kWh/m2) |
| Wind potential | Energy | Regression | `wind_speed` -> power proxy (proportional to v^3) |

**Pollen (Health angle) is dropped.** DWD's `wetterdienst` observation API
exposes no pollen data; the only source is DWD's separate regional pollen-index
archive (8 coarse regions, not city-level). Rather than ingest a second,
mismatched data source or fabricate a synthetic proxy that would need its own
caveats, the health angle is removed. The frost and wind targets are built from
fields that are ~99-100% complete; solar is ~85-92% complete (see below).

This keeps the pipeline free of imputation: features use only near-complete
fields, and rows whose target is missing are simply excluded from that target's
walk-forward train/test (not imputed). The cloud architecture - the actual
subject of the deliverable - is unaffected.

## 2026-09-06: Feature fields restricted to near-complete columns

`sunshine_duration` is 100% missing for Berlin and Leipzig and 79% for Frankfurt
(those primary stations don't report it), so it is excluded as a feature -
`cloud_cover_total` (~99% present) carries the same signal.
`radiation_sky_long_wave` (34% null) and `radiation_sky_short_wave_diffuse`
(9% null) are not used. `snow_depth` (6% null) is excluded. Feature inputs:
`temperature_air_{mean,min,max}_2m`, `temperature_air_min_0_05m`, `wind_speed`,
`wind_gust_max`, `humidity`, `pressure_air_site`, `cloud_cover_total`,
`precipitation_height`, plus calendar and fixed station metadata.

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
data-availability limitation and documented per city - not a defect.

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
