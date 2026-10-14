"""HTTP API and server-rendered dashboard.

Public, read-only: ``/``, ``/cities``, ``/forecast``, ``/forecast/{city}``,
``/metrics``, ``/healthz``. Protected: ``POST /jobs/ingest`` and
``POST /jobs/train`` require ``Authorization: Bearer <CLIMATELENS_JOBS_TOKEN>``
and are disabled when no token is configured.
"""

from __future__ import annotations

import hmac
import threading
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from climatelens import __version__
from climatelens.config import CITIES, CITIES_BY_SLUG, Settings, get_logger, get_settings
from climatelens.pipeline import (
    PipelineError,
    run_features,
    run_incremental_ingest,
    run_train,
)
from climatelens.predict import ForecastService, ForecastUnavailable

log = get_logger(__name__)

DISCLAIMER = "Technical demonstrator. Not medical, agricultural, or safety advice."

app = FastAPI(title="ClimateLens", version=__version__)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


class ServiceCache:
    """Loads the forecast service once; job endpoints invalidate it after writes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._service: ForecastService | None = None

    def get(self, settings: Settings) -> ForecastService:
        with self._lock:
            if self._service is None:
                self._service = ForecastService.load(settings)
            return self._service

    def invalidate(self) -> None:
        with self._lock:
            self._service = None


service_cache = ServiceCache()
#: Ingest and train write the same files; never run two jobs at once.
_job_lock = threading.Lock()

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_service(settings: SettingsDep) -> ForecastService:
    try:
        return service_cache.get(settings)
    except ForecastUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


ServiceDep = Annotated[ForecastService, Depends(get_service)]


def require_job_token(
    settings: SettingsDep, authorization: Annotated[str | None, Header()] = None
) -> None:
    expected = settings.jobs_token.get_secret_value()
    if not expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "job endpoints are disabled")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# --- Public, read-only ----------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/cities")
def cities() -> list[dict[str, object]]:
    return [
        {
            "city": c.slug,
            "name": c.name,
            "latitude": c.centre_latitude,
            "longitude": c.centre_longitude,
            "station": {"id": c.station_id, "name": c.station_name},
            "station_distance_km": c.station_distance_km,
            "solar_station": {"id": c.solar_station_id, "name": c.solar_station_name},
            "solar_distance_km": c.solar_distance_km,
        }
        for c in CITIES
    ]


@app.get("/forecast")
def forecast_all(service: ServiceDep) -> dict[str, object]:
    return {
        "model_version": service.version,
        "disclaimer": DISCLAIMER,
        "forecasts": service.forecasts(),
    }


@app.get("/forecast/{city}")
def forecast_city(city: str, service: ServiceDep) -> dict[str, object]:
    if city not in CITIES_BY_SLUG:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown city {city!r}")
    result = service.forecast(city)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no data for {city!r}")
    return {"disclaimer": DISCLAIMER, **result}


@app.get("/metrics")
def metrics(service: ServiceDep, per_city: bool = False) -> dict[str, object]:
    return service.metrics_summary(per_city=per_city)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, settings: SettingsDep) -> HTMLResponse:
    try:
        service = service_cache.get(settings)
    except ForecastUnavailable as exc:
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"available": False, "reason": str(exc), "disclaimer": DISCLAIMER},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    forecasts = service.forecasts()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "available": True,
            "forecasts": forecasts,
            "metrics": service.metrics_summary()["targets"],
            "version": service.version,
            "valid_for": max(f["valid_for"] for f in forecasts),
            "any_stale": any(f["stale"] for f in forecasts),
            "disclaimer": DISCLAIMER,
        },
    )


# --- Protected jobs -------------------------------------------------------------


def _run_job(name: str, steps) -> dict[str, object]:
    if not _job_lock.acquire(blocking=False):
        raise HTTPException(status.HTTP_409_CONFLICT, "another job is already running")
    try:
        log.info("job start", extra={"job": name})
        results = [step() for step in steps]
    except PipelineError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    finally:
        _job_lock.release()
    service_cache.invalidate()
    log.info("job done", extra={"job": name})
    return {"job": name, "status": "ok", "results": results}


@app.post("/jobs/ingest", dependencies=[Depends(require_job_token)])
def job_ingest(settings: SettingsDep) -> dict[str, object]:
    """Daily: fetch the last days from DWD, then rebuild the feature table."""
    return _run_job(
        "ingest", [lambda: run_incremental_ingest(settings), lambda: run_features(settings)]
    )


@app.post("/jobs/train", dependencies=[Depends(require_job_token)])
def job_train(settings: SettingsDep) -> dict[str, object]:
    """Periodic: walk-forward evaluation, refit, write a new model bundle."""
    return _run_job("train", [lambda: run_train(settings)])
