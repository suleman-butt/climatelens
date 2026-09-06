# Project Log

## 2026-09-06

- Created the initial repository documentation and local-first implementation plan.
- Confirmed Cloudflare controls DNS for `sulemanb.com`.
- Started Phase 0 project tooling with pinned Python dependencies, Make targets, and package layout.
- No GCP resources have been created yet.

### Phase 2 - Ingestion (started)

- Added `config.py`: `Settings` (env-prefixed `CLIMATELENS_`), the frozen 12-city
  `CITIES` universe with centre coordinates and seed DWD station IDs, and JSON
  logging helpers (`configure_logging`, `get_logger`) so Cloud Logging parses fields.
- Added `data.py`: `wetterdienst` daily fetch (CLIMATE_SUMMARY + SOLAR) with
  exponential-backoff retry; `to_wide` (long -> one row per city/date); `validate`
  producing a `ValidationReport` (duplicate rows dropped, impossible values nulled
  with counts, missing parameters per city, calendar gaps per city); local
  partitioned Parquet at `data/raw/date=YYYY-MM-DD/stations.parquet` with clean
  overwrite; `resolve_stations` online helper.
- Added `jobs.py` CLI: `ingest` (incremental / `--backfill-years N` / `--start`
  `--end`) and `stations` (verify the mapping against live DWD).
- Added `tests/test_data.py`: 9 offline tests (reshape, validation rules,
  Parquet round-trip + overwrite, date-range helpers). `make lint` + `make test`
  green (11 tests).
- BLOCKER: this dev sandbox cannot reach `opendata.dwd.de` (TLS interception -
  `CERTIFICATE_VERIFY_FAILED`). Live fetch, the real backfill, station-ID
  verification and DWD unit confirmation must be run on a machine with clean
  outbound HTTPS. Station IDs / elevations in `config.py` are seeds pending
  `climatelens stations`.
- TODO before Phase 3: run `climatelens stations`, reconcile `CITIES`, run
  `climatelens ingest --backfill-years 3`, record row counts and data-quality
  findings here (spec 9), confirm radiation/sunshine units vs `VALUE_BOUNDS`.
