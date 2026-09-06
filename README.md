# ClimateLens

ClimateLens is a cloud-native weather forecasting service for selected German cities. It turns daily German Weather Service (DWD) observations into next-day forecasts across two practical perspectives:

- **Agriculture:** frost risk
- **Energy:** solar and wind potential

This project is being developed as an MSc DevOps & Cloud Computing university deliverable. The main focus is a reproducible cloud architecture, automated delivery, and responsible use of machine-learning forecasts.

> **Project status:** Early development. The local data, feature, model, API, and dashboard workflow will be validated before cloud deployment.

## Planned Features

- Daily ingestion of DWD weather-station data for 12 German cities
- Feature engineering for temperature, wind, humidity, pressure, cloud cover, and calendar effects
- Three next-day forecast targets: frost, solar, and wind
- Walk-forward model evaluation against transparent baselines
- REST API and server-rendered dashboard
- Versioned raw data, features, models, and evaluation metrics
- Infrastructure managed with Terraform
- Automated application delivery through GitHub Actions and Google Cloud Workload Identity Federation

## Forecast Targets

| Target | Perspective | Type | Description |
|---|---|---|---|
| Frost risk | Agriculture | Classification | Probability that next-day minimum temperature is below 0 C |
| Solar potential | Energy | Regression | Next-day global radiation (kWh/m2), from DWD SOLAR stations |
| Wind potential | Energy | Regression | Next-day mean wind speed as a power-potential proxy (proportional to v^3) |

A fourth target, next-day pollen load (Health), was scoped out: the DWD
observation API carries no pollen data, and the only alternative is DWD's
separate coarse regional pollen-index archive. See [`docs/decisions.md`](docs/decisions.md).

## Architecture

```text
DWD weather observations
          |
          v
Validation and immutable raw storage
          |
          v
Feature engineering and BigQuery feature table
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

The initial implementation will use a single Cloud Run service with protected ingestion and training endpoints. The workloads can be separated into Cloud Run Jobs after the local and cloud workflows are stable.

## Technology Stack

- **Language:** Python 3.12
- **API:** FastAPI and Uvicorn
- **Data processing:** pandas and PyArrow
- **Machine learning:** scikit-learn and LightGBM
- **Weather data:** `wetterdienst` and DWD open data
- **Storage:** Google Cloud Storage and BigQuery
- **Compute:** Google Cloud Run
- **Scheduling:** Google Cloud Scheduler
- **Infrastructure:** Terraform
- **CI/CD:** GitHub Actions with Workload Identity Federation
- **DNS:** Cloudflare for `sulemanb.com`

## Repository Structure

```text
climatelens/
├── src/climatelens/       Application source code
├── tests/                 Automated tests
├── infra/                 Terraform and GCP bootstrap files
├── docs/                  Architecture decisions and project evidence
├── notebooks/             Data exploration
└── .github/workflows/     CI and deployment workflows
```

The project keeps feature engineering, modelling, and evaluation independent of GCP. Cloud I/O is isolated in the data and job layers so the core workflow remains testable locally.

## Local Development

The project will provide Makefile commands for the main development tasks:

```bash
make install
make lint
make test
make build
```

Configuration will be supplied through environment variables. Secrets must never be committed to the repository; `.env.example` will document the required variable names without containing credentials.

The intended development sequence is:

1. Validate a representative DWD data sample locally.
2. Run ingestion, feature engineering, training, and evaluation locally.
3. Start the API and verify the dashboard and forecast endpoints.
4. Build and test the container locally.
5. Deploy the validated application to Google Cloud.

## Model Evaluation

ClimateLens will use expanding walk-forward validation. Randomly shuffled train/test splits are intentionally excluded because weather data is time-dependent and can otherwise introduce look-ahead bias.

Required baselines are:

- Persistence: tomorrow equals today
- Climatological mean: historical mean for the day of year
- Majority class: always predict no frost

Regression models will report MAE and RMSE. Frost classification will report accuracy and Brier score. Results will be recorded per target and per city for the project report.

## Cloud Deployment

The target region is `europe-west3`. The planned public address is:

```text
https://climatelens.sulemanb.com
```

Cloudflare will remain the authoritative DNS provider, while Cloud Run will serve the application and Google will manage the HTTPS certificate.

Deployment principles:

- No service-account JSON keys
- GitHub Actions authentication through Workload Identity Federation
- Terraform-managed runtime infrastructure
- Automatic application deployment from `main`
- Approval required for production infrastructure changes
- Budget alert and storage lifecycle policies configured before workloads run
- Public access limited to the dashboard and read-only forecast API
- Ingestion and training endpoints protected from arbitrary public invocation

The project uses a Google Cloud learning account with an initial credit allowance. Actual costs depend on usage and Google Cloud pricing. The budget alert is a monitoring control, not a hard spending limit, so resources will be kept minimal and reviewed regularly.

## Responsible Use

ClimateLens is a technical demonstrator for educational and research purposes. Its forecasts are city-level estimates and may be affected by data gaps, station coverage, model limitations, and weather uncertainty.

**Technical demonstrator. Not medical, agricultural, or safety advice.**

## Project Documentation

- [`docs/decisions.md`](docs/decisions.md): Architecture and modelling decisions
- [`docs/project-log.md`](docs/project-log.md): Build evidence, experiments, issues, and timings
- [`infra/`](infra/): Infrastructure as code

These files will be added as implementation progresses.

## License

This project is licensed under the MIT License. See [`LICENSE`](LICENSE).
