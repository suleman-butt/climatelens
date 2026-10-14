"""API and dashboard tests against a small bundle built from synthetic data."""

from __future__ import annotations

import re
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
        "created_at": "2026-01-01T00:00:00+00:00",
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


@pytest.mark.parametrize("path", ["/health", "/healthz"])
def test_health_is_live_without_models(empty_client, path) -> None:
    response = empty_client.get(path)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cities_lists_the_fixed_universe(empty_client):
    cities = empty_client.get("/cities").json()
    assert len(cities) == 12
    assert {"city", "station", "solar_station", "solar_distance_km"} <= set(cities[0])


def test_forecast_all(client):
    body = client.get("/forecast").json()
    assert body["model_version"] == "20260101T000000Z"
    assert "safety-critical decisions" in body["disclaimer"]
    assert [f["city"] for f in body["forecasts"]] == ["berlin", "munich"]  # config order
    f = body["forecasts"][0]
    assert f["valid_for"] == "2023-05-16"  # day after the last observation
    assert f["stale"] is True
    assert 0.0 <= f["frost"]["probability"] <= 1.0
    assert f["frost"]["risk"] in {"low", "moderate", "high"}
    assert f["solar"]["vs_typical"] > 0
    assert f["solar"]["kwh_m2"] >= 0
    assert f["wind"]["speed_kmh"] == round(f["wind"]["speed_ms"] * 3.6)
    assert f["wind"]["description"]
    assert f["solar"]["outlook"] in {"below", "typical", "above"}


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


def _panels(html: str) -> list[str]:
    return re.findall(
        r'<section class="panel">\s*<div class="panel-head">\s*<h2>([^<]+)</h2>', html
    )


def test_dashboard_defaults_to_one_city_with_three_cards(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert _panels(html) == ["Berlin"]
    for title in ("Frost", "Sunshine", "Wind"):
        assert html.count(f"<h3>{title}<") == 1
    assert "About these forecasts" in html
    assert "safety-critical decisions" in html
    assert "How reliable is this?" in html
    assert html.count("right on") == 1  # accuracy is stated once, not per card
    assert "Outdoors" in html and "Energy</span>" not in html
    assert "Walk-forward" not in html  # no evaluation jargon on the dashboard


def test_dashboard_shows_selected_cities_in_requested_order(client):
    html = client.get("/", params={"city": ["munich", "berlin"]}).text
    assert _panels(html) == ["Munich", "Berlin"]
    assert 'value="munich" checked' in html and 'value="berlin" checked' in html


def test_dashboard_caps_selection_and_ignores_unknown_cities(client):
    params = {"city": ["atlantis", "munich", "munich", "berlin", "hamburg", "cologne"]}
    html = client.get("/", params=params).text
    # Hamburg is a valid third choice but has no data in this bundle, so no panel.
    assert _panels(html) == ["Munich", "Berlin"]
    assert 'value="hamburg" checked' in html
    assert 'value="cologne" checked' not in html


def test_selected_cities_helper():
    assert api.selected_cities([]) == ["berlin"]
    assert api.selected_cities(["atlantis"]) == ["berlin"]
    assert api.selected_cities(["essen", "bremen", "essen", "leipzig", "dresden"]) == [
        "essen",
        "bremen",
        "leipzig",
    ]


def test_without_a_bundle_forecasts_are_unavailable(empty_client):
    assert empty_client.get("/forecast").status_code == 503
    assert empty_client.get("/metrics").status_code == 503
    page = empty_client.get("/")
    assert page.status_code == 503
    assert "Forecasts are not available yet" in page.text
    assert "Cities" in page.text  # the picker still renders


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


def test_frost_risk_bands():
    from climatelens.predict import frost_risk

    assert [frost_risk(p) for p in (0.0, 0.19, 0.2, 0.49, 0.5, 1.0)] == [
        "low",
        "low",
        "moderate",
        "moderate",
        "high",
        "high",
    ]


def test_accuracy_overall_and_per_city(bundle_dirs):
    settings = Settings(data_dir=bundle_dirs / "data", model_dir=bundle_dirs / "models")
    service = ForecastService.load(settings)
    for acc in (service.accuracy(), service.accuracy("berlin")):
        assert 0.0 <= acc["frost_accuracy"] <= 1.0
        assert acc["solar_mae"] > 0 and acc["wind_mae"] > 0


def test_beaufort_labels_follow_displayed_kmh():
    from climatelens.predict import beaufort

    assert beaufort(0.4) == (0, "Calm")
    assert beaufort(11.6) == (3, "Gentle breeze")  # shown as 12 km/h
    assert beaufort(11.4) == (2, "Light breeze")  # shown as 11 km/h
    assert beaufort(45) == (6, "Strong breeze")
    assert beaufort(130) == (12, "Hurricane force")


def test_solar_outlook_bands():
    from climatelens.predict import solar_outlook

    assert [solar_outlook(r) for r in (None, 0.8, 0.85, 1.0, 1.15, 1.2)] == [
        None,
        "below",
        "typical",
        "typical",
        "typical",
        "above",
    ]


def test_dashboard_offers_light_dark_auto_theme(client):
    html = client.get("/").text
    for choice in ("light", "dark", "auto"):
        assert f'data-theme-choice="{choice}"' in html
    # Forced dark, and system dark unless the viewer chose light.
    assert ':root[data-theme="dark"]' in html
    assert ':root:not([data-theme="light"])' in html
    # Saved choice is applied in <head>, before the body paints.
    assert html.index("climatelens-theme") < html.index("<body>")


def test_dashboard_brand_and_how_it_works(client):
    html = client.get("/").text
    assert 'class="logo"' in html and 'rel="icon"' in html
    assert "How ClimateLens works" in html
    for step in ("Collect", "Learn", "Forecast", "Stay up to date"):
        assert f"</span>{step}</h3>" in html
    assert "Developed by Suleman Butt" in html
    assert html.count("MSc DevOps &amp; Cloud Computing") == 1  # credited once, in the footer
    assert "IU International University" in html
    assert "18 weather stations serving 12 cities" in html
    # Real numbers from the bundle and feature table, not hard-coded text.
    assert "daily records" in html and "Last trained on" in html


def test_how_it_works_claims_follow_the_served_model(client, monkeypatch):
    html = client.get("/").text
    service = service_cache._service
    if service.about()["served_models"] == ["lightgbm"]:
        assert "LightGBM" in html and "it beat" in html
    monkeypatch.setattr(service, "about", lambda: {"served_models": ["persistence"]})
    html = client.get("/").text
    assert "LightGBM" not in html and "it beat" not in html
    assert "the most accurate method was chosen" in html


def test_about_summarises_data_and_model(bundle_dirs):
    settings = Settings(data_dir=bundle_dirs / "data", model_dir=bundle_dirs / "models")
    about = ForecastService.load(settings).about()
    assert about["records"] == 1000 and about["cities"] == 2
    assert about["start"] == "2022-01-01"
    assert about["years"] > 1


# --- Cloud Scheduler OIDC auth -------------------------------------------------

SCHEDULER = "climatelens-scheduler@climatelens-iu.iam.gserviceaccount.com"


@pytest.fixture
def oidc_client(bundle_dirs, monkeypatch):
    settings = Settings(
        data_dir=bundle_dirs / "data",
        model_dir=bundle_dirs / "models",
        jobs_oidc_audience="climatelens-jobs",
        jobs_oidc_service_account=SCHEDULER,
    )
    tokens = {
        "good": {"email": SCHEDULER, "email_verified": True},
        "other-sa": {"email": "attacker@example.iam.gserviceaccount.com", "email_verified": True},
        "unverified": {"email": SCHEDULER, "email_verified": False},
    }

    def fake_verify(token, audience):
        assert audience == "climatelens-jobs"
        if token not in tokens:
            raise ValueError("bad signature")
        return tokens[token]

    monkeypatch.setattr(api, "_verify_google_token", fake_verify)
    monkeypatch.setattr(api, "run_train", lambda s: {"event": "train_result"})
    yield _client(settings)
    app.dependency_overrides.clear()
    service_cache.invalidate()


def test_scheduler_oidc_token_is_accepted(oidc_client):
    response = oidc_client.post("/jobs/train", headers={"Authorization": "Bearer good"})
    assert response.status_code == 200


@pytest.mark.parametrize("token", ["other-sa", "unverified", "forged"])
def test_other_oidc_tokens_are_rejected(oidc_client, token):
    response = oidc_client.post("/jobs/train", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_shared_token_does_not_unlock_oidc_only_config(oidc_client):
    response = oidc_client.post("/jobs/train", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 401


# --- Cache refresh across instances --------------------------------------------


def test_cache_reloads_when_a_new_bundle_is_published(bundle_dirs, monkeypatch):
    settings = Settings(data_dir=bundle_dirs / "data", model_dir=bundle_dirs / "models")
    cache = api.ServiceCache(refresh_seconds=0)
    first = cache.get(settings)
    assert cache.get(settings) is first  # nothing changed: same object

    monkeypatch.setattr(api, "latest_model_version", lambda uri: "20990101T000000Z")
    monkeypatch.setattr(api.ForecastService, "load", classmethod(lambda cls, s: "reloaded"))
    assert cache.get(settings) == "reloaded"


def test_cache_skips_checks_within_refresh_window(bundle_dirs, monkeypatch):
    settings = Settings(data_dir=bundle_dirs / "data", model_dir=bundle_dirs / "models")
    cache = api.ServiceCache(refresh_seconds=3600)
    first = cache.get(settings)
    monkeypatch.setattr(api, "latest_model_version", lambda uri: pytest.fail("checked too soon"))
    assert cache.get(settings) is first


def test_dashboard_shows_app_version_and_update_note(client):
    from climatelens import __version__

    html = client.get("/").text
    assert f"App v{__version__}" in html
    assert "Model 20260101T000000Z" in html  # the served bundle, read at runtime
    assert "Data: Deutscher Wetterdienst (DWD) open data" in html
    assert "API Documentation" in html
    assert "Updated daily from DWD observations" in html


def test_dashboard_template_is_clean_utf8():
    from pathlib import Path

    import climatelens

    raw = (Path(climatelens.__file__).parent / "templates" / "dashboard.html").read_bytes()
    assert b"\x00" not in raw
    raw.decode("utf-8")
