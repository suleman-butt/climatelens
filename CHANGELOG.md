# Changelog

All notable changes to ClimateLens. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Each version is a protected git tag
`vX.Y.Z` and an immutable container image of the same name; since 1.2.0,
releases are built and deployed by GitLab CI.

## [Unreleased]

### Added
- Custom domain `climatelens.sulemanb.com` through Firebase Hosting in front of
  Cloud Run, with a Google-managed certificate (infrastructure, Terraform).
- Excalidraw diagrams for the cloud architecture and "how it works"
  (`diagrams/`).
- Process documentation: decision log, project log and evaluation results
  (`docs/`), and this changelog.

## [1.3.1] - 2026-10-17

### Fixed
- The web service now configures JSON logging at startup, so its INFO messages
  (snapshot loads, jobs, ingest results) reach Cloud Logging.

## [1.3.0] - 2026-10-17

### Added
- Forecast snapshot: the daily ingest and monthly retrain precompute all
  forecasts into a small JSON file that the web service reads instead of
  loading models; rebuilt automatically when stale.
- Keep-warm Cloud Scheduler job (every 10 minutes, 07:00-23:00).
- Cache headers for Firebase Hosting's CDN (5 minutes for pages and API;
  never for health checks, errors or jobs).
- Background preload of the forecast service when an instance starts.

### Changed
- First request after a cold start: 6.5-9.5 s -> 0.93 s.

## [1.2.0] - 2026-10-17

### Added
- Release pipeline on protected tags `vX.Y.Z`: lint, test, build, smoke test,
  version check, push of the immutable image, deploy to Cloud Run by digest,
  live check; GitLab `production` environment.
- Keyless authentication from GitLab CI to Google Cloud with Workload Identity
  Federation (no service-account keys).
- Model quality gate: a retrained model is published only if no forecast is
  more than 5 % worse than the served one (`climatelens train --force`
  overrides).
- `make release` and `make destroy`.

## [1.1.1] - 2026-10-17

### Changed
- The date of the newest weather data is shown once in the header; a city gets
  its own note only when its data is older than the others.

## [1.1.0] - 2026-10-17

### Added
- Models for 1, 2 and 3 days ahead of the newest observation; the service
  picks the one that lands on tomorrow in German time.
- Today's date and the forecast date in the dashboard header.
- "About ClimateLens" section; full-width footer.

### Changed
- Daily ingest moved from 09:30 to 15:30, after DWD publishes the previous day.

### Fixed
- The dashboard showed a forecast for a past date, because DWD publishes each
  day's observations on the following day.
- pandas warning during ingest.

## [1.0.1] - 2026-10-14

### Changed
- Two-line footer: developer credit; data source, API documentation, app
  version and served model version.

## [1.0.0] - 2026-10-14

First release running on Google Cloud.

### Added
- Terraform-managed infrastructure: Cloud Run, Cloud Storage, Artifact
  Registry, Cloud Scheduler, least-privilege service accounts, Workload
  Identity Federation, EUR 10 budget alert.
- Daily ingest and monthly retrain called by Cloud Scheduler with OIDC tokens.
- Dashboard with city picker (up to 3 cities), plain-language cards for frost,
  sunshine and wind, light/dark theme; JSON API and `/metrics`.
- Hash-locked dependencies; container built and smoke-tested in CI.

### Changed
- Smaller runtime image (compressed ~323 MB -> ~278 MB).
- Immutable version tags in Artifact Registry.
