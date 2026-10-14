"""Offline tests for feature engineering on synthetic raw frames."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from climatelens.config import CITIES_BY_SLUG
from climatelens.data import read_features, write_features
from climatelens.features import (
    FEATURE_COLUMNS,
    FEATURE_FIELDS,
    TARGETS,
    build_features,
    feature_report,
    latest_rows,
    solar_j_cm2_to_kwh_m2,
    target_frame,
    wind_power_proxy,
)


def _raw(city="berlin", start="2024-01-01", days=10, drop_dates=()):
    dates = pd.date_range(start, periods=days, freq="D")
    n = len(dates)
    step = np.arange(n, dtype="float64")
    frame = pd.DataFrame(
        {
            "city": city,
            "station_id": CITIES_BY_SLUG[city].station_id,
            "date": dates,
            # Day index encoded in the values makes the shift direction checkable.
            "temperature_air_mean_2m": step,
            "temperature_air_min_2m": step - 3.0,  # below zero for days 0-2
            "temperature_air_max_2m": step + 4.0,
            "temperature_air_min_0_05m": step - 4.0,
            "wind_speed": 1.0 + step,
            "wind_gust_max": 5.0 + step,
            "humidity": 80.0,
            "pressure_air_site": 1000.0 + step,
            "cloud_cover_total": 4.0,
            "precipitation_height": 1.0,
            "radiation_global": 360.0 * (1.0 + step),  # kWh/m2 = day index + 1
        }
    )
    return frame.loc[~frame["date"].isin(pd.to_datetime(list(drop_dates)))].reset_index(drop=True)


def test_targets_are_next_day_values():
    feats = build_features(_raw())
    day0 = feats.iloc[0]
    assert day0["date"] == pd.Timestamp("2024-01-01")
    assert day0["target_wind_speed"] == 2.0  # wind on day 1
    assert day0["target_solar_kwh_m2"] == pytest.approx(2.0)  # radiation on day 1
    # tmin on day 1 is -2 -> frost; on day 3 it is 0 -> not frost (strictly < 0).
    assert feats.loc[1, "target_frost"] == 1.0
    assert feats.loc[2, "target_frost"] == 0.0


def test_features_never_use_the_future():
    raw = _raw()
    base = build_features(raw)
    # Perturb only the last day; every feature on earlier days must be unchanged.
    changed = raw.copy()
    last = changed["date"] == changed["date"].max()
    for f in FEATURE_FIELDS:
        changed.loc[last, f] = changed.loc[last, f] + 100.0
    perturbed = build_features(changed)
    cols = list(FEATURE_COLUMNS)
    pd.testing.assert_frame_equal(base.loc[:8, cols], perturbed.loc[:8, cols])


def test_lag_and_rolling_are_trailing():
    feats = build_features(_raw())
    row = feats.loc[feats["date"] == pd.Timestamp("2024-01-05")].iloc[0]  # day index 4
    assert row["temperature_air_mean_2m_lag1"] == 3.0
    assert row["pressure_air_site_delta1"] == 1.0
    assert row["temperature_air_mean_2m_mean3d"] == pytest.approx((2 + 3 + 4) / 3)
    assert row["precipitation_height_sum3d"] == 3.0
    assert row["temperature_air_range_2m"] == 7.0


def test_calendar_gap_breaks_shift_instead_of_skipping():
    feats = build_features(_raw(drop_dates=("2024-01-04",)))
    assert pd.Timestamp("2024-01-04") not in set(feats["date"])
    before_gap = feats.loc[feats["date"] == pd.Timestamp("2024-01-03")].iloc[0]
    after_gap = feats.loc[feats["date"] == pd.Timestamp("2024-01-05")].iloc[0]
    # Tomorrow is missing, so the target is unknown - not the day after.
    for target in TARGETS:
        assert pd.isna(before_gap[target])
    # Yesterday is missing, so the lag is unknown - not two days ago.
    assert pd.isna(after_gap["temperature_air_mean_2m_lag1"])


def test_solar_unit_conversion():
    assert solar_j_cm2_to_kwh_m2(pd.Series([360.0, 2700.0])).tolist() == [1.0, 7.5]


def test_wind_power_proxy_is_cubic():
    assert wind_power_proxy(np.array([1.0, 2.0, 3.0])).tolist() == [1.0, 8.0, 27.0]


def test_missing_target_is_excluded_not_imputed():
    raw = _raw()
    raw.loc[raw["date"] >= pd.Timestamp("2024-01-08"), "radiation_global"] = np.nan
    feats = build_features(raw)
    solar = target_frame(feats, "target_solar_kwh_m2")
    wind = target_frame(feats, "target_wind_speed")
    assert solar["date"].max() == pd.Timestamp("2024-01-06")  # its target is day 7
    assert len(wind) == len(feats) - 1  # only the last day lacks a wind target
    assert solar["target_solar_kwh_m2"].notna().all()


def test_target_frame_rejects_unknown_target():
    with pytest.raises(ValueError):
        target_frame(build_features(_raw()), "target_pollen")


def test_cities_are_independent_and_latest_row_per_city():
    raw = pd.concat([_raw("berlin"), _raw("munich", days=5)], ignore_index=True)
    feats = build_features(raw)
    munich = feats.loc[feats["city"] == "munich"]
    assert len(munich) == 5
    assert pd.isna(munich.iloc[0]["temperature_air_mean_2m_lag1"])  # no bleed from berlin
    assert munich["station_elevation_m"].unique().tolist() == [515.0]

    latest = latest_rows(feats)
    assert latest["city"].tolist() == ["berlin", "munich"]
    assert latest["date"].tolist() == [pd.Timestamp("2024-01-10"), pd.Timestamp("2024-01-05")]
    assert latest[list(TARGETS)].isna().all().all()


def test_empty_raw_gives_empty_table():
    feats = build_features(pd.DataFrame())
    assert feats.empty
    assert set(FEATURE_COLUMNS) <= set(feats.columns)


def test_feature_report_and_parquet_round_trip(tmp_path):
    feats = build_features(_raw())
    report = feature_report(feats)
    assert report.rows == 10
    assert report.target_rows["target_wind_speed"] == 9
    assert report.target_last_date["target_frost"] == "2024-01-09"

    uri = str(tmp_path / "features" / "features.parquet")
    write_features(feats, uri)
    back = read_features(uri)
    pd.testing.assert_frame_equal(feats, back, check_dtype=False)
