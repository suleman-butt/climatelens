"""Feature engineering: raw daily observations -> one model-ready row per (city, day).

Pure DataFrame code (no I/O). Row ``t`` holds features computed from data up to
and including day ``t`` and the three targets observed on day ``t + 1``:

* ``target_frost``        - 1 if next-day ``temperature_air_min_2m`` < 0 C, else 0
* ``target_solar_kwh_m2`` - next-day global radiation, J/cm2 -> kWh/m2
* ``target_wind_speed``   - next-day mean wind speed (m/s)

Each city is re-indexed onto a full daily calendar before shifting or rolling,
so a missing day becomes NaN instead of silently pairing ``t`` with ``t + 2``.
Missing targets are kept as NaN in the table and excluded per target by
:func:`target_frame` - never imputed (see docs/decisions.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from climatelens.config import CITIES, City, get_logger

log = get_logger(__name__)

#: Raw observation fields used as model inputs (all >= 98% complete).
FEATURE_FIELDS: tuple[str, ...] = (
    "temperature_air_mean_2m",
    "temperature_air_min_2m",
    "temperature_air_max_2m",
    "temperature_air_min_0_05m",
    "wind_speed",
    "wind_gust_max",
    "humidity",
    "pressure_air_site",
    "cloud_cover_total",
    "precipitation_height",
)

#: Fields that get trailing-window means.
ROLLING_FIELDS: tuple[str, ...] = (
    "temperature_air_mean_2m",
    "temperature_air_min_2m",
    "wind_speed",
    "humidity",
    "cloud_cover_total",
)
ROLLING_WINDOWS: tuple[int, ...] = (3, 7)

#: Fields that get a day-over-day change (pressure tendency is a strong signal).
DELTA_FIELDS: tuple[str, ...] = (
    "temperature_air_mean_2m",
    "temperature_air_min_2m",
    "pressure_air_site",
    "wind_speed",
)

#: 1 kWh/m2 = 3.6e6 J/m2 = 360 J/cm2.
J_PER_CM2_PER_KWH_PER_M2 = 360.0

FROST_THRESHOLD_C = 0.0

TARGETS: tuple[str, ...] = ("target_frost", "target_solar_kwh_m2", "target_wind_speed")

#: Same-day values of each target quantity (day ``t``). Not model features: used
#: only by the persistence baseline ("tomorrow equals today") in Phase 4.
CURRENT_VALUES: dict[str, str] = {
    "target_frost": "frost",
    "target_solar_kwh_m2": "solar_kwh_m2",
    "target_wind_speed": "wind_speed",
}

ID_COLUMNS: tuple[str, ...] = ("city", "station_id", "date")


def _feature_columns() -> list[str]:
    cols = list(FEATURE_FIELDS)
    cols.append("temperature_air_range_2m")
    cols += [f"{f}_lag1" for f in FEATURE_FIELDS]
    cols += [f"{f}_delta1" for f in DELTA_FIELDS]
    cols += [f"{f}_mean{w}d" for f in ROLLING_FIELDS for w in ROLLING_WINDOWS]
    cols.append("precipitation_height_sum3d")
    cols += ["day_of_year_sin", "day_of_year_cos", "month"]
    cols += ["station_latitude", "station_longitude", "station_elevation_m"]
    return cols


#: Model input columns, in a stable order.
FEATURE_COLUMNS: tuple[str, ...] = tuple(_feature_columns())


def solar_j_cm2_to_kwh_m2(values: pd.Series) -> pd.Series:
    """Convert DWD daily global radiation (J/cm2) to kWh/m2."""
    return values / J_PER_CM2_PER_KWH_PER_M2


def _min_periods(window: int) -> int:
    # Tolerate isolated gaps without letting one observation stand for a week.
    return window // 2 + 1


def _city_features(group: pd.DataFrame, city: City) -> pd.DataFrame:
    """Build the feature + target block for one city on a full daily calendar."""
    group = group.sort_values("date").set_index("date")
    calendar = pd.date_range(group.index.min(), group.index.max(), freq="D", name="date")
    raw = group.reindex(calendar)

    out = pd.DataFrame(index=calendar)
    for f in FEATURE_FIELDS:
        out[f] = raw[f] if f in raw.columns else np.nan
    out["temperature_air_range_2m"] = out["temperature_air_max_2m"] - out["temperature_air_min_2m"]

    for f in FEATURE_FIELDS:
        out[f"{f}_lag1"] = out[f].shift(1)
    for f in DELTA_FIELDS:
        out[f"{f}_delta1"] = out[f] - out[f].shift(1)
    for f in ROLLING_FIELDS:
        for w in ROLLING_WINDOWS:
            out[f"{f}_mean{w}d"] = out[f].rolling(w, min_periods=_min_periods(w)).mean()
    out["precipitation_height_sum3d"] = (
        out["precipitation_height"].rolling(3, min_periods=_min_periods(3)).sum()
    )

    doy = calendar.dayofyear.to_numpy()
    days_in_year = np.where(calendar.is_leap_year, 366.0, 365.0)
    out["day_of_year_sin"] = np.sin(2 * np.pi * doy / days_in_year)
    out["day_of_year_cos"] = np.cos(2 * np.pi * doy / days_in_year)
    out["month"] = calendar.month

    out["station_latitude"] = city.station_latitude
    out["station_longitude"] = city.station_longitude
    out["station_elevation_m"] = city.station_elevation_m

    # Same-day target quantities (persistence baseline inputs).
    tmin = out["temperature_air_min_2m"]
    out["frost"] = (tmin < FROST_THRESHOLD_C).astype("float64").where(tmin.notna())
    radiation = raw["radiation_global"] if "radiation_global" in raw.columns else np.nan
    out["solar_kwh_m2"] = solar_j_cm2_to_kwh_m2(pd.Series(radiation, index=calendar))

    # Next-day targets: value at t + 1 on the full calendar.
    out["target_frost"] = out["frost"].shift(-1)
    out["target_solar_kwh_m2"] = out["solar_kwh_m2"].shift(-1)
    out["target_wind_speed"] = out["wind_speed"].shift(-1)

    # Calendar days with no raw row at all carry no usable features.
    out = out.loc[out.index.isin(group.index)]
    out.insert(0, "station_id", city.station_id)
    out.insert(0, "city", city.slug)
    return out.reset_index()


def build_features(raw: pd.DataFrame, cities: tuple[City, ...] = CITIES) -> pd.DataFrame:
    """Turn a wide raw frame (from :func:`climatelens.data.read_raw`) into features.

    Returns one row per (city, date) present in ``raw``, with
    :data:`ID_COLUMNS`, :data:`FEATURE_COLUMNS`, same-day target values and
    :data:`TARGETS`. The last day of each city has NaN targets (tomorrow is
    unknown) - that row is the one used for live prediction.
    """
    columns = [*ID_COLUMNS, *FEATURE_COLUMNS, *CURRENT_VALUES.values(), *TARGETS]
    columns = list(dict.fromkeys(columns))
    if raw.empty:
        return pd.DataFrame(columns=columns)

    raw = raw.copy()
    raw["date"] = pd.to_datetime(raw["date"]).astype("datetime64[ns]")
    by_slug = {c.slug: c for c in cities}
    blocks = []
    for slug, group in raw.groupby("city"):
        city = by_slug.get(str(slug))
        if city is None:
            log.warning("skipping unknown city", extra={"city": str(slug)})
            continue
        blocks.append(_city_features(group, city))
    if not blocks:
        return pd.DataFrame(columns=columns)

    features = pd.concat(blocks, ignore_index=True)[columns]
    return features.sort_values(["city", "date"]).reset_index(drop=True)


def target_frame(features: pd.DataFrame, target: str) -> pd.DataFrame:
    """Rows usable for training/evaluating one target: target present, not imputed."""
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}; expected one of {TARGETS}")
    return features.loc[features[target].notna()].reset_index(drop=True)


def latest_rows(features: pd.DataFrame) -> pd.DataFrame:
    """The most recent row per city - the input for a next-day forecast."""
    if features.empty:
        return features
    idx = features.groupby("city")["date"].idxmax()
    return features.loc[idx].sort_values("city").reset_index(drop=True)


@dataclass
class FeatureReport:
    rows: int = 0
    cities: int = 0
    start: str = ""
    end: str = ""
    feature_columns: int = 0
    feature_null_pct: dict[str, float] = field(default_factory=dict)
    target_rows: dict[str, int] = field(default_factory=dict)
    target_complete_pct: dict[str, dict[str, float]] = field(default_factory=dict)
    target_last_date: dict[str, str] = field(default_factory=dict)
    frost_positive_rate: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


def feature_report(features: pd.DataFrame) -> FeatureReport:
    """Completeness summary for the project log (per target and per city)."""
    report = FeatureReport(rows=len(features), feature_columns=len(FEATURE_COLUMNS))
    if features.empty:
        return report
    report.cities = int(features["city"].nunique())
    report.start = features["date"].min().strftime("%Y-%m-%d")
    report.end = features["date"].max().strftime("%Y-%m-%d")

    nulls = features[list(FEATURE_COLUMNS)].isna().mean() * 100
    report.feature_null_pct = {k: round(float(v), 2) for k, v in nulls.items() if v > 0}

    for target in TARGETS:
        present = features[target].notna()
        report.target_rows[target] = int(present.sum())
        by_city = present.groupby(features["city"]).mean() * 100
        report.target_complete_pct[target] = {k: round(float(v), 1) for k, v in by_city.items()}
        last = features.loc[present, "date"].max()
        report.target_last_date[target] = "" if pd.isna(last) else last.strftime("%Y-%m-%d")

    frost = features["target_frost"].dropna()
    report.frost_positive_rate = round(float(frost.mean()), 4) if len(frost) else 0.0
    return report
