# Project Log

A chronological engineering log of how ClimateLens was built: what was done in
each phase, the measurements taken at the time, the problems encountered and
how they were solved. Figures are as measured on the day; later entries
supersede earlier numbers. The reasoning behind design choices is in the
[decision log](decisions.md).

| Phase | Content |
|---|---|
| 0-2 | Tooling, API skeleton, container, DWD ingestion, 3-year backfill |
| 3-5 | Features, walk-forward evaluation and models, API and dashboard |
| 6 | Hash-locked dependencies, hardened container, CI container job |
| 7 | Google Cloud infrastructure with Terraform; cloud pipeline verified |
| 8 | Keyless release pipeline from GitLab CI, model quality gate |
| 9 | Custom domain through Firebase Hosting |
| 10 | Results, diagrams, cost review, teardown plan |

---

## 2026-09-06

- Created the initial repository documentation and local-first implementation plan.
- Confirmed Cloudflare controls DNS for `sulemanb.com`.
- Started Phase 0 project tooling with pinned Python dependencies, Make targets, and package layout.
- No GCP resources have been created yet.

### Phase 2 - Ingestion

- Added `config.py`: `Settings` (env-prefixed `CLIMATELENS_`), the frozen 12-city
  `CITIES` universe, and JSON logging helpers (`configure_logging`, `get_logger`).
- Added `data.py`: `wetterdienst` daily fetch (CLIMATE_SUMMARY + SOLAR) with
  exponential-backoff retry; `to_wide`; `validate` -> `ValidationReport`
  (duplicate rows dropped, impossible values nulled with counts, missing
  parameters per city, calendar gaps per city); local partitioned Parquet at
  `data/raw/date=YYYY-MM-DD/stations.parquet` with clean overwrite;
  `resolve_stations` online verification helper.
- Added `jobs.py` CLI: `ingest` (incremental / `--backfill-years N` /
  `--start --end`) and `stations`.
- Added `tests/test_data.py`: 9 offline tests. `make lint` + `make test` green
  (11 tests).

**BLOCKER (resolved) - `wetterdienst` 0.97.0 cannot reach DWD.** The pinned
version builds the metadata URL with a double slash
(`.../climate//daily/kl/...`) and uses an HTML directory-listing parser that
DWD's current server breaks, so every fetch failed with `MetaFileNotFoundError`
("No meta file was found") or `FileNotFoundError`. Clearing the wetterdienst
cache did not help. Fix: upgraded `wetterdienst` 0.97.0 -> 0.136.0 (and
`pyarrow` 18.1.0 -> 25.0.1, its new floor). The 0.136 API differs -
`parameters=[("daily","climate_summary"), ("daily","solar")]` instead of
separate `parameter=`/`resolution=`/`period=`; `Settings` uses
`ts_convert_units` not `ts_si_units`. Rewrote the fetch layer accordingly. Live
fetch then worked first try. Pinning a version that cannot fetch data is not
reproducibility, so this deviation from the originally planned pin is deliberate.

**Station selection (2026-09-06, via `climatelens stations`).** Two stations per
city (see docs/decisions.md):

- Only **56 DWD stations nationwide report daily global radiation** (vs 1284 for
  CLIMATE_SUMMARY). The planned "temperature + wind + radiation within 15 km" is
  unsatisfiable for most large cities, so each city now has a primary
  CLIMATE_SUMMARY station (near) plus a separate SOLAR station (often far).
- Primary station must be active, have >= 3 years history, **and actually report
  wind + pressure** - many CLIMATE_SUMMARY stations record only temperature and
  precipitation. First naive resolution picked such stations for Frankfurt,
  Dresden and Dortmund (ingest flagged `missing_parameters`
  `pressure_air_site`, `wind_speed`); fixed by probing recent wind/pressure
  during resolution.
- All primary stations resolve <= 11 km except **Cologne 16.0 km** (Koeln-Bonn
  airport - accepted, noted).
- **Dortmund dropped, Bremen added.** No station reporting wind + pressure exists
  within 30 km of Dortmund (nearest is Werl, 30 km). Bremen (11th most populous)
  has station 00691 at 3.8 km covering the full climate set *and* solar.
- Solar station distance: Hamburg / Stuttgart / Bremen / Dresden / Nuremberg use
  one station for both. Far solar stations: Leipzig 14 km, Frankfurt 15 km,
  Essen 17 km, Berlin (Potsdam) 28 km, Munich (Weihenstephan) 31 km, Hannover
  (Braunschweig) 49 km, Cologne (Bochum) 60 km.

**Units confirmed** (via a live 3-day, 12-city ingest, `ts_convert_units=False`
= DWD native units): temperature deg C, wind m/s, pressure hPa, humidity %,
sunshine hours, `cloud_cover_total` in **octas 0-8** (VALUE_BOUNDS updated from
0-100), `radiation_global` in **J/cm2 per day** (typical ~100-300 winter,
~2000-3000 summer; the solar target is kWh/m2 -> conversion needed in
features, Phase 3). Validation on the 3-day sample: 36/36 rows kept, 0 impossible
values, 0 gaps, 0 missing parameters.

**Backfill (`climatelens ingest --backfill-years 3`, 2023-09-05 -> 2026-09-05).**
Clean: 212,112 long rows fetched -> **13,164 rows written** (12 cities x 1097
days, i.e. complete), 1097 date partitions. 0 duplicate rows, 0 impossible
values, 0 per-city missing parameters, 0 calendar gaps. Meets the Phase 2
acceptance criterion. (Per-cell nulls on individual days may still exist; those
are handled in feature engineering, Phase 3.)

Phase 2 complete.

### Scope decision - pollen dropped (2026-09-06)

Diagnostic on the backfill drove a scope cut. Targets are now **frost, solar,
wind** (Agriculture + Energy angles); the Health/pollen target is removed - no
pollen data in the DWD observation API and no appetite for a second data source
or a synthetic proxy. Feature inputs are restricted to near-complete fields
(`sunshine_duration` excluded: 100% null for Berlin/Leipzig; `radiation_sky_*`,
`snow_depth` excluded). Solar target is ~85-92% complete (Cologne/Essen worst at
~85%, and the most recent ~6 weeks are absent due to DWD's solar processing lag);
those rows are excluded from the solar model's walk-forward split rather than
imputed. See docs/decisions.md. README and dashboard drop from four cards to
three.

## 2026-10-14

### GitLab migration

- Moved the canonical repository to GitLab
  (`git@gitlab.com:sulemanb/devops-cloud/climatelens.git`) as `origin`; the old
  GitHub remote was removed locally. A GitLab push mirror keeps
  `github.com/suleman-butt/climatelens` in sync automatically.
- Added `.gitlab-ci.yml` with `lint` (ruff check + format check) and `test`
  (pytest with coverage, JUnit and Cobertura reports) stages. Runs on every
  branch push and merge request, without duplicate branch + MR pipelines.
- Updated README and docs/decisions.md: GitHub Actions -> GitLab CI/CD,
  Workload Identity Federation via GitLab OIDC.

### Phase 3 - Feature engineering

- Added `features.py` (pure DataFrame code): `build_features`, `target_frame`,
  `latest_rows`, `feature_report`, unit helpers `solar_j_cm2_to_kwh_m2`
  (J/cm2 / 360) and `wind_power_proxy` (v^3).
- 42 model features from the 10 near-complete fields: same-day values,
  diurnal range, lag-1, day-over-day deltas (temperature, pressure, wind),
  3/7-day trailing means, 3-day precipitation sum, day-of-year sin/cos, month,
  station lat/lon/elevation.
- Targets at t+1: `target_frost`, `target_solar_kwh_m2`, `target_wind_speed`.
  Same-day `frost` / `solar_kwh_m2` / `wind_speed` kept for the Phase 4
  persistence baseline.
- Each city is re-indexed to a full daily calendar before shifting/rolling, so a
  missing day yields NaN rather than pairing t with t+2.
- Feature table I/O (`write_features` / `read_features`) added to `data.py`;
  `Settings.features_uri` -> `data/features/features.parquet`; new CLI command
  `climatelens features`.
- Added `tests/test_features.py`: 11 offline tests (next-day alignment,
  no-look-ahead perturbation test, trailing lag/rolling, calendar-gap handling,
  unit conversion, missing-target exclusion, per-city independence, report and
  Parquet round trip). `ruff` + `pytest` green (22 tests).

**Feature build on the 3-year backfill (2023-09-05 -> 2026-09-05).** 13,164
rows (12 cities), 42 features; worst feature null rate 2.0%
(`wind_speed_delta1`). Usable rows per target:

| Target | Rows | Per-city completeness | Last target date |
|---|---|---|---|
| Frost | 13,152 | 99.9% all cities | 2026-09-04 |
| Solar | 12,100 | 85.3% (Cologne, Essen) - 95.5% (Dresden) | 2026-07-30 |
| Wind | 12,937 | 87.8% (Hannover) - 99.9% (Leipzig) | 2026-09-04 |

- Frost positive rate 15.5% overall (Essen 8.9% - Nuremberg 22.5%): imbalanced,
  so the majority-class baseline and Brier score matter in Phase 4.
- Solar target sanity: monthly mean 0.58 kWh/m2 (Dec) - 5.93 kWh/m2 (Jun).
  Wind target mean 3.2 m/s (0.6 - 13.0).
- **Hannover wind outage:** station 02014 reported no `wind_speed` from
  2026-03-22 to 2026-07-09 (110 days, plus 16 days in Jun 2025 and 7 in
  Nov/Dec 2023). Those rows are excluded from the wind target only; frost and
  solar are unaffected. Noted as a data limitation for per-city wind results.

Phase 3 complete.

### Phase 4 - Modelling and evaluation

- Added `models.py` (pure): baselines `Persistence` (tomorrow = today,
  climatology fallback when today is missing), `Climatology` (per-city
  day-of-year mean, +/- 7-day circular window, Feb 29 folded onto Feb 28),
  `MajorityClass` (frost only), and `LightGBMModel` on the 42 features (fixed,
  deterministic parameters; NaNs handled natively; frost returns a probability,
  regression outputs clipped at 0).
- Added `evaluate.py` (pure): expanding walk-forward with monthly test folds,
  first fold after >= 365 days of history; MAE/RMSE (solar, wind),
  accuracy/Brier (frost); skill scores vs persistence (MAE) and climatology
  (Brier); per-target and per-city summaries; `train_final` refits the
  walk-forward winner per target on all rows with a known target.
- Model bundles: `models/<UTC version>/{target}.joblib` + `manifest.json`
  (training range, rows, params, top features, library versions, CI commit) +
  `metrics.json` (full walk-forward results); `models/LATEST` points at the
  bundle to serve. Bundles are immutable.
- New CLI commands: `climatelens evaluate [--per-city]` (report only) and
  `climatelens train` (evaluate -> refit -> write bundle). A full train takes
  ~2 min 15 s locally (single-threaded for determinism).
- `libgomp1` (OpenMP runtime for LightGBM) added to the CI image and the API
  container stage.
- Added `tests/test_models.py`: 15 offline tests (baseline behaviour, leap-year
  and year-wrap climatology, metric formulas, monthly expanding folds, a spy
  model proving every fold trains strictly before its test month, LightGBM
  beating climatology on a synthetic signal, bundle round trip and
  immutability). `ruff` + `pytest` green (37 tests).

**Walk-forward results (bundle 20261014T154455Z).** Test period 2024-10-01 ->
2026-09-04 (solar -> 2026-07-30), 24 monthly folds (solar 22). The trained
bundle uses LightGBM for all three targets - it won every target overall and in
every city.

| Target | Metric | Persistence | Climatology | Majority | LightGBM | LightGBM skill |
|---|---|---|---|---|---|---|
| Frost (8,448 rows) | Brier | 0.0959 | 0.1212 | 0.1861 | **0.0596** | +50.8% vs climatology |
| Frost | Accuracy | 0.904 | 0.834 | 0.814 | **0.917** | |
| Solar (7,716 rows) | MAE kWh/m2 | 0.942 | 1.051 | - | **0.843** | +10.5% vs persistence |
| Solar | RMSE kWh/m2 | 1.380 | 1.379 | - | **1.125** | |
| Wind (8,263 rows) | MAE m/s | 0.923 | 1.002 | - | **0.835** | +9.4% vs persistence |
| Wind | RMSE m/s | 1.224 | 1.300 | - | **1.083** | |

Per city (LightGBM skill): frost Brier skill +41% (Nuremberg) to +57%
(Munich); solar MAE skill +6.2% (Cologne) to +13.3% (Hannover); wind MAE skill
+1.8% (Frankfurt) to +13.1% (Hamburg).

- Persistence is the strongest baseline for every target - weather is
  autocorrelated day to day - and beats climatology even for frost (Brier
  0.0959 vs 0.1212).
- Frost majority-class accuracy is 81.4% (test-period frost rate 18.6%), so
  accuracy alone flatters weak models; Brier is the primary frost metric.
- Frankfurt's wind gain is marginal (+1.8%): Offenbach-Wetterpark is a sheltered
  low-wind site (MAE ~0.58 m/s), leaving little for features to add.
- Top features: frost - same-day min/mean temperature and ground minimum;
  solar - day-of-year, max temperature, precipitation, cloud cover; wind -
  same-day wind speed and its 3/7-day means.
- Not verified this session: the container build with `libgomp1` (Docker
  Desktop was not running).

Phase 4 complete.

### Phase 5 - API and dashboard

- Added `pipeline.py`: ingest / features / evaluate / train steps shared by the
  CLI and the API job endpoints (`jobs.py` is now a thin CLI over it).
- Added `predict.py`: `ForecastService` loads the `LATEST` bundle and the latest
  feature row per city and returns next-day forecasts - frost probability (and
  `expected` at p >= 0.5), solar kWh/m2, wind speed, v^3 power proxy and power
  relative to the city's typical day. Forecasts based on observations older
  than 3 days are flagged `stale`. `metrics_summary` reports the served model
  vs its reference baseline from the bundle's `metrics.json`.
- `api.py`: public `/`, `/forecast`, `/forecast/{city}`, `/cities`, `/metrics`,
  `/healthz`; protected `POST /jobs/ingest` (incremental ingest + feature
  rebuild) and `POST /jobs/train`. Job endpoints need
  `Authorization: Bearer $CLIMATELENS_JOBS_TOKEN` (constant-time compare), are
  disabled (403) when no token is set, run one at a time (409 if busy) and
  reload the forecast service afterwards. 503 until a bundle and feature table
  exist.
- Server-rendered Jinja2 dashboard (`templates/dashboard.html`): three cards
  (frost, solar, wind) x 12 cities, each with the served model's walk-forward
  score and skill vs its baseline; stale-data banner; responsible-use
  disclaimer; light/dark; single column below 960 px. Checked in the browser on
  desktop and at 375 px (no horizontal scroll).
- New dependency `jinja2==3.1.6`; templates packaged via
  `[tool.setuptools.package-data]`. `CLIMATELENS_JOBS_TOKEN` added to
  `.env.example`.
- `tests/test_api.py`: 15 tests against a real bundle trained on synthetic data
  (forecast shape and ordering, stale flag, 404s, metrics, dashboard render,
  503 without a bundle, job auth: disabled / bad scheme / wrong token / busy /
  missing input, cache reload after a job). `ruff` + `pytest` green (52 tests).
- Data refreshed for a live forecast: `ingest --start 2026-09-06 --end
  2026-10-14` wrote 456 rows (12 cities x 38 days, latest DWD day 2026-10-13);
  features rebuilt (13,620 rows). The dashboard now shows forecasts valid
  2026-10-14 from model bundle 20261014T154455Z. The new partitions carry no
  `radiation_global` column at all (DWD solar lag) - harmless, since radiation
  is a target only.
- **Container still unverified.** Docker Desktop's engine crashed during every
  build attempt on this machine (latest: while copying site-packages into the
  `api` stage). The Dockerfile itself built correctly up to each crash; the
  GitLab test job installs `libgomp1` on the same Debian base and trains
  LightGBM, covering the runtime dependency.

Phase 5 complete (container verification carried over to Phase 6).

### Dashboard redesign (usability review)

- City picker sidebar: up to 3 cities at once (`/?city=berlin&city=munich`),
  Berlin by default. Selection lives in the URL (bookmarkable, works without
  JavaScript via a `noscript` submit button); a small script applies changes
  instantly, disables further choices at 3 and keeps at least one city. The
  server also caps at 3, drops duplicates and ignores unknown slugs. Below
  760 px the sidebar becomes a scrollable chip row with selected cities first.
- One panel per selected city with three cards - snowflake (frost), sun
  (solar), wind icons as inline SVG - each showing one large value and a
  plain-language label: frost risk low / moderate / high (< 20%, 20-50%,
  >= 50%), solar vs the city's typical value for the month (below / typical /
  above, +/-15%), wind power vs the city's typical day (low < 0.6x, high
  > 1.4x).
- Evaluation jargon removed from the page. Each card carries one accuracy line
  from the served model's walk-forward results for that city ("Correct on 92%
  of past days", "Typically within +/-0.8 kWh/m2"); full detail stays on
  `/metrics`.
- Disclaimer reworded as an "About these forecasts" note: technical
  demonstrator for education and research, statistical estimates, not for
  medical, agricultural or safety-critical decisions, refer to DWD for official
  forecasts and warnings. The same text is returned in the API `disclaimer`.
- API additions: `frost.risk`, `solar.vs_typical`; `ForecastService.city_accuracy`.
- Browser-checked on desktop (dark), 800 px and 375 px (light): no horizontal
  scroll (fixed a grid min-width overflow from the chip row). 57 tests green.
- Second pass for non-specialist readers: each card now gives one plain answer
  and one number - Frost ("Frost unlikely / possible / likely" + chance of
  < 0 C), Sunshine ("Sunnier than usual / About usual / Duller than usual" +
  expected kWh/m2 vs the city's usual for the month), Wind ("Gentle breeze"
  etc. + km/h). Repeated per-card descriptions and per-card accuracy lines
  removed; accuracy stated once in a "How reliable is this?" footer line.
  Station names dropped from the city header.
- Wind perspective changed from Energy to **Outdoors** (solar already covers
  energy): Beaufort description from the displayed whole km/h, so equal speeds
  never get different labels. `wind_power_proxy` and the API `power_*` fields
  removed; API gains `wind.speed_kmh`, `wind.beaufort`, `wind.description`,
  `solar.typical_kwh_m2`, `solar.outlook`. README perspectives and targets
  table updated. 58 tests green; checked in the browser at desktop and 375 px.
- Light / Dark / Auto theme switch in the header. Auto follows the device
  setting; an explicit choice is saved in the viewer's browser
  (`localStorage`, wrapped in try/catch so the page still works when storage is
  blocked) and applied by a small script in `<head>` before the first paint,
  so there is no flash of the wrong theme. The switch is hidden when
  JavaScript is off (the page then follows the device setting). Browser-checked:
  Light on a dark device, Dark on a light device, choice kept across reloads and
  city changes, Auto clears it; 375 px layout without horizontal scroll.
  59 tests green.
- Branding and explanation for non-specialists: logo (lens with a rising sun,
  inline SVG, also the favicon) beside the name; a fuller subtitle; and a "How
  ClimateLens works" section in four plain steps (Collect, Learn, Forecast,
  Stay up to date). It uses live figures from the feature table and bundle
  manifest (currently 13,620 daily records, 3.1 years since 5 Sep 2023, 18 DWD
  stations, last trained 14 Oct 2026), names LightGBM and claims it beat the
  simple rules only while LightGBM is the served model, and credits the
  MSc DevOps & Cloud Computing project. `ForecastService.about()` added.
  62 tests green; checked at desktop and 375 px in light and dark.
- Footer credit: "Created by Suleman Butt · MSc DevOps & Cloud Computing,
  IU International University". The programme is now named once (footer
  only); the "How ClimateLens works" intro states the purpose without
  repeating it.

### Phase 6 - Container and local end-to-end

- **Dependency lock.** `requirements.lock` (runtime, 87 packages) and
  `requirements-dev.lock` (+ dev tools, 95) generated with
  `uv pip compile --universal --generate-hashes`; every transitive dependency
  pinned and hash-checked. The Docker image and CI install with
  `pip install --require-hashes`; `make lock` regenerates. The previously
  floating numpy resolves to 2.5.4. The developer's global Python differed from
  the lock in ~39 packages (all patch-level) - the drift the lock removes.
- **Dockerfile hardened.** Builder installs the lock into `/opt/venv` in its
  own cached layer, then the package with `--no-deps`; runtime stage copies the
  venv only, adds `libgomp1`, runs as uid 10001 with writable `/app/data` and
  `/app/models`, and starts uvicorn on `$PORT` (Cloud Run). `.dockerignore`
  now also excludes `data/`, `models/`, local tool settings, `.env.*` and the dev lock.
- **CI `container` stage.** Docker-in-Docker job builds the `api` image on
  every push, checks it runs as uid 10001, imports LightGBM and the app, serves
  `/healthz` on `$PORT`, and returns 503 for `/forecast` and `/` while no model
  is published.
- **Makefile.** `install` uses the dev lock; new `lock`, `pipeline`, `serve`,
  `run`, `container-pipeline`. `deploy`/`destroy` point to Phase 7.
- **End to end from scratch on locked versions** (isolated `.venv` from
  `requirements-dev.lock`, empty data and model folders): `ingest
  --backfill-years 3` 38 s (13,164 rows, 1,097 partitions) -> `features` 18 s
  -> `train` 1 min 41 s (bundle 20261014T164535Z) -> API served 12 fresh
  forecasts (valid 2026-10-14, not stale), `/metrics` and the dashboard.
  Scores in line with Phase 4: frost Brier 0.0593 (+51% vs climatology), solar
  MAE 0.864 (+11.5% vs persistence), wind MAE 0.828 (+9.9%). Lint and all 62
  tests also pass on the locked versions.
- **Reproducibility verified.** Retraining on the same feature table produced
  bit-identical predictions for all three targets (max |diff| = 0.0 on 2,000
  rows), confirming the deterministic LightGBM settings.
- **Local Docker still failing.** Docker Desktop's WSL VM crashes and restarts
  mid-build (`wsl-bootstrap failed, restarting`; BuildKit then reports missing
  overlayfs snapshots) on every attempt, including with the new Dockerfile.
  Host: 15.7 GB RAM, WSL 3.0.3, default 8 GB WSL limit, no `.wslconfig`. This
  is a local Docker Desktop/WSL fault, not the image; the CI `container` job is
  the verification of record for the image.
- **Local container verified (Docker Desktop 29.8.2).** The first build
  completed every step, then the WSL VM crashed while unpacking the image
  (`wsl.exe` exit `0xc00000fd`, a Windows stack overflow in WSL itself); the
  retry finished from cache in 15 s. Image `climatelens:local`: 1.49 GB.
  Smoke checks as in CI: uid 10001, LightGBM 4.5.0 / numpy 2.5.4 from the lock,
  `/healthz` ok, `/forecast` and `/` 503 without a model. Pipeline inside the
  container against the mounted `data/` and `models/`: `ingest` (2026-10-11 ->
  10-13, 36 rows, no gaps), `features` (13,620 rows), `train` -> bundle
  20261014T171753Z (frost Brier 0.0567, solar MAE 0.843, wind MAE 0.830, all
  LightGBM). The container then served the dashboard, `/forecast` (valid
  2026-10-14, not stale) and `/metrics` from that bundle; `/jobs/train` 403
  without a token. Docker Desktop's WSL VM remains unstable on this machine
  under heavy writes, but the image itself is verified.
- Minor follow-up: pandas `FutureWarning` in `data.py` (concatenating empty or
  all-NA frames) during ingest; harmless now, to be cleaned up.
- **CI verified.** Pipeline #2947557528 for cc4d4a8 passed all three stages -
  `lint`, `test` (installing from `requirements-dev.lock` with
  `--require-hashes` on Linux) and the new `container` job (Docker-in-Docker
  build + smoke test) - in 4 min 21 s after 174 s in the queue. GitLab created
  pipelines with a long delay during its 14 Oct 15:00-19:00 UTC rate-limit
  preview; the older 17f4154 pipeline was auto-cancelled as redundant
  (`interruptible: true`), as intended.

Phase 6 complete.

## 2026-10-14 (evening)

### Phase 7 - GCP infrastructure (Terraform)

- **Project:** `climatelens-iu` (no organisation, personal account), billing
  account in EUR with trial credit. Tools: Terraform 1.16.5, Google Cloud SDK
  589.0.0, glab 1.123.0. Terraform authenticates with Application Default
  Credentials (`gcloud auth application-default login`, quota project
  `climatelens-iu`); no service-account keys.
- **Bootstrap** (`infra/bootstrap`, local state): 7 resources - 6 core APIs and
  the versioned state bucket `climatelens-iu-tfstate`.
- **Main** (`infra/`, state in GCS, google provider 7.46.1): plan 26 to add,
  applied in one run with the budget created first (2 s) - EUR 10/month budget
  (50/90/100% actual, 100% forecast, usage before credits), 7 APIs, data and
  models buckets (private, versioned, lifecycle rules), Artifact Registry with
  a keep-10 / 30-day cleanup policy, three service accounts (runtime,
  scheduler, deployer) with resource-level roles only, Workload Identity pool +
  provider for gitlab.com restricted to this project on protected refs, Cloud
  Run service (scale to zero, 1 vCPU / 2 GiB, 900 s timeout) with public
  invoker, and two Cloud Scheduler jobs with OIDC tokens.
- **App changes:** GCS storage layer (raw, features, model bundles via
  `google-cloud-storage`, parallel raw reads); Cloud Scheduler OIDC auth for
  `/jobs/*` (signature, audience `climatelens-jobs`, exact scheduler service
  account); service cache reloads when the model pointer or feature table
  changes; incremental ingest catches up from the last stored day; `/health`
  alias. 77 tests (in-memory fake GCS client for the gs:// paths).
- **Blocker found and fixed - Windows commit limit.** The machine had no page
  file: commit limit = RAM = 15.7 GB, 15.3 GB committed. New processes failed
  (`fork: Resource temporarily unavailable`, `0xC000012D`
  STATUS_COMMITMENT_LIMIT); this was also the real cause of the Docker
  Desktop / WSL VM crashes in Phase 6. After enabling a system-managed page
  file and restarting, the limit is 28.2 GB and Docker builds are stable.
- **Data:** 1,135 raw partitions uploaded with `gcloud storage rsync` (1 min
  44 s; an earlier `cp` attempt died with the memory problem).
- **Deploy:** image rebuilt locally (dependency layer cached), pushed to
  Artifact Registry in 42 s, deployed by digest
  (`manual-202610141849` -> revision 00002; `/health` follow-up
  `manual-202610141856` -> revision 00003).
- **Cloud pipeline verified end to end:**
  - `gcloud scheduler jobs run climatelens-daily-ingest` -> `/jobs/ingest` 200
    with the scheduler's OIDC token; feature table written to GCS (~20 s).
  - `gcloud scheduler jobs run climatelens-monthly-train` -> `/jobs/train` 200
    in 101 s on Cloud Run; bundle 20261014T185456Z published.
  - Live: `/forecast` (valid 2026-10-14, not stale), `/metrics` (frost Brier
    0.0567, +50.8% vs climatology; solar MAE 0.843, +10.5%; wind MAE 0.830,
    +9.9%), dashboard 200 in 0.17 s; `/jobs/train` without a token 401.
- **Found in testing:** `/healthz` returns Google's 404 from outside Cloud Run
  (reserved path); fixed with `/health`.

Phase 7 complete (deployments are manual until Phase 8 wires GitLab CI).

### Release 1.0.0 and pre-Phase-8 improvements

- **Versioning:** version 1.0.0 from `climatelens.__version__`; git tag
  `v1.0.0`; image `v1.0.0` (digest `sha256:aae0b1a1...`) with OCI version
  label; Artifact Registry tag immutability enabled via Terraform (1 in-place
  change) and verified by a rejected re-push. Deployed as revision
  `climatelens-00004-hbd`; footer shows "App v1.0.0".
- **Image size:** venv 962 -> 792 MB, compressed ~323 -> ~278 MB (see
  decisions). `google-cloud-bigquery`, `grpcio`, `grpcio-status` and
  `packaging` left the runtime lock (87 -> 83 packages). The 1.49 GB vs 325 MB
  difference was compressed vs (compressed + unpacked) reporting.
- **Dashboard layout:** with three cities the sidebar now stretches to the
  panels' height (both columns end at the same pixel, measured 748/748 at
  1380 px) and shows an "Updated daily..." note at its foot; cards are more
  compact (3-city height ~720 -> ~620 px). With one city the sidebar is
  naturally taller than the panel. Mobile unchanged (note hidden, no
  horizontal scroll).
- 79 tests green.

### Release 1.0.1 (manual)

- Footer redesigned into two lines (developer credit; data source, API
  documentation, app version, served model ID). Version bumped to 1.0.1,
  commit 90e48ce tagged `v1.0.1`, image `v1.0.1` (digest
  `sha256:027034fd...`) pushed and deployed as revision
  `climatelens-00005-gln`. Live footer verified in the browser:
  "App v1.0.1 · Model 20261014T185456Z". 80 tests green.
- `docs/` is no longer tracked in git (kept locally); README links removed.
- Decision recorded: retrain monthly, refresh data daily.

## 2026-10-17

### Release 1.1.0 - forecasts for tomorrow

- **Bug seen live:** on 17 Oct the dashboard forecast 16 Oct. The 09:30 daily
  ingest (runs on 15, 16, 17 Oct all HTTP 200) only found DWD data up to
  15 Oct; DWD published 16 Oct between 07:30 and 12:46 UTC. A t+1 model can
  never reach tomorrow with daily data that lags a day.
- **Fix:** targets for horizons 1-3 days (9 models); "today"/"tomorrow" in
  Europe/Berlin; the service picks the horizon landing on tomorrow (normally
  2); daily ingest moved to 15:30 Berlin (Terraform, 1 in-place change);
  climatology baseline uses the target day's season; old bundles still serve
  (flagged stale).
- **Walk-forward per horizon** (bundle 20261017T130211Z, local):
  frost Brier skill vs climatology h1 +50.8%, h2 +10.9%, h3 0.0% (climatology
  served); solar MAE skill vs persistence h1 +10.5%, h2 +13.6%, h3 +14.9%;
  wind h1 +9.8%, h2 +13.6%, h3 climatology served (+15.1% vs persistence).
  Model selection automatically served climatology where LightGBM did not win.
- **Dashboard:** header shows "Today" and "Forecast for"; new "About ClimateLens"
  section (what, for whom, how it differs from a weather service, cloud
  design) above "How it works"; step 3 explains the DWD delay; reliability
  line quotes the accuracy of the horizon shown; model claims follow the
  shown horizon; footer full width (two notes side by side + credit/links bar);
  mobile header stacks dates above the theme switch (fixed an off-screen
  "Today" found in testing).
- Removed the pandas FutureWarning on ingest (skip empty frames in concat).
- **Release:** commit 5a115c7, tag `v1.1.0`, image digest `sha256:d0df6cae...`,
  revision `climatelens-00006-fpc`. Cloud ingest 22.6 s; cloud training of 9
  models 287 s (bundle 20261017T131809Z). Live: today 2026-10-17 -> forecast
  2026-10-18 from data up to 2026-10-16, horizon 2, not stale. 86 tests green.

### Release 1.1.1

- Dashboard: "Based on DWD weather data up to <date>" shown once under the
  header dates instead of under every city; a city gets an "Older data" note
  only when its data is older than the rest (e.g. station outage).
- README: live link is now the regional URL as a clickable link; new
  "Automation" section (daily ingest 15:30 and why, monthly retrain and why,
  OIDC-secured job endpoints, catch-up and cache refresh).
- Commit 93c2279, tag `v1.1.1`, digest `sha256:900ad4a9...`, revision
  `climatelens-00007-cng`. Live: App v1.1.1, data date once, forecast for
  2026-10-18. 87 tests green.

### Phase 8 - automated releases (first release: v1.2.0)

- CI: tag pipelines (`vX.Y.Z`, protected, Maintainers only) run lint, test,
  `release-image` and `deploy`; branch pipelines keep lint/test/container. Model
  quality gate added (5 % tolerance, `--force` override). `make release`
  added. Branch pipeline for 8d7547e green before tagging.
- **v1.2.0 released by CI alone:** tag pushed at ~13:44 UTC; image `v1.2.0`
  in Artifact Registry after 303 s; live page showed "App v1.2.0" after 406 s.
  Deployed digest == pushed digest (`sha256:319b4a0a...`), revision
  `climatelens-00008-xk9`.
- **Keyless deploy proven in Cloud Audit Logs:** the 13:51 UTC
  `Services.ReplaceService` call was made by
  `climatelens-deployer@climatelens-iu.iam.gserviceaccount.com`, delegated by
  `principal://iam.googleapis.com/projects/273124305778/locations/global/workloadIdentityPools/gitlab/subject/project_path:sulemanb/devops-cloud/climatelens:ref_type:tag:ref:v1.2.0`.
  The previous deploy (13:32, v1.1.1) was by the personal account - the
  contrast shows the switch from manual to federated CI deployment.

Phase 8 complete.

### Phase 9 - custom domain

- Cloud Run domain mapping unavailable in europe-west3 (and preview); Firebase
  Hosting chosen (decision log). Terraform: +7 resources (2 APIs, Firebase
  project, Hosting site `climatelens-iu`, version with `** -> Cloud Run`
  rewrite, release, custom domain). `climatelens-iu.web.app` served the app
  immediately (`Cache-Control: private`).
- Cloudflare (user): DNS-only CNAME `climatelens -> climatelens-iu.web.app` +
  TXT `_acme-challenge.climatelens`; apex A and `*` wildcard left untouched.
  Visible on 1.1.1.1 and 8.8.8.8 at once.
- Firebase status: HOST_ACTIVE + OWNERSHIP_ACTIVE within a minute,
  CERT_VALIDATING -> CERT_PROPAGATING after ~5 min; `https://climatelens.sulemanb.com`
  answered HTTP 200 with a valid certificate shortly after.
- README rewritten for end users and reviewers (what, how, evaluation table,
  cloud architecture, full GCP resource table - 40 Terraform resources, DevOps
  practices, how to run). Commits 1019fe4, c0880f5.

Phase 9 complete.

### Release 1.3.0 / 1.3.1 - fast cold starts, logging

- Measured before: cold dashboard requests 6.5-9.5 s (once 16 s), warm
  0.2-0.3 s; ~5 s of a cold start was the first request loading the 9 models
  and feature table (LightGBM/sklearn import), ~2 s container start + imports.
- 1.3.0 (B+C+D): keep-warm Scheduler job (*/10 07-22 Berlin, 3rd job, free);
  forecast snapshot (53 KB JSON) written after features/train, used when it
  matches LATEST + feature fingerprint, rebuilt and saved otherwise; background
  preload on startup; Cache-Control `public, max-age=60, s-maxage=300` on pages
  and API (no-store for health, errors, jobs). Local service load 2.95 s -> 0.00 s.
  Firebase edge: x-cache MISS then HIT. Released by CI (tag pipeline 6 min 44 s),
  revision 00009; its first request built the snapshot (7.4 s, one-time).
- 1.3.1: API configures JSON logging at startup (INFO messages were dropped).
  Released via full route (branch + tag pipelines). Revision 00010 logged
  "forecast service loaded from snapshot"; its first dashboard request took
  0.93 s (vs 7.4 s / 6.5-9.5 s before). 99 tests.

### Phase 10 - report and wrap-up

- Results tables generated from the live bundle 20261017T131809Z
  ([evaluation-results.md](evaluation-results.md)): overall by horizon,
  per city for 1 and 2 days ahead, top features. At 2 days ahead LightGBM beats
  the reference baseline in every city for all three forecasts.
- Diagrams: cloud architecture and "how it works" drawn in Excalidraw
  (editable sources and SVGs in [`diagrams/`](../diagrams/), shown in the README).
- Cost review (3 days measured): Cloud Run ~260 s billable/day, ~130 req/day
  (<10 % of free tier); storage 23 MB; Artifact Registry ~1-1.5 GB deduplicated.
  Expected well under EUR 1/month. README "Cost" section.
- Teardown plan in README (project deletion, or bucket emptying with
  --all-versions + make destroy + bootstrap destroy; Cloudflare/GitLab cleanup;
  bootstrap local state note).
- README final pass (fold counts, teardown command fixed). Commit b3a79f0.
- Actual Google Cloud cost 1-17 Oct 2026 (Billing > Reports): EUR 0.00 -
  Cloud Run usage EUR 0.02 fully covered by the free tier; all other services
  EUR 0.00.
- Public process documentation added: this log, the
  [decision log](decisions.md), [evaluation results](evaluation-results.md)
  and a [changelog](../CHANGELOG.md).
- Notebooks ([`notebooks/`](../notebooks/)), saved with outputs: 01 data
  collection and preparation (live DWD download, validation, completeness,
  exploration), 02 feature engineering (targets, feature groups, look-ahead and
  gap checks), 03 model training and evaluation (walk-forward scheme, baselines
  vs LightGBM per target, horizon and city, frost reliability, feature
  importance, quality gate), 04 deployment and testing (test suite plus 13 live
  checks against the service). They call the package code rather than
  re-implementing it; report figures are saved to `notebooks/figures/`.
