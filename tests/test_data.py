"""Offline tests for reshaping, validation and raw Parquet storage.

Network fetching (``fetch_observations`` / ``resolve_stations``) is exercised
manually against live DWD; these tests use synthetic frames only.
"""

from __future__ import annotations

import pandas as pd
import pytest

from climatelens.data import (
    REQUIRED_PARAMETERS,
    backfill_range,
    default_incremental_range,
    read_raw,
    to_wide,
    validate,
    write_raw_partitions,
)

PARAMS = list(REQUIRED_PARAMETERS) + ["radiation_global"]


def _long_row(station_id, city, dataset, parameter, day, value):
    return {
        "station_id": station_id,
        "city": city,
        "dataset": dataset,
        "parameter": parameter,
        "date": pd.Timestamp(day),
        "value": value,
        "quality": 10,
    }


def _synthetic_long(days=("2024-01-01", "2024-01-02", "2024-01-03")):
    rows = []
    for day in days:
        for p in REQUIRED_PARAMETERS:
            rows.append(_long_row("00433", "berlin", "climate_summary", p, day, 5.0))
        rows.append(_long_row("00433", "berlin", "solar", "radiation_global", day, 120.0))
    return pd.DataFrame(rows)


def test_to_wide_one_row_per_city_date():
    wide = to_wide(_synthetic_long())
    assert list(wide.columns[:3]) == ["city", "station_id", "date"]
    assert len(wide) == 3
    assert (wide["city"] == "berlin").all()
    for p in PARAMS:
        assert p in wide.columns


def test_validate_drops_duplicate_city_date():
    long_df = pd.concat([_synthetic_long(), _synthetic_long(days=("2024-01-01",))])
    wide = to_wide(long_df)
    # to_wide already de-dups parameters; force a duplicate row explicitly
    wide = pd.concat([wide, wide.iloc[[0]]], ignore_index=True)
    clean, report = validate(wide)
    assert report.duplicate_rows_dropped == 1
    assert clean.duplicated(subset=["city", "date"]).sum() == 0


def test_validate_nulls_impossible_values():
    wide = to_wide(_synthetic_long())
    wide.loc[0, "wind_speed"] = -3.0
    wide.loc[1, "humidity"] = 250.0
    clean, report = validate(wide)
    assert report.impossible_values_nulled.get("wind_speed") == 1
    assert report.impossible_values_nulled.get("humidity") == 1
    assert pd.isna(clean.loc[0, "wind_speed"])


def test_validate_flags_missing_parameters():
    wide = to_wide(_synthetic_long()).drop(columns=["radiation_global"])
    _, report = validate(wide)
    assert "radiation_global" in report.missing_parameters["berlin"]


def test_validate_flags_date_gaps():
    wide = to_wide(_synthetic_long(days=("2024-01-01", "2024-01-04")))
    _, report = validate(wide)
    assert report.date_gaps["berlin"] == ["2024-01-02", "2024-01-03"]


def test_raw_parquet_round_trip_and_overwrite(tmp_path):
    wide = to_wide(_synthetic_long())
    base = str(tmp_path / "raw")

    written = write_raw_partitions(wide, base)
    assert len(written) == 3

    back = read_raw(base)
    assert len(back) == 3

    # Re-writing the same dates overwrites without duplicating rows.
    write_raw_partitions(wide, base)
    back = read_raw(base)
    assert len(back) == 3
    assert back.duplicated(subset=["city", "date"]).sum() == 0


def test_read_raw_date_filter(tmp_path):
    import datetime as dt

    wide = to_wide(_synthetic_long())
    base = str(tmp_path / "raw")
    write_raw_partitions(wide, base)
    subset = read_raw(base, start=dt.date(2024, 1, 2), end=dt.date(2024, 1, 2))
    assert len(subset) == 1
    assert subset.iloc[0]["date"] == pd.Timestamp("2024-01-02")


def test_gcs_uri_not_supported_yet():
    with pytest.raises(NotImplementedError):
        write_raw_partitions(to_wide(_synthetic_long()), "gs://bucket/raw")


def test_date_range_helpers_are_ordered():
    for lo, hi in (default_incremental_range(), backfill_range(3)):
        assert lo < hi
    assert backfill_range(3)[0] < backfill_range(1)[0]
