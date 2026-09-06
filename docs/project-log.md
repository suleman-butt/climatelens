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

TODO before Phase 3: run `climatelens ingest --backfill-years 3` and record the
full row counts / gap counts / any impossible values here (spec 9).
