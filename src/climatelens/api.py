"""HTTP API and server-rendered dashboard.

Public, read-only: ``/``, ``/cities``, ``/forecast``, ``/forecast/{city}``,
``/metrics``, ``/healthz``. Protected: ``POST /jobs/ingest`` and
``POST /jobs/train`` require ``Authorization: Bearer <CLIMATELENS_JOBS_TOKEN>``
and are disabled when no token is configured.
"""

from __future__ import annotations

import hmac
import threading
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
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

DISCLAIMER = (
    "ClimateLens is a technical demonstrator for educational and research purposes. "
    "Its forecasts are statistical estimates and must not be relied on for medical, "
    "agricultural, or safety-critical decisions. For official forecasts and warnings, "
    "consult the Deutscher Wetterdienst (DWD)."
)

#: Most cities the dashboard shows side by side.
MAX_DASHBOARD_CITIES = 3
DEFAULT_DASHBOARD_CITY = "berlin"

app = FastAPI(title="ClimateLens", version=__version__)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["nice_date"] = lambda iso: date.fromisoformat(iso).strftime("%a %d %b %Y")
templates.env.filters["month_name"] = lambda iso: date.fromisoformat(iso).strftime("%B")


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


def selected_cities(requested: list[str]) -> list[str]:
    """Known city slugs from the query, de-duplicated, capped, with a default."""
    chosen = [c for c in dict.fromkeys(requested) if c in CITIES_BY_SLUG]
    return chosen[:MAX_DASHBOARD_CITIES] or [DEFAULT_DASHBOARD_CITY]


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    settings: SettingsDep,
    city: Annotated[list[str], Query()] = [],  # noqa: B006 - FastAPI query default
) -> HTMLResponse:
    selected = selected_cities(city)
    context: dict[str, object] = {
        "cities": CITIES,
        "station_count": len({c.station_id for c in CITIES} | {c.solar_station_id for c in CITIES}),
        "selected": selected,
        "max_cities": MAX_DASHBOARD_CITIES,
        "disclaimer": DISCLAIMER,
    }
    try:
        service = service_cache.get(settings)
    except ForecastUnavailable as exc:
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {**context, "available": False, "reason": str(exc)},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    by_city = {f["city"]: f for f in service.forecasts()}
    panels = [by_city[slug] for slug in selected if slug in by_city]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            **context,
            "available": True,
            "panels": panels,
            "accuracy": service.accuracy(),
            "about": service.about(),
            "version": service.version,
            "any_stale": any(p["stale"] for p in panels),
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
