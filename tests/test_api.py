"""API and dashboard tests against a small bundle built from synthetic data."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import climatelens.api as api
from climatelens.api import app, service_cache
from climatelens.config import Settings, get_settings
from climatelens.data import write_features, write_model_bundle
from climatelens.evaluate import evaluate_all, train_final
from climatelens.features import build_features
from climatelens.predict import ForecastService
from tests.test_models import _seasonal_raw

TOKEN = "test-token"


@pytest.fixture(scope="module")
def bundle_dirs(tmp_path_factory):
    """A real (small) feature table + model bundle on disk for two cities."""
    root = tmp_path_factory.mktemp("climatelens")
    raw = pd.concat(
        [_seasonal_raw("berlin", days=500, seed=1), _seasonal_raw("munich", days=500, seed=2)],
        ignore_index=True,
    )
    features = build_features(raw)
    settings = Settings(data_dir=root / "data", model_dir=root / "models")
    write_features(features, settings.features_uri)
    evaluations = evaluate_all(features, min_train_days=365)
    trained = train_final(features, evaluations)
    manifest = {
        "version": "20260101T000000Z",
        "targets": list(trained),
        "models": {t: {"model": x.model.name} for t, x in trained.items()},
    }
    metrics = {t: ev.as_dict() for t, ev in evaluations.items()}
    write_model_bundle(
        {t: x.model for t, x in trained.items()}, manifest, metrics, str(root / "models")
    )
    return root


def _client(settings: Settings) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: settings
    service_cache.invalidate()
    return TestClient(app)


@pytest.fixture
def client(bundle_dirs):
    settings = Settings(
        data_dir=bundle_dirs / "data",
        model_dir=bundle_dirs / "models",
        jobs_token=SecretStr(TOKEN),
    )
    yield _client(settings)
    app.dependency_overrides.clear()
    service_cache.invalidate()


@pytest.fixture
def empty_client(tmp_path):
    yield _client(Settings(data_dir=tmp_path / "data", model_dir=tmp_path / "models"))
    app.dependency_overrides.clear()
    service_cache.invalidate()


def test_healthz_is_live_without_models(empty_client) -> None:
    response = empty_client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cities_lists_the_fixed_universe(empty_client):
    cities = empty_client.get("/cities").json()
    assert len(cities) == 12
    assert {"city", "station", "solar_station", "solar_distance_km"} <= set(cities[0])


def test_forecast_all(client):
    body = client.get("/forecast").json()
    assert body["model_version"] == "20260101T000000Z"
    assert "Not medical" in body["disclaimer"]
    assert [f["city"] for f in body["forecasts"]] == ["berlin", "munich"]  # config order
    f = body["forecasts"][0]
    assert f["valid_for"] == "2023-05-16"  # day after the last observation
    assert f["stale"] is True
    assert 0.0 <= f["frost"]["probability"] <= 1.0
    assert f["solar"]["kwh_m2"] >= 0
    assert f["wind"]["power_proxy"] == pytest.approx(f["wind"]["speed_ms"] ** 3, rel=0.01)


def test_forecast_city_and_unknown_city(client):
    assert client.get("/forecast/munich").json()["city"] == "munich"
    assert client.get("/forecast/atlantis").status_code == 404
    assert client.get("/forecast/hamburg").status_code == 404  # known city, no data


def test_forecast_is_fresh_when_data_is_recent(bundle_dirs):
    settings = Settings(data_dir=bundle_dirs / "data", model_dir=bundle_dirs / "models")
    service = ForecastService.load(settings)
    berlin = service.forecast("berlin", today=date(2023, 5, 16))
    assert berlin["data_age_days"] == 1
    assert berlin["stale"] is False


def test_metrics_summary(client):
    body = client.get("/metrics").json()
    frost = body["targets"]["target_frost"]
    assert frost["primary_metric"] == "brier"
    assert frost["reference_baseline"] == "climatology"
    assert frost["served_model"] in frost["models"]
    assert "per_city" not in frost
    per_city = client.get("/metrics", params={"per_city": True}).json()
    assert set(per_city["targets"]["target_wind_speed"]["per_city"]) == {"berlin", "munich"}


def test_dashboard_renders_three_cards(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for title in ("Frost risk", "Solar potential", "Wind potential"):
        assert title in html
    assert "Berlin" in html and "Munich" in html
    assert "Not medical, agricultural, or safety advice" in html
    assert "20260101T000000Z" in html


def test_without_a_bundle_forecasts_are_unavailable(empty_client):
    assert empty_client.get("/forecast").status_code == 503
    assert empty_client.get("/metrics").status_code == 503
    page = empty_client.get("/")
    assert page.status_code == 503
    assert "No forecasts yet" in page.text


def test_jobs_disabled_without_configured_token(empty_client):
    assert empty_client.post("/jobs/ingest").status_code == 403
    assert empty_client.post("/jobs/train").status_code == 403


@pytest.mark.parametrize("header", [None, "Bearer wrong", f"Basic {TOKEN}", TOKEN])
def test_jobs_reject_bad_credentials(client, header):
    headers = {"Authorization": header} if header else {}
    response = client.post("/jobs/train", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_job_runs_with_token_and_reloads_service(client, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "run_incremental_ingest", lambda s: calls.append("ingest") or {})
    monkeypatch.setattr(api, "run_features", lambda s: calls.append("features") or {})
    client.get("/forecast")  # warm the cache
    response = client.post("/jobs/ingest", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert calls == ["ingest", "features"]
    assert service_cache._service is None  # next request reloads the new data


def test_job_rejected_while_another_job_runs(client):
    assert api._job_lock.acquire(blocking=False)
    try:
        response = client.post("/jobs/train", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 409
    finally:
        api._job_lock.release()


def test_job_with_missing_input_reports_conflict(empty_client, tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data", model_dir=tmp_path / "models", jobs_token=SecretStr(TOKEN)
    )
    client = _client(settings)
    response = client.post("/jobs/train", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 409
    assert "feature table" in response.json()["detail"]
