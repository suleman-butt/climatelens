# Project Log

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
reproducibility, so this deviation from the spec's pin is deliberate.

**Station selection (2026-09-06, via `climatelens stations`).** Two stations per
city (see docs/decisions.md):

- Only **56 DWD stations nationwide report daily global radiation** (vs 1284 for
  CLIMATE_SUMMARY). The spec's "temperature + wind + radiation within 15 km" is
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
~2000-3000 summer; spec's solar target is kWh/m2 -> conversion needed in
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

### Dashboard redesign (user feedback)

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
