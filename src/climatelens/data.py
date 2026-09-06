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

from climatelens.config import CITIES, CITIES_BY_STATION, City, get_logger

log = get_logger(__name__)

# --- DWD parameter expectations -------------------------------------------------

#: Datasets requested from DWD at daily resolution.
DWD_DATASETS: tuple[str, ...] = ("climate_summary", "solar")

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
    "cloud_cover_total": (0.0, 100.0),
    "radiation_global": (0.0, 5000.0),
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


def _wetterdienst_request(station_ids: list[str], start: datetime, end: datetime):
    """Build a DwdObservationRequest for the configured datasets."""
    from wetterdienst import Settings
    from wetterdienst.provider.dwd.observation import (
        DwdObservationDataset,
        DwdObservationPeriod,
        DwdObservationRequest,
        DwdObservationResolution,
    )

    settings = Settings(
        ts_shape="long",
        ts_humanize=True,
        ts_si_units=False,
    )
    request = DwdObservationRequest(
        parameter=[DwdObservationDataset.CLIMATE_SUMMARY, DwdObservationDataset.SOLAR],
        resolution=DwdObservationResolution.DAILY,
        period=[DwdObservationPeriod.HISTORICAL, DwdObservationPeriod.RECENT],
        start_date=start,
        end_date=end,
        settings=settings,
    )
    return request.filter_by_station_id(station_id=station_ids)


def fetch_observations(
    station_ids: list[str],
    start: datetime,
    end: datetime,
    *,
    max_retries: int = 4,
    base_delay: float = 2.0,
) -> pd.DataFrame:
    """Fetch daily observations for the given stations as a tidy long frame.

    Retries with exponential backoff. Returns columns ``LONG_COLUMNS``.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            request = _wetterdienst_request(station_ids, start, end)
            values = request.values.all().df
            raw = values.to_pandas() if hasattr(values, "to_pandas") else pd.DataFrame(values)
            break
        except Exception as exc:  # noqa: BLE001 - network layer, log and retry
            if attempt > max_retries:
                log.error(
                    "dwd fetch failed permanently", extra={"attempts": attempt, "error": str(exc)}
                )
                raise
            delay = base_delay * (2 ** (attempt - 1))
            log.warning(
                "dwd fetch failed, retrying",
                extra={"attempt": attempt, "sleep_s": delay, "error": str(exc)},
            )
            time.sleep(delay)

    if raw.empty:
        log.warning("dwd fetch returned no rows", extra={"stations": len(station_ids)})
        return pd.DataFrame(columns=LONG_COLUMNS)

    raw = raw.rename(columns={"station_id": "station_id", "date": "date"})
    raw["station_id"] = raw["station_id"].astype(str).str.zfill(5)
    raw["date"] = pd.to_datetime(raw["date"], utc=True).dt.tz_convert(None).dt.normalize()
    raw["city"] = raw["station_id"].map(
        lambda s: CITIES_BY_STATION[s].slug if s in CITIES_BY_STATION else None
    )
    for col in ("dataset", "parameter", "quality", "value"):
        if col not in raw.columns:
            raw[col] = pd.NA
    return raw[LONG_COLUMNS].reset_index(drop=True)


# --- Reshaping & validation -------------------------------------------------


def to_wide(long_df: pd.DataFrame) -> pd.DataFrame:
    """Pivot a long observation frame to one row per (city, date)."""
    if long_df.empty:
        return pd.DataFrame(columns=["city", "station_id", "date"])
    frame = long_df.dropna(subset=["city", "parameter"]).copy()
    frame = frame.sort_values(["city", "date", "dataset"])
    frame = frame.drop_duplicates(subset=["city", "date", "parameter"], keep="last")
    wide = frame.pivot_table(
        index=["city", "date"], columns="parameter", values="value", aggfunc="last"
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
    stations: int
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
    ids = [c.dwd_station_id for c in cities]
    log.info(
        "ingest start",
        extra={"start": start.isoformat(), "end": end.isoformat(), "stations": len(ids)},
    )
    long_df = fetch_observations(ids, start, end)
    wide = to_wide(long_df)
    clean, report = validate(wide)
    written = write_raw_partitions(clean, base_uri)
    result = IngestResult(
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        stations=len(ids),
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


def resolve_stations(cities: tuple[City, ...] = CITIES) -> pd.DataFrame:
    """For each city, find the nearest station reporting both datasets.

    Online: needs HTTPS to opendata.dwd.de. Returns a frame with the current
    assignment and the resolved nearest qualifying station for comparison.
    """
    from wetterdienst.provider.dwd.observation import (
        DwdObservationDataset,
        DwdObservationPeriod,
        DwdObservationRequest,
        DwdObservationResolution,
    )

    def _station_frame(dataset: DwdObservationDataset) -> pd.DataFrame:
        req = DwdObservationRequest(
            parameter=[dataset],
            resolution=DwdObservationResolution.DAILY,
            period=[DwdObservationPeriod.HISTORICAL, DwdObservationPeriod.RECENT],
        )
        df = req.all().df
        return df.to_pandas() if hasattr(df, "to_pandas") else pd.DataFrame(df)

    summary = _station_frame(DwdObservationDataset.CLIMATE_SUMMARY)
    solar = _station_frame(DwdObservationDataset.SOLAR)
    solar_ids = set(solar["station_id"].astype(str).str.zfill(5))
    qualifying = summary[summary["station_id"].astype(str).str.zfill(5).isin(solar_ids)].copy()
    qualifying["station_id"] = qualifying["station_id"].astype(str).str.zfill(5)

    lat = qualifying["latitude"].astype(float).to_numpy()
    lon = qualifying["longitude"].astype(float).to_numpy()

    rows = []
    for city in cities:
        distance_km = _haversine_km_vec(city.latitude, city.longitude, lat, lon)
        dists = qualifying.assign(distance_km=distance_km).sort_values("distance_km")
        best = dists.iloc[0] if not dists.empty else None
        rows.append(
            {
                "city": city.name,
                "slug": city.slug,
                "configured_station_id": city.dwd_station_id,
                "configured_station_name": city.station_name,
                "resolved_station_id": None if best is None else best["station_id"],
                "resolved_station_name": None if best is None else best.get("name"),
                "resolved_distance_km": None
                if best is None
                else round(float(best["distance_km"]), 2),
                "resolved_latitude": None if best is None else round(float(best["latitude"]), 4),
                "resolved_longitude": None if best is None else round(float(best["longitude"]), 4),
                "resolved_elevation_m": None
                if best is None
                else round(float(best["height"]), 1)
                if "height" in dists.columns
                else None,
                "matches_config": best is not None and best["station_id"] == city.dwd_station_id,
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
