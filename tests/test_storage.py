"""Storage layer against an in-memory fake of the google-cloud-storage client.

The same functions serve local paths and gs:// URIs; these tests run the gs://
branch offline so CI never needs cloud credentials.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from google.api_core.exceptions import NotFound

import climatelens.data as data
from climatelens.config import Settings
from climatelens.data import (
    fingerprint,
    latest_model_version,
    latest_raw_date,
    read_features,
    read_model_bundle,
    read_raw,
    to_wide,
    write_features,
    write_model_bundle,
    write_raw_partitions,
)
from climatelens.pipeline import run_features
from tests.test_data import _synthetic_long


class _FakeBlob:
    def __init__(self, bucket: _FakeBucket, name: str) -> None:
        self.bucket, self.name = bucket, name

    @property
    def generation(self) -> int:
        return self.bucket.objects[self.name][1]

    def upload_from_string(self, payload: bytes) -> None:
        self.bucket.counter += 1
        self.bucket.objects[self.name] = (bytes(payload), self.bucket.counter)

    def download_as_bytes(self) -> bytes:
        if self.name not in self.bucket.objects:
            raise NotFound(self.name)
        return self.bucket.objects[self.name][0]

    def exists(self) -> bool:
        return self.name in self.bucket.objects


class _FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, int]] = {}
        self.counter = 0

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self, name)

    def get_blob(self, name: str) -> _FakeBlob | None:
        return _FakeBlob(self, name) if name in self.objects else None


class _FakeClient:
    def __init__(self) -> None:
        self.buckets: dict[str, _FakeBucket] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return self.buckets.setdefault(name, _FakeBucket())

    def list_blobs(self, bucket: str, prefix: str = ""):
        objects = self.bucket(bucket).objects
        return [_FakeBlob(self.bucket(bucket), n) for n in sorted(objects) if n.startswith(prefix)]


@pytest.fixture
def gcs(monkeypatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(data, "_gcs_client", lambda: client)
    return client


def test_raw_partitions_round_trip_on_gcs(gcs):
    wide = to_wide(_synthetic_long())
    written = write_raw_partitions(wide, "gs://data/raw")
    assert written[0] == "gs://data/raw/date=2024-01-01/stations.parquet"
    assert len(gcs.bucket("data").objects) == 3

    back = read_raw("gs://data/raw")
    assert len(back) == 3 and back["city"].eq("berlin").all()
    one_day = read_raw("gs://data/raw", start=dt.date(2024, 1, 2), end=dt.date(2024, 1, 2))
    assert one_day["date"].tolist() == [pd.Timestamp("2024-01-02")]
    assert latest_raw_date("gs://data/raw") == dt.date(2024, 1, 3)

    # Overwrite keeps one row per (city, date).
    write_raw_partitions(wide, "gs://data/raw")
    assert len(read_raw("gs://data/raw")) == 3


def test_read_raw_ignores_unrelated_objects(gcs):
    write_raw_partitions(to_wide(_synthetic_long(days=("2024-01-01",))), "gs://data/raw")
    gcs.bucket("data").blob("raw/README.txt").upload_from_string(b"x")
    gcs.bucket("data").blob("raw/date=bad/stations.parquet").upload_from_string(b"x")
    assert len(read_raw("gs://data/raw")) == 1


def test_missing_objects_read_as_empty(gcs):
    assert read_raw("gs://data/raw").empty
    assert latest_raw_date("gs://data/raw") is None
    assert read_features("gs://data/features/features.parquet").empty
    assert latest_model_version("gs://models/models") is None
    assert fingerprint("gs://data/features/features.parquet") is None
    with pytest.raises(FileNotFoundError):
        read_model_bundle("gs://models/models")


def test_features_and_fingerprint_on_gcs(gcs):
    frame = pd.DataFrame({"a": [1, 2]})
    uri = "gs://data/features/features.parquet"
    write_features(frame, uri)
    first = fingerprint(uri)
    pd.testing.assert_frame_equal(read_features(uri), frame)
    write_features(frame, uri)
    assert fingerprint(uri) != first  # a rewrite is visible as a change


def test_model_bundle_on_gcs_is_immutable_and_moves_latest(gcs):
    manifest = {"version": "20260101T000000Z", "targets": ["target_wind_speed"]}
    model = {"weights": [1, 2, 3]}
    write_model_bundle({"target_wind_speed": model}, manifest, {"m": 1}, "gs://models/models")
    assert latest_model_version("gs://models/models") == "20260101T000000Z"
    models, back, metrics = read_model_bundle("gs://models/models")
    assert models["target_wind_speed"] == model and back == manifest and metrics == {"m": 1}
    with pytest.raises(FileExistsError):
        write_model_bundle({}, manifest, {}, "gs://models/models")


def test_settings_point_at_buckets_when_configured():
    settings = Settings(data_bucket="d", models_bucket="m")
    assert settings.raw_uri == "gs://d/raw"
    assert settings.features_uri == "gs://d/features/features.parquet"
    assert settings.models_uri == "gs://m/models"


def test_feature_pipeline_runs_against_gcs(gcs):
    write_raw_partitions(to_wide(_synthetic_long()), "gs://d/raw")
    result = run_features(Settings(data_bucket="d", models_bucket="m"))
    assert result["uri"] == "gs://d/features/features.parquet"
    assert len(read_features(result["uri"])) == 3


def test_incremental_ingest_catches_up_after_missed_days(gcs, monkeypatch):
    import climatelens.pipeline as pipeline

    calls = []
    monkeypatch.setattr(pipeline, "run_ingest", lambda s, start, end: calls.append((start, end)))
    settings = Settings(data_bucket="d", models_bucket="m")
    start, end = pipeline.default_incremental_range(days_back=2)

    # Nothing stored yet: the normal recent window.
    pipeline.run_incremental_ingest(settings)
    assert calls[-1] == (start, end)

    # Last stored day is 10 days before the window: resume the day after it.
    last = (start - dt.timedelta(days=10)).date()
    write_raw_partitions(to_wide(_synthetic_long(days=(last.isoformat(),))), settings.raw_uri)
    pipeline.run_incremental_ingest(settings)
    assert calls[-1][0].date() == last + dt.timedelta(days=1)

    # A very long outage is capped, never a multi-year refetch.
    old = (end - dt.timedelta(days=400)).date()
    gcs.bucket("d").objects.clear()
    write_raw_partitions(to_wide(_synthetic_long(days=(old.isoformat(),))), settings.raw_uri)
    pipeline.run_incremental_ingest(settings)
    assert calls[-1][0] == end - dt.timedelta(days=pipeline.MAX_CATCH_UP_DAYS)
