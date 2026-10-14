# ClimateLens

ClimateLens is a cloud-native weather forecasting service for selected German cities. It turns daily German Weather Service (DWD) observations into next-day forecasts across three practical perspectives:

- **Agriculture:** frost risk
- **Solar energy:** sunshine energy reaching the ground
- **Outdoors:** how windy the day will be

This project is being developed as an MSc DevOps & Cloud Computing university deliverable. The main focus is a reproducible cloud architecture, automated delivery, and responsible use of machine-learning forecasts.

> **Project status:** Live on Google Cloud Run, with Terraform-managed infrastructure and scheduled daily ingest and monthly retraining. Automated deployment from GitLab CI and the custom domain are next.

## Planned Features

- Daily ingestion of DWD weather-station data for 12 German cities
- Feature engineering for temperature, wind, humidity, pressure, cloud cover, and calendar effects
- Three next-day forecast targets: frost, solar, and wind
- Walk-forward model evaluation against transparent baselines
- REST API and server-rendered dashboard
- Versioned raw data, features, models, and evaluation metrics
- Infrastructure managed with Terraform
- Automated application delivery through GitLab CI/CD and Google Cloud Workload Identity Federation

## Forecast Targets

| Target | Perspective | Type | Description |
|---|---|---|---|
| Frost | Agriculture | Classification | Probability that next-day minimum temperature is below 0 C |
| Sunshine | Solar energy | Regression | Next-day global radiation (kWh/m2), from DWD SOLAR stations |
| Wind | Outdoors | Regression | Next-day mean wind speed (m/s), shown as km/h and a Beaufort description |

A fourth target, next-day pollen load (Health), was scoped out: the DWD
observation API carries no pollen data, and the only alternative is DWD's
separate coarse regional pollen-index archive.

## Architecture

```text
DWD weather observations
          |
          v
Validation and immutable raw storage
          |
          v
Feature engineering, Parquet feature table in Cloud Storage
          |
          v
Walk-forward training and evaluation
          |
          v
Versioned model artifacts in Cloud Storage
          |
          v
FastAPI service on Cloud Run
          |
          v
REST API and server-rendered dashboard
```

One Cloud Run service serves the dashboard and API and runs the protected ingestion and training endpoints, which Cloud Scheduler calls daily (ingest) and monthly (retrain). The workloads can be split into Cloud Run Jobs if they outgrow a request.

## Technology Stack

- **Language:** Python 3.12
- **API:** FastAPI and Uvicorn
- **Data processing:** pandas and PyArrow
- **Machine learning:** scikit-learn and LightGBM
- **Weather data:** `wetterdienst` and DWD open data
- **Storage:** Google Cloud Storage (raw partitions, feature table, model bundles)
- **Compute:** Google Cloud Run
- **Scheduling:** Google Cloud Scheduler
- **Infrastructure:** Terraform
- **CI/CD:** GitLab CI/CD with Workload Identity Federation (GitLab OIDC)
- **DNS:** Cloudflare for `sulemanb.com`

## Repository Structure

```text
climatelens/
├── src/climatelens/       Application source code
├── tests/                 Automated tests
├── infra/                 Terraform (GCP infrastructure) and its bootstrap
├── Dockerfile             Production image (non-root, hash-locked dependencies)
└── .gitlab-ci.yml         CI and deployment pipeline
```

The project keeps feature engineering, modelling, and evaluation independent of GCP. Cloud I/O is isolated in the data and job layers so the core workflow remains testable locally.

## Local Development

```bash
make install    # exact, hash-checked dependencies from requirements-dev.lock
make lint       # ruff check + format check
make test       # pytest (offline, synthetic data)
make serve      # dashboard on http://localhost:8000 with auto-reload
```

Every dependency, including transitive ones, is pinned with hashes in
`requirements.lock` (runtime image) and `requirements-dev.lock` (CI and
development). After editing `pyproject.toml`, regenerate both with `make lock`
(needs [uv](https://docs.astral.sh/uv/)).

Configuration comes from `CLIMATELENS_*` environment variables (or a local
`.env`); `.env.example` lists them. Secrets are never committed.

The pipeline, end to end:

```bash
climatelens ingest --backfill-years 3   # DWD observations -> data/raw/
climatelens features                    # -> data/features/features.parquet
climatelens evaluate                    # walk-forward models vs baselines (report only)
climatelens train                       # evaluate, refit, write models/<version>/
```

Afterwards, `make pipeline` fetches the latest days, rebuilds features and
retrains.

### Container

```bash
make build                # production image (non-root, hash-checked deps)
make container-pipeline   # ingest -> features -> train inside the container
make run                  # dashboard from the container on http://localhost:8080
```

`data/` and `models/` are mounted into the container, so the image itself
stays stateless.

### Releases

The version lives in `__version__` in `src/climatelens/__init__.py` (currently 1.0.0). A
release is a git tag `vX.Y.Z` plus an image with the same tag in Artifact
Registry, where tags are immutable:

```bash
make version         # 1.0.0
make release-image   # build + push europe-west3-docker.pkg.dev/.../climatelens:v1.0.0
```

Bump `__version__` before the next release; pushing an existing tag again is
rejected. The CI `container` job builds the same image on every push
and checks that it starts, serves `/healthz` and runs as the non-root user.

## API

| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/` | public | Dashboard: frost, solar and wind cards for all cities |
| GET | `/forecast` | public | Next-day forecasts for every city |
| GET | `/forecast/{city}` | public | Next-day forecast for one city (slug, e.g. `berlin`) |
| GET | `/cities` | public | The 12 cities and their DWD stations |
| GET | `/metrics` | public | Walk-forward scores of the served model vs baselines (`?per_city=true`) |
| GET | `/health` | public | Liveness (`/healthz` also works locally; Cloud Run reserves it externally) |
| POST | `/jobs/ingest` | token | Fetch recent DWD data and rebuild features |
| POST | `/jobs/train` | token | Evaluate, retrain and publish a new model bundle |

Job endpoints require `Authorization: Bearer $CLIMATELENS_JOBS_TOKEN` and are
disabled when no token is set. Forecasts older than three days are flagged
`stale`.

## Model Evaluation

ClimateLens will use expanding walk-forward validation. Randomly shuffled train/test splits are intentionally excluded because weather data is time-dependent and can otherwise introduce look-ahead bias.

Required baselines are:

- Persistence: tomorrow equals today
- Climatological mean: historical mean for the day of year
- Majority class: always predict no frost

Regression models will report MAE and RMSE. Frost classification will report accuracy and Brier score. Results will be recorded per target and per city for the project report.

## Cloud Deployment

ClimateLens runs on Google Cloud in `europe-west3` (Frankfurt), project
`climatelens-iu`:

```text
https://climatelens-rsd2eoklsa-ey.a.run.app
```

The planned custom address `https://climatelens.sulemanb.com` follows with the
domain mapping (Cloudflare stays the authoritative DNS; Google manages the
certificate).

| Component | Service |
|---|---|
| Dashboard, API, job endpoints | Cloud Run (scales to zero, 1 vCPU / 2 GiB) |
| Raw data and feature table | Cloud Storage `climatelens-iu-data` |
| Model bundles and `LATEST` pointer | Cloud Storage `climatelens-iu-models` |
| Container images | Artifact Registry `climatelens` (keeps the 10 newest) |
| Daily ingest 09:30, monthly retrain on the 1st, 11:00 (Europe/Berlin) | Cloud Scheduler with Google-signed OIDC tokens |
| GitLab CI to GCP without keys | Workload Identity Federation (pool `gitlab`) |
| Cost guard | Billing budget alert, EUR 10/month, counting usage before credits |

All of it is Terraform in [`infra/`](infra/):

```bash
cd infra/bootstrap && terraform init && terraform apply   # once: state bucket + core APIs
cd infra && terraform init && terraform plan && terraform apply
```

Deployment principles:

- No service-account JSON keys anywhere: people use `gcloud` logins, GitLab CI uses Workload Identity Federation, Cloud Run and Scheduler use attached service accounts
- One least-privilege service account per role (runtime, scheduler, deployer)
- Terraform owns the infrastructure; CI owns the running image
- The budget alert is created before any billable API is enabled; storage and registry have lifecycle rules
- Public access is limited to the dashboard and read-only API; `/jobs/*` accepts only Cloud Scheduler's token (signature, audience and service account checked)

The project uses a Google Cloud trial credit. The budget alert is a monitoring
control, not a hard spending limit; expected running cost is well under
EUR 1/month, mostly container image storage.

## Responsible Use

ClimateLens is a technical demonstrator for educational and research purposes. Its forecasts are city-level estimates and may be affected by data gaps, station coverage, model limitations, and weather uncertainty.

**Technical demonstrator. Not medical, agricultural, or safety advice.**

## License

This project is licensed under the MIT License. See [`LICENSE`](LICENSE).
