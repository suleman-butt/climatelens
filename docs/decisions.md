# Decision Log

This log records the significant technical decisions taken while building
ClimateLens, in the order they were made: the context, the alternatives that
were considered and why the chosen option won. Entries are kept as written at
the time; when a later decision changes an earlier one, both remain and the
status below says so.

| Date | Decision | Status |
|---|---|---|
| 2026-09-06 | Local-first implementation | Done |
| 2026-09-06 | Three forecast targets, pollen dropped | Active |
| 2026-09-06 | Feature fields restricted to near-complete columns | Active |
| 2026-09-06 | Simplified initial compute | Active |
| 2026-09-06 | Cloudflare DNS | Active - Firebase Hosting added in front (17 Oct) |
| 2026-09-06 | Frozen city -> station mapping in code | Active |
| 2026-09-06 | Two DWD stations per city | Active |
| 2026-09-06 | wetterdienst pinned to 0.136.0, not the originally planned 0.97.0 | Active |
| 2026-09-06 | No service-account keys | Active - implemented with Workload Identity Federation |
| 2026-10-14 | GitLab as primary repository and CI/CD platform | Active |
| 2026-10-14 | Feature table design | Partly superseded - next-day target by 1-3 day horizons (17 Oct); wind v^3 by outdoor framing |
| 2026-10-14 | Walk-forward evaluation and model selection | Active |
| 2026-10-14 | API job endpoints authenticated in the application | Active - scheduler OIDC tokens |
| 2026-10-14 | Forecasts are served even when data is stale | Active - refined by horizon choice (17 Oct) |
| 2026-10-14 | Wind reframed from energy to outdoor conditions | Active |
| 2026-10-14 | Dashboard written for non-specialists | Active |
| 2026-10-14 | Hash-locked dependencies | Active |
| 2026-10-14 | Container verified in CI | Active |
| 2026-10-14 | No CI pipeline for documentation-only pushes | Active |
| 2026-10-14 | Cloud architecture as deployed (supersedes earlier plans) | Active |
| 2026-10-14 | Semantic versions, immutable image tags | Active |
| 2026-10-14 | Smaller runtime image | Active |
| 2026-10-14 | Retrain monthly, refresh data daily | Active |
| 2026-10-17 | Forecast tomorrow with 1-3 day horizons (supersedes "next-day") | Active |
| 2026-10-17 | Releases from version tags in GitLab CI | Active |
| 2026-10-17 | Custom domain through Firebase Hosting | Active |
| 2026-10-17 | Fast cold starts without an always-on instance | Active |

---

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
station.

## 2026-09-06: Two DWD stations per city

Only 56 DWD stations nationwide report daily global radiation, against 1284 for
`CLIMATE_SUMMARY`, so the planned rule (temperature + wind + radiation within
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

## 2026-09-06: wetterdienst pinned to 0.136.0, not the originally planned 0.97.0

`wetterdienst==0.97.0` can no longer fetch from `opendata.dwd.de` (malformed
metadata URL + outdated directory-listing parser). Upgraded to 0.136.0 (pulling
`pyarrow` to 25.0.1). A pinned dependency that cannot retrieve data provides no
reproducibility, so this is a deliberate, documented deviation. The fetch layer
was rewritten for the 0.136 API.

## 2026-09-06: No service-account keys

CI/CD will authenticate to Google Cloud using Workload Identity Federation. Long-lived service-account JSON keys are excluded to reduce credential-leakage risk.

*Updated 2026-10-14:* the CI/CD platform is now GitLab (see below). Pipeline jobs
request a GitLab OIDC token (`id_tokens`), which a GCP Workload Identity pool
provider trusts for `gitlab.com`, restricted to this project path and to
protected refs for deployment.

## 2026-10-14: GitLab as primary repository and CI/CD platform

The canonical repository is `gitlab.com/sulemanb/devops-cloud/climatelens`. All
pushes, merge requests and pipelines run on GitLab; the GitHub repository
(`github.com/suleman-butt/climatelens`) is kept as a read-only push mirror fed by
GitLab, so it is never pushed to directly.

Consequences:

- CI/CD is defined in `.gitlab-ci.yml` instead of GitHub Actions workflows.
- Workload Identity Federation uses GitLab's OIDC issuer (`https://gitlab.com`)
  instead of GitHub's; the no-JSON-keys principle is unchanged.
- Production infrastructure approval moves to GitLab protected environments with
  a manual `terraform apply` job.

## 2026-10-14: Feature table design

- **One table, all targets.** Row `t` holds features from data up to day `t` and
  the three targets from day `t + 1`. Rows with a missing target stay in the
  table (as NaN) and are dropped per target at training time
  (`features.target_frame`), so one feature build serves all three models and no
  target is imputed. The last row per city has no target and is the live
  prediction input.
- **Calendar-correct shifts.** Each city is re-indexed onto a full daily
  calendar before lags, rolling windows and the target shift. A missing day then
  yields NaN instead of silently treating "two days later" as "tomorrow".
  Rolling means need just over half the window (`window // 2 + 1`) to be
  present.
- **Wind target is mean wind speed in m/s, not v^3.** Wind power potential is
  proportional to v^3, but v^3 is heavy-tailed and its errors are hard to
  interpret. The model predicts v (MAE/RMSE in m/s); the dashboard derives the
  power proxy with `wind_power_proxy(v) = v^3`.
- **Same-day target values kept beside the targets** (`frost`, `solar_kwh_m2`,
  `wind_speed`) for the persistence baseline. `solar_kwh_m2` is not a model
  feature, because the solar station may be up to 60 km away and lags ~6 weeks.
- **City identity enters only through station metadata** (lat/lon/elevation),
  not a one-hot city column, so the model shares structure across cities.

## 2026-10-14: Walk-forward evaluation and model selection

- **Monthly expanding folds.** Each fold trains on every row before a month
  boundary and tests on that month; the first test month starts after >= 365
  days of history so climatology has seen every season. Monthly refits match a
  realistic retraining cadence and give 24 folds over two years.
- **No gap between train and test.** Train row `t` carries the observation of
  day `t + 1`; at forecast time on day `t + 1` that observation is already
  known, so it is not leakage. A spy test enforces train-before-test per fold.
- **Primary metrics:** Brier score for frost (accuracy is reported but
  flattered by class imbalance) and MAE for solar and wind. Skill is reported
  against the strongest relevant reference: climatology for frost probability,
  persistence for the regressions.
- **The served model per target is the walk-forward winner**, refit on all rows
  with a known target. If a baseline ever beats LightGBM, the baseline is
  served - the bundle manifest records which model was chosen and why.
- **Fixed LightGBM hyperparameters, no tuning.** With ~2 years of test data,
  tuning on the walk-forward folds would overfit the evaluation itself. Small,
  regularised trees (15 leaves, 400 rounds, lr 0.03, subsampling) with
  `deterministic=True` and a single thread, so a retrain on the same data
  reproduces the same model.
- **Immutable, versioned bundles** with a `LATEST` pointer written last, so a
  reader never sees a half-written bundle and any served forecast traces back
  to its training data, parameters and evaluation.

## 2026-10-14: API job endpoints authenticated in the application

The dashboard and forecast API must be public, while `/jobs/*` must not be.
Cloud Run IAM applies to a whole service, so with one service (see "Simplified
initial compute") the job endpoints authenticate in the application:

- Locally and as a first layer, a bearer token (`CLIMATELENS_JOBS_TOKEN`,
  compared in constant time). With no token configured the endpoints are
  disabled, so a misconfigured deployment fails closed.
- In the cloud (Phase 7) Cloud Scheduler will call them with a Google-signed
  OIDC ID token; the check will verify issuer, audience and the scheduler
  service account instead of a shared secret, with the token kept in Secret
  Manager only as long as it is still needed.

Jobs run synchronously inside the request (ingest seconds, train ~2-3 min,
within Cloud Run's request timeout), one at a time, and invalidate the cached
forecast service so the next request serves the new data or bundle.

## 2026-10-14: Forecasts are served even when data is stale

If DWD data is late, each city's forecast is still issued from its most recent
observations, with `valid_for`, `data_age_days` and a `stale` flag (> 3 days),
and the dashboard shows a banner. Hiding forecasts would make a data outage look
like a service outage; labelling them keeps the page honest and usable.

## 2026-10-14: Wind reframed from energy to outdoor conditions

Solar already covers the energy angle, so presenting wind as a second energy
target (power proxy v^3) was redundant for dashboard users. Wind is now the
**Outdoors** perspective: the next-day mean wind speed in km/h with its
Beaufort description ("Gentle breeze", "Strong breeze", ...). The model and
target are unchanged (mean wind speed in m/s); only the presentation changed.
This supersedes the dashboard part of "Feature table design": the v^3 power
proxy (`wind_power_proxy`) and the `power_*` API fields were removed.

The Beaufort label is chosen from the *rounded km/h value shown on the page*
(official km/h bands), so two cities showing the same speed never get different
labels. The scale describes sustained wind; since the forecast is a daily mean,
gusts will be stronger - acceptable for an everyday "how windy" summary, and
the disclaimer rules out safety use.

## 2026-10-14: Dashboard written for non-specialists

Each card answers one question in plain words (frost unlikely / possible /
likely; sunnier than usual / about usual / duller than usual for the month;
Beaufort wind description) with one supporting number. Model accuracy is stated
once in the footer in everyday terms, not per card; evaluation detail (folds,
baselines, skill scores) stays on `/metrics` and in the project log. Users
compare up to three cities, selected in a sidebar and kept in the URL.

## 2026-10-14: Hash-locked dependencies

`pyproject.toml` pins direct dependencies only, so transitive ones (numpy,
scipy, polars, ...) floated between builds. Two lock files generated by
`uv pip compile --universal --generate-hashes` now pin everything:
`requirements.lock` for the runtime image and `requirements-dev.lock` for CI
and development. Installs use `pip install --require-hashes`, so a build
either gets exactly the tested packages or fails - which also guards against a
tampered package on the index. `--universal` keeps one lock valid on Linux
(CI, Cloud Run) and Windows (development). Plain pip and requirements files
were kept over a heavier tool so the Dockerfile and CI need nothing beyond pip;
uv is only needed to regenerate the locks.

## 2026-10-14: Container verified in CI

The production image is built and smoke-tested by a Docker-in-Docker CI job on
every push rather than only on a developer machine: it must run as the non-root
user, load LightGBM, serve `/healthz` on `$PORT` and fail closed (503) without a
model. Registry push and deployment build on this job in Phase 8.

## 2026-10-14: No CI pipeline for documentation-only pushes

The project runs on GitLab's free tier (about 400 CI minutes), and one full
pipeline (lint, test, Docker-in-Docker container build) costs several of them.
`workflow:rules` therefore starts a push or merge-request pipeline only when a
file that can change the build or tests changed: `src/`, `tests/`, `infra/`,
`pyproject.toml`, the lock files, `Dockerfile`, `.dockerignore`, `Makefile` or
`.gitlab-ci.yml`. Pushes that touch only Markdown (README, `docs/`) start no
pipeline. Manual, scheduled and API pipelines always run. GitLab's `changes:`
can only express "run if any of these changed", so the rule lists what triggers
a pipeline rather than what to skip; new top-level code folders must be added
to `.pipeline-triggers`.

## 2026-10-14: Cloud architecture as deployed (supersedes earlier plans)

- **No BigQuery.** The feature table is ~1 MB of Parquet (13,620 rows). It is
  stored in Cloud Storage next to the raw partitions and read whole by the
  service, so BigQuery would add a service, IAM and cost without benefit. The
  unused `bigquery_dataset` setting was removed. If the data grows by orders of
  magnitude, BigQuery (or external tables over the same Parquet) is the next
  step.
- **One storage interface for local and cloud.** `data.py` addresses every
  artefact by URI (local path or `gs://`); only a handful of helpers know the
  difference, so the same pipeline and tests cover both. Over GCS the ~1,100
  daily raw files are fetched with 16 parallel requests.
- **Cache refresh instead of instance coordination.** Each Cloud Run instance
  re-checks the `LATEST` model pointer and the feature table's object
  generation at most once a minute and reloads when either changed, so a job
  on one instance reaches all instances without any messaging.
- **Daily ingest catches up.** The incremental run resumes from the last stored
  day (capped at 60 days) instead of always fetching two days, so a missed run
  never leaves a permanent gap.
- **Two-stage Terraform.** `infra/bootstrap` (local state) creates only the
  versioned state bucket and core APIs; everything else lives in `infra/` with
  state in that bucket. The budget alert is created first and every billable
  API depends on it.
- **Budget counts usage before credits** (`EXCLUDE_ALL_CREDITS`): on a trial
  account the credits would otherwise cover all spend and the EUR 10 alert would
  never fire.
- **Terraform owns infrastructure, CI owns the image.** The Cloud Run service is
  created with Google's placeholder image and Terraform ignores later image
  changes; deployments update the image by digest.
- **`/health` as the public liveness path.** Cloud Run's front end reserves some
  paths ending in `z`; `/healthz` returns Google's own 404 from outside (the
  startup probe still reaches it inside). `/health` was added; `/healthz` stays
  for container probes and local use.

## 2026-10-14: Semantic versions, immutable image tags

- `climatelens.__version__` is the single source of the version; `pyproject.toml`
  reads it (`dynamic = ["version"]`), the image carries it as the OCI label
  `org.opencontainers.image.version`, and the dashboard footer shows it.
- A release is a git tag `vX.Y.Z` on the commit, and the image built from that
  commit is pushed as `vX.Y.Z`. 1.0.0 is the first release running on Google
  Cloud.
- Artifact Registry has **immutable tags**: once `v1.0.0` exists it cannot be
  moved to another image (verified - a re-push is rejected), so a version
  always identifies exactly one image. Deployments still pin the digest.
- Phase 8 moves building, pushing and deploying into GitLab CI on version tags.

## 2026-10-14: Smaller runtime image

The uncompressed venv was 962 MB (compressed image ~323 MB). Removed: test
suites bundled inside installed packages (~136 MB, e.g. `pandas/tests`), `pip`
from the runtime venv (13 MB), and the unused `google-cloud-bigquery` with its
gRPC stack. Result: venv 792 MB (-18%), compressed image ~278 MB (-14%),
verified by a live DWD ingest, feature build, model load and every endpoint
inside the slim image. Kept deliberately: precompiled `.pyc` files (~179 MB) -
the non-root container cannot write them, so dropping them would slow every
Cloud Run cold start. The large remaining packages (polars runtime for
`wetterdienst`, pyarrow, scipy, pandas, scikit-learn) are needed at runtime.

Note on sizes: Artifact Registry and `docker push` report the compressed size;
Docker Desktop's containerd image store reports compressed + unpacked size
together, which is why the same image showed 1.49 GB locally and 325 MB in the
registry.

## 2026-10-14: Retrain monthly, refresh data daily

The model is retrained once a month (Cloud Scheduler, 1st of the month 11:00
Europe/Berlin); new observations are ingested and features rebuilt every day.

Why monthly works for ClimateLens:

- **Forecasts are still daily.** The model encodes slowly changing weather
  relationships (e.g. clear, calm evenings after a cold day bring frost) and
  applies them every day to that day's fresh observations. The daily ingest,
  not the retrain, keeps the forecast current.
- **A month barely changes the model.** Training uses ~3 years (~1,100 days
  per city); one more month adds ~3 % of data, so daily or weekly retraining
  would mostly reproduce the same model.
- **The published accuracy assumes it.** Walk-forward evaluation retrains
  monthly and tests on the following month, so the reported scores (frost Brier
  skill +51 % vs climatology, solar and wind MAE skill +10.5 % and +9.9 % vs
  persistence) describe exactly this operating mode.
- **Solar data lags ~6 weeks at DWD**, so more frequent retraining adds no new
  solar targets.
- **Cost is not the constraint.** A retrain takes ~100 s on Cloud Run, well
  within the free tier; the choice is about value, not price.

Accepted limitation: a sudden change at a station (sensor outage, relocation)
is reflected in the model within a month at most. More frequent retraining
would not fix a broken sensor; data-quality checks and a quality gate on new
models are the better safeguards. Retrain frequency stays a single Scheduler
setting (`train_schedule` in `infra/variables.tf`) if evidence ever favours
weekly retraining.

## 2026-10-17: Forecast tomorrow with 1-3 day horizons (supersedes "next-day")

Observed on the live service: on 17 Oct the dashboard showed a forecast for
16 Oct. Cause: DWD publishes a day's daily observations during the *following*
day (16 Oct data was absent at 07:30 UTC on 17 Oct and present by 12:46 UTC),
and the model only ever predicted "the day after the newest observation".
With daily data the newest observation is at best yesterday, so a t+1 model
can never forecast tomorrow.

Decision:

- Train every target for **horizons 1, 2 and 3 days** ahead of the newest
  observation (`target_frost`, `target_frost_h2`, `target_frost_h3`, ... - nine
  models). The service computes today and tomorrow in **Europe/Berlin** and
  uses the horizon `h` with `latest observation + h == tomorrow`: normally 2,
  3 if DWD is late. Older data uses the furthest horizon and is flagged stale.
- Each horizon has its own walk-forward evaluation; the dashboard quotes the
  accuracy of the horizon it shows, so longer horizons are not presented with
  1-day accuracy.
- The climatology baseline now looks up the season of the *target* day
  (t + h); persistence predicts the target day with today's value for every
  horizon.
- The daily ingest moved from 09:30 to **15:30 Europe/Berlin**, after DWD has
  published yesterday, so the usual horizon is 2 rather than 3.
- Bundles without h2/h3 models (trained before 1.1.0) still serve, using the
  1-day model, with the forecast flagged stale - until the next retrain.
- Released as **1.1.0** (minor: new behaviour, compatible API with added fields
  `issued_on` and `horizon_days`).

## 2026-10-17: Releases from version tags in GitLab CI 

- **Trigger:** a protected git tag `vX.Y.Z`. The tag pipeline runs lint and
  tests, then `release-image` (build, smoke test, tag == `__version__` check,
  push the immutable image) and `deploy` (Cloud Run update by digest, live
  check of `/health` and the version in the page). Branch pipelines keep the
  build-and-smoke `container` job and never deploy.
- **Keyless authentication:** jobs request a GitLab OIDC token (`id_tokens`,
  audience = the Workload Identity provider). Google STS exchanges it only when
  `project_path` is this project and `ref_protected` is true, and the job then
  impersonates `climatelens-deployer` (Artifact Registry writer on the repo,
  `run.developer` on the one service, actAs the runtime account). Nothing
  secret is stored in GitLab CI variables. `release-image` uses plain
  curl + jq for the token exchange (the Docker image has no gcloud); `deploy`
  uses gcloud with an external-account credential configuration file.
- **Protected `v*` tags** (Maintainers only) make "who can release" a GitLab
  permission and are required by the WIF condition.
- **One image, tested then shipped:** the deployed digest is the one that
  passed the smoke test in the same pipeline; `resource_group: production`
  serialises deployments; the GitLab `production` environment records history.
- **Terraform stays a local, reviewed step.** Running `apply` in CI would need
  a near-owner service account reachable from CI; for a single-maintainer
  project the risk outweighs the convenience. Infrastructure changes are
  planned and applied from the maintainer's laptop with ADC, and `infra/`
  changes still trigger CI lint/test. A read-only plan job could be added
  later with a viewer-scoped account.
- **Model quality gate:** a retrain publishes (moves `LATEST`) only if no
  target's primary walk-forward score (Brier / MAE) is more than 5 % worse than
  the served bundle's; otherwise the bundle is stored but not served and a
  warning is logged. `climatelens train --force` overrides it deliberately.

## 2026-10-17: Custom domain through Firebase Hosting 

Cloud Run domain mapping is a preview feature, "not production-ready" per
Google, and not offered in europe-west3. Options compared: a global external
Application Load Balancer (~EUR 17-20/month for the forwarding rule alone,
above the EUR 10 budget), a Cloudflare Worker proxy (free, but Cloudflare would
terminate TLS and carry the traffic outside Google and Terraform), moving the
service to europe-west4 (uses the preview feature, rebuilds all regional
resources), and **Firebase Hosting** (free tier, Google-managed certificate,
Terraform via the google-beta provider). Firebase Hosting was chosen.

- Hosting site `climatelens-iu` with a single rewrite `** -> Cloud Run
  climatelens (europe-west3)`; dynamic responses are not cached on the CDN
  (Hosting sets `Cache-Control: private`); cookies other than `__session` are
  stripped (the app uses none).
- Cloudflare remains the authoritative DNS with a **DNS-only** CNAME
  `climatelens -> climatelens-iu.web.app` plus the ACME TXT record; proxying
  would hide Firebase and block certificate issuance. The apex and the
  wildcard record are untouched; the specific record overrides the wildcard.
- The `run.app` URLs stay valid: Cloud Scheduler and the CI live check call
  the service directly, so the domain layer is not on the jobs' path.

## 2026-10-17: Fast cold starts without an always-on instance

Measured: the first dashboard request after the service had scaled to zero
took 6.5-9.5 s (warm requests 0.2-0.3 s). About 5 s of that was the first
request loading the nine models and the feature table and importing LightGBM
and scikit-learn; the rest was container start and Python imports.

Options compared: a minimum of one always-on instance (estimated EUR 18-25 per
month for 1 vCPU / 2 GiB idle - about twice the whole budget), a keep-warm ping,
precomputing the forecasts, short CDN caching, and splitting training into a
separate small service.

Decision (release 1.3.0), all at no cost:

- **Forecast snapshot:** after each ingest and retrain the finished forecasts
  (all horizons for every city) are written to a ~53 KB JSON file. The web
  service reads it instead of loading models; it is used only when it matches
  the current model version and feature table, and rebuilt otherwise. "Which
  horizon is tomorrow" is still decided per request, so a snapshot made in the
  afternoon stays correct after midnight.
- **Keep-warm ping:** a third Cloud Scheduler job calls `/health` every
  10 minutes from 07:00 to 23:00 (within Scheduler's free tier).
- **CDN caching:** pages and API responses carry
  `Cache-Control: public, max-age=60, s-maxage=300`, so Firebase Hosting serves
  repeat visits from its edge; health checks, errors and job calls are never
  cached.
- **Background preload** of the forecast service when an instance starts.

Result: the first request on a brand-new revision took 0.93 s instead of
6.5-9.5 s; forecast-service load fell from ~3 s to ~0 s.
