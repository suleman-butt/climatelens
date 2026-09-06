"""DWD data acquisition, validation and raw Parquet storage.

All cloud and network I/O for the project lives here (and in ``jobs.py``).
``features.py`` / ``models.py`` / ``evaluate.py`` stay pure DataFrame code.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from climatelens.config import (
    CITIES,
    CLIMATE_STATION_TO_SLUG,
    SOLAR_STATION_CITY_PAIRS,
    City,
    climate_station_ids,
    get_logger,
    solar_station_ids,
)

log = get_logger(__name__)

# --- DWD parameter expectations -------------------------------------------------

#: Datasets requested from DWD at daily resolution.
DWD_DATASETS: tuple[str, ...] = ("climate_summary", "solar")

#: Parameters taken from the SOLAR dataset (its ``sunshine_duration`` is dropped -
#: CLIMATE_SUMMARY already carries one from the primary station).
SOLAR_KEEP_PARAMETERS: tuple[str, ...] = (
    "radiation_global",
    "radiation_sky_short_wave_diffuse",
    "radiation_sky_long_wave",
)

#: Parameters every station must report for a date row to be usable.
REQUIRED_PARAMETERS: tuple[str, ...] = (
    "temperature_air_mean_2m",
    "temperature_air_min_2m",
    "temperature_air_max_2m",
    "wind_speed",
    "humidity",
    "pressure_air_site",
)

#: Radiation parameter backing the solar target (spec 4.2).
RADIATION_PARAMETERS: tuple[str, ...] = ("radiation_global",)

#: Plausible physical ranges. Values outside are nulled and counted, not dropped.
VALUE_BOUNDS: dict[str, tuple[float, float]] = {
    "temperature_air_mean_2m": (-60.0, 60.0),
    "temperature_air_min_2m": (-70.0, 55.0),
    "temperature_air_max_2m": (-50.0, 65.0),
    "temperature_air_min_0_05m": (-75.0, 60.0),
    "wind_speed": (0.0, 120.0),
    "wind_gust_max": (0.0, 150.0),
    "humidity": (0.0, 100.0),
    "pressure_air_site": (850.0, 1100.0),
    "pressure_vapor": (0.0, 90.0),
    "precipitation_height": (0.0, 500.0),
    "sunshine_duration": (0.0, 24.0),
    "snow_depth": (0.0, 500.0),
    "cloud_cover_total": (0.0, 8.0),  # DWD reports cloud cover in eighths (octas)
    "radiation_global": (0.0, 5000.0),  # joule/cm^2 per day (ts_convert_units=False)
    "radiation_sky_short_wave_diffuse": (0.0, 5000.0),
    "radiation_sky_long_wave": (0.0, 5000.0),
}

LONG_COLUMNS = ["station_id", "city", "dataset", "parameter", "date", "value", "quality"]


# --- Validation report --------------------------------------------------------


@dataclass
class ValidationReport:
    rows_in: int = 0
    rows_out: int = 0
    duplicate_rows_dropped: int = 0
    impossible_values_nulled: dict[str, int] = field(default_factory=dict)
    missing_parameters: dict[str, list[str]] = field(default_factory=dict)
    date_gaps: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "duplicate_rows_dropped": self.duplicate_rows_dropped,
            "impossible_values_nulled": self.impossible_values_nulled,
            "missing_parameters": self.missing_parameters,
            "date_gaps": {k: len(v) for k, v in self.date_gaps.items()},
            "warnings": self.warnings,
        }


# --- Fetching ----------------------------------------------------------------


_RAW_LONG_COLUMNS = ["station_id", "dataset", "parameter", "date", "value", "quality"]


def _dwd_settings():
    from wetterdienst import Settings

    # ts_convert_units=False keeps DWD's documented native units (deg C, m/s,
    # hPa, %, octas, J/cm^2, hours) so VALUE_BOUNDS stay predictable.
    # use_certifi=True avoids Windows system-trust-store gaps in aiohttp.
    return Settings(
        ts_shape="long",
        ts_humanize=True,
        ts_convert_units=False,
        use_certifi=True,
    )


def _fetch_raw(
    station_ids: list[str],
    parameters: list[tuple[str, str]],
    start: datetime,
    end: datetime,
    *,
    max_retries: int,
    base_delay: float,
) -> pd.DataFrame:
    """One retrying wetterdienst pull -> tidy pandas long frame (no city column)."""
    from wetterdienst.provider.dwd.observation import DwdObservationRequest

    attempt = 0
    while True:
        attempt += 1
        try:
            request = DwdObservationRequest(
                parameters=parameters,
                start_date=start,
                end_date=end,
                settings=_dwd_settings(),
            ).filter_by_station_id(station_id=station_ids)
            values = request.values.all().df
            raw = values.to_pandas() if hasattr(values, "to_pandas") else pd.DataFrame(values)
            break
        except Exception as exc:  # noqa: BLE001 - network layer, log and retry
            if attempt > max_retries:
                log.error(
                    "dwd fetch failed permanently",
                    extra={"attempts": attempt, "parameters": parameters, "error": str(exc)},
                )
                raise
            delay = base_delay * (2 ** (attempt - 1))
            log.warning(
                "dwd fetch failed, retrying",
                extra={"attempt": attempt, "sleep_s": delay, "error": str(exc)},
            )
            time.sleep(delay)

    if raw.empty:
        return pd.DataFrame(columns=_RAW_LONG_COLUMNS)
    raw["station_id"] = raw["station_id"].astype(str).str.zfill(5)
    raw["date"] = pd.to_datetime(raw["date"], utc=True).dt.tz_convert(None).dt.normalize()
    for col in ("dataset", "parameter", "quality", "value"):
        if col not in raw.columns:
            raw[col] = pd.NA
    for col in ("dataset", "parameter"):
        if isinstance(raw[col].dtype, pd.CategoricalDtype):
            raw[col] = raw[col].astype("string")
    return raw[_RAW_LONG_COLUMNS]


def fetch_observations(
    start: datetime,
    end: datetime,
    *,
    climate_ids: list[str] | None = None,
    solar_ids: list[str] | None = None,
    max_retries: int = 4,
    base_delay: float = 2.0,
) -> pd.DataFrame:
    """Fetch CLIMATE_SUMMARY (primary stations) + SOLAR (solar stations).

    Returns one tidy long frame with a resolved ``city`` column
    (:data:`LONG_COLUMNS`). Solar rows are fanned out to every city that shares a
    solar station. Retries each pull with exponential backoff.
    """
    climate_ids = list(climate_ids) if climate_ids is not None else climate_station_ids()
    solar_ids = list(solar_ids) if solar_ids is not None else solar_station_ids()
    kw = {"max_retries": max_retries, "base_delay": base_delay}

    climate = _fetch_raw(climate_ids, [("daily", "climate_summary")], start, end, **kw)
    climate["city"] = climate["station_id"].map(CLIMATE_STATION_TO_SLUG)

    solar = _fetch_raw(solar_ids, [("daily", "solar")], start, end, **kw)
    solar = solar[solar["parameter"].isin(SOLAR_KEEP_PARAMETERS)]
    pairs = pd.DataFrame(SOLAR_STATION_CITY_PAIRS, columns=["station_id", "city"])
    solar = solar.merge(pairs, on="station_id", how="inner")

    long_df = pd.concat([climate, solar], ignore_index=True).dropna(subset=["city"])
    if long_df.empty:
        log.warning(
            "dwd fetch returned no usable rows",
            extra={"climate_stations": len(climate_ids), "solar_stations": len(solar_ids)},
        )
        return pd.DataFrame(columns=LONG_COLUMNS)
    log.info(
        "dwd fetch ok",
        extra={
            "climate_rows": int((long_df["dataset"] == "climate_summary").sum()),
            "solar_rows": int((long_df["dataset"] == "solar").sum()),
        },
    )
    return long_df[LONG_COLUMNS].reset_index(drop=True)


# --- Reshaping & validation -------------------------------------------------


def to_wide(long_df: pd.DataFrame) -> pd.DataFrame:
    """Pivot a long observation frame to one row per (city, date)."""
    if long_df.empty:
        return pd.DataFrame(columns=["city", "station_id", "date"])
    frame = long_df.dropna(subset=["city", "parameter"]).copy()
    for col in ("city", "parameter", "dataset"):
        if isinstance(frame[col].dtype, pd.CategoricalDtype):
            frame[col] = frame[col].astype("string")
    frame = frame.sort_values(["city", "date", "dataset"])
    frame = frame.drop_duplicates(subset=["city", "date", "parameter"], keep="last")
    wide = frame.pivot_table(
        index=["city", "date"], columns="parameter", values="value", aggfunc="last", observed=True
    ).reset_index()
    wide.columns.name = None
    station_map = long_df.dropna(subset=["city"]).groupby("city")["station_id"].first().to_dict()
    wide.insert(1, "station_id", wide["city"].map(station_map))
    return wide.sort_values(["city", "date"]).reset_index(drop=True)


def validate(wide: pd.DataFrame) -> tuple[pd.DataFrame, ValidationReport]:
    """Clean a wide frame in place-ish and return it with a report.

    - drops duplicate (city, date) rows (keeps last)
    - nulls values outside :data:`VALUE_BOUNDS` and counts them
    - flags missing required/radiation parameters per city
    - flags calendar gaps per city
    """
    report = ValidationReport(rows_in=len(wide))
    if wide.empty:
        report.warnings.append("empty frame")
        return wide, report

    df = wide.copy()
    before = len(df)
    df = df.drop_duplicates(subset=["city", "date"], keep="last")
    report.duplicate_rows_dropped = before - len(df)

    for param, (lo, hi) in VALUE_BOUNDS.items():
        if param not in df.columns:
            continue
        mask = df[param].notna() & ((df[param] < lo) | (df[param] > hi))
        n = int(mask.sum())
        if n:
            df.loc[mask, param] = pd.NA
            report.impossible_values_nulled[param] = n

    expected = set(REQUIRED_PARAMETERS) | set(RADIATION_PARAMETERS)
    for city, group in df.groupby("city"):
        missing = sorted(p for p in expected if p not in df.columns or group[p].notna().sum() == 0)
        if missing:
            report.missing_parameters[str(city)] = missing

        days = pd.to_datetime(group["date"]).sort_values()
        if len(days) >= 2:
            full = pd.date_range(days.iloc[0], days.iloc[-1], freq="D")
            gaps = sorted(set(full) - set(days))
            if gaps:
                report.date_gaps[str(city)] = [d.strftime("%Y-%m-%d") for d in gaps]

    report.rows_out = len(df)
    log.info("validation complete", extra=report.as_dict())
    return df.reset_index(drop=True), report


# --- Raw Parquet storage ----------------------------------------------------


def _is_gcs(uri: str) -> bool:
    return uri.startswith("gs://")


def write_raw_partitions(wide: pd.DataFrame, base_uri: str) -> list[str]:
    """Write one ``date=YYYY-MM-DD/stations.parquet`` file per date.

    Re-writing an existing date overwrites it cleanly (no duplicate rows).
    """
    if _is_gcs(base_uri):
        raise NotImplementedError(
            "GCS raw storage is wired up in Phase 6; set CLIMATELENS_DATA_DIR for now"
        )
    if wide.empty:
        return []
    written: list[str] = []
    root = Path(base_uri)
    for day, group in wide.groupby(wide["date"].dt.strftime("%Y-%m-%d")):
        part_dir = root / f"date={day}"
        part_dir.mkdir(parents=True, exist_ok=True)
        target = part_dir / "stations.parquet"
        group.reset_index(drop=True).to_parquet(target, index=False)
        written.append(str(target))
    log.info("wrote raw partitions", extra={"partitions": len(written), "base": base_uri})
    return written


def read_raw(base_uri: str, start: date | None = None, end: date | None = None) -> pd.DataFrame:
    """Read raw partitions back into a single wide frame, optional date filter."""
    if _is_gcs(base_uri):
        raise NotImplementedError(
            "GCS raw storage is wired up in Phase 6; set CLIMATELENS_DATA_DIR for now"
        )
    root = Path(base_uri)
    if not root.exists():
        return pd.DataFrame()
    frames = []
    for part in sorted(root.glob("date=*/stations.parquet")):
        day = datetime.strptime(part.parent.name.removeprefix("date="), "%Y-%m-%d").date()
        if start and day < start:
            continue
        if end and day > end:
            continue
        frames.append(pd.read_parquet(part))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["city", "date"]).reset_index(drop=True)


# --- Ingestion orchestration ----------------------------------------------


@dataclass
class IngestResult:
    start: str
    end: str
    climate_stations: int
    solar_stations: int
    rows_fetched: int
    rows_written: int
    partitions_written: int
    report: dict[str, object]


def ingest_range(
    start: datetime,
    end: datetime,
    base_uri: str,
    cities: tuple[City, ...] = CITIES,
) -> IngestResult:
    """Fetch -> validate -> write for a date range. Used by ``jobs.py``."""
    climate_ids = sorted({c.station_id for c in cities})
    solar_ids = sorted({c.solar_station_id for c in cities})
    log.info(
        "ingest start",
        extra={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "climate_stations": len(climate_ids),
            "solar_stations": len(solar_ids),
        },
    )
    long_df = fetch_observations(start, end, climate_ids=climate_ids, solar_ids=solar_ids)
    wide = to_wide(long_df)
    clean, report = validate(wide)
    written = write_raw_partitions(clean, base_uri)
    result = IngestResult(
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        climate_stations=len(climate_ids),
        solar_stations=len(solar_ids),
        rows_fetched=len(long_df),
        rows_written=len(clean),
        partitions_written=len(written),
        report=report.as_dict(),
    )
    log.info("ingest done", extra=result.__dict__)
    return result


# --- Station resolution (online helper for ``climatelens stations``) --------


def _haversine_km_vec(lat1: float, lon1: float, lat2, lon2):
    """Great-circle distance in km from one point to arrays of points."""
    r = 6371.0088
    p1 = np.radians(lat1)
    p2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


#: Station must still be reporting on/after this date to be eligible.
STATION_ACTIVE_SINCE = "2026-06-01"
#: Station must have started on/before this date (covers a 3-year backfill).
STATION_HISTORY_BEFORE = "2023-01-01"


def _station_catalog(dataset: str) -> pd.DataFrame:
    """Active, long-history DWD stations for a daily dataset (online)."""
    from wetterdienst.provider.dwd.observation import DwdObservationRequest

    df = DwdObservationRequest(parameters=[("daily", dataset)], settings=_dwd_settings()).all().df
    cat = df.to_pandas() if hasattr(df, "to_pandas") else pd.DataFrame(df)
    cat["station_id"] = cat["station_id"].astype(str).str.zfill(5)
    cat["start_date"] = pd.to_datetime(cat["start_date"], utc=True)
    cat["end_date"] = pd.to_datetime(cat["end_date"], utc=True)
    active = cat["end_date"] >= pd.Timestamp(STATION_ACTIVE_SINCE, tz="UTC")
    deep = cat["start_date"] <= pd.Timestamp(STATION_HISTORY_BEFORE, tz="UTC")
    return cat[active & deep].reset_index(drop=True)


def _ranked_by_distance(catalog: pd.DataFrame, lat: float, lon: float) -> pd.DataFrame:
    d = _haversine_km_vec(
        lat,
        lon,
        catalog["latitude"].astype(float).to_numpy(),
        catalog["longitude"].astype(float).to_numpy(),
    )
    return catalog.assign(distance_km=d).sort_values("distance_km").reset_index(drop=True)


def _reports_core_instruments(station_id: str) -> bool:
    """True if the station actually measured wind AND pressure in the last ~45 days.

    Many DWD ``climate_summary`` stations only record temperature and
    precipitation; distance alone is not enough to pick a primary station.
    """
    end = datetime.now(UTC)
    start = end - timedelta(days=45)
    probe = _fetch_raw(
        [station_id],
        [
            ("daily", "climate_summary", "wind_speed"),
            ("daily", "climate_summary", "pressure_air_site"),
        ],
        start,
        end,
        max_retries=1,
        base_delay=1.0,
    )
    if probe.empty:
        return False
    seen = probe.dropna(subset=["value"]).groupby("parameter").size()
    return bool(seen.get("wind_speed", 0)) and bool(seen.get("pressure_air_site", 0))


def resolve_stations(cities: tuple[City, ...] = CITIES) -> pd.DataFrame:
    """Check the frozen mapping against live DWD (online, needs HTTPS to DWD).

    For each city, resolve the nearest active long-history SOLAR station and the
    nearest CLIMATE_SUMMARY station that also reports wind + pressure, then
    compare both to what ``config.CITIES`` has pinned.
    """
    climate_cat = _station_catalog("climate_summary")
    solar_cat = _station_catalog("solar")

    rows = []
    for city in cities:
        ranked = _ranked_by_distance(climate_cat, city.centre_latitude, city.centre_longitude)
        k = next(
            (r for r in ranked.head(15).itertuples() if _reports_core_instruments(r.station_id)),
            ranked.iloc[0],
        )
        s = _ranked_by_distance(solar_cat, city.centre_latitude, city.centre_longitude).iloc[0]
        rows.append(
            {
                "city": city.name,
                "cfg_station": city.station_id,
                "cfg_km": city.station_distance_km,
                "live_station": k.station_id,
                "live_km": round(float(k.distance_km), 2),
                "station_ok": k.station_id == city.station_id,
                "cfg_solar": city.solar_station_id,
                "cfg_solar_km": city.solar_distance_km,
                "live_solar": s["station_id"],
                "live_solar_km": round(float(s["distance_km"]), 2),
                "solar_ok": s["station_id"] == city.solar_station_id,
            }
        )
    return pd.DataFrame(rows)


# --- date helpers ---------------------------------------------------------


def default_incremental_range(days_back: int = 2) -> tuple[datetime, datetime]:
    """Yesterday-ish window for the daily run (DWD lags ~1 day)."""
    today = datetime.now(UTC).date()
    end = today - timedelta(days=1)
    start = end - timedelta(days=days_back)
    return (
        datetime(start.year, start.month, start.day, tzinfo=UTC),
        datetime(end.year, end.month, end.day, tzinfo=UTC),
    )


def backfill_range(years: int) -> tuple[datetime, datetime]:
    today = datetime.now(UTC).date()
    end = today - timedelta(days=1)
    start = end - timedelta(days=round(365.25 * years))
    return (
        datetime(start.year, start.month, start.day, tzinfo=UTC),
        datetime(end.year, end.month, end.day, tzinfo=UTC),
    )
