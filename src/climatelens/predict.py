"""Forecasts for tomorrow from the served model bundle and the latest feature rows.

"Today" is the calendar date in Germany (:data:`LOCAL_TZ`). DWD publishes a
day's observations the following day, so a city's newest feature row (day
``t``) is usually 1-2 days old. The forecast always targets *tomorrow*, using
the model whose horizon ``h`` satisfies ``t + h == tomorrow``. If the data is
older than the longest horizon allows, the furthest horizon is used and the
forecast is flagged ``stale`` (its date is then before tomorrow).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from climatelens.config import CITIES_BY_SLUG, Settings, get_logger
from climatelens.data import (
    fingerprint,
    latest_model_version,
    read_features,
    read_json,
    read_model_bundle,
    write_json,
)
from climatelens.evaluate import FROST_DECISION_THRESHOLD
from climatelens.features import HORIZONS, TARGETS, latest_rows, split_target, target_name
from climatelens.models import is_classification

log = get_logger(__name__)

#: The service's notion of "today" and "tomorrow": the date in Germany.
LOCAL_TZ = ZoneInfo("Europe/Berlin")


def local_today() -> date:
    return datetime.now(LOCAL_TZ).date()


#: Frost probability bands for the plain-language risk level.
FROST_RISK_BANDS: tuple[tuple[float, str], ...] = ((0.5, "high"), (0.2, "moderate"), (0.0, "low"))

#: Solar vs the city's usual value for the month: +/-15% counts as "typical".
SOLAR_TYPICAL_BAND = 0.15

#: Beaufort scale on whole km/h (the value the dashboard shows), so the label
#: always agrees with the displayed speed: (exclusive upper bound, force, name).
BEAUFORT_KMH: tuple[tuple[float, int, str], ...] = (
    (1, 0, "Calm"),
    (6, 1, "Light air"),
    (12, 2, "Light breeze"),
    (20, 3, "Gentle breeze"),
    (29, 4, "Moderate breeze"),
    (39, 5, "Fresh breeze"),
    (50, 6, "Strong breeze"),
    (62, 7, "Near gale"),
    (75, 8, "Gale"),
    (89, 9, "Strong gale"),
    (103, 10, "Storm"),
    (118, 11, "Violent storm"),
    (math.inf, 12, "Hurricane force"),
)

#: Baseline the skill score is quoted against, per target (matches evaluate.py).
SKILL_REFERENCE: dict[str, tuple[str, str]] = {
    "target_frost": ("brier", "climatology"),
    "target_solar_kwh_m2": ("mae", "persistence"),
    "target_wind_speed": ("mae", "persistence"),
}


class ForecastUnavailable(RuntimeError):
    """No model bundle or no feature table yet - nothing to forecast from."""


def _num(value: float, digits: int) -> float | None:
    value = float(value)
    return None if math.isnan(value) else round(value, digits)


def frost_risk(probability: float) -> str:
    return next(level for floor, level in FROST_RISK_BANDS if probability >= floor)


def beaufort(speed_kmh: float) -> tuple[int, str]:
    """Beaufort force and description for a wind speed in km/h (rounded first)."""
    kmh = round(speed_kmh)
    return next((force, name) for upper, force, name in BEAUFORT_KMH if kmh < upper)


def solar_outlook(vs_typical: float | None) -> str | None:
    """ "below" / "typical" / "above" the city's usual solar energy for the month."""
    if vs_typical is None:
        return None
    if vs_typical < 1 - SOLAR_TYPICAL_BAND:
        return "below"
    if vs_typical > 1 + SOLAR_TYPICAL_BAND:
        return "above"
    return "typical"


#: Bump when the snapshot layout changes; older snapshots are then rebuilt.
SNAPSHOT_SCHEMA = 1


@dataclass
class ForecastService:
    """Everything the dashboard and API need, without models or the feature table.

    ``predictions`` holds every served target (all horizons) for each city's
    newest feature row; which horizon is "tomorrow" is decided per request, so
    a snapshot written in the afternoon stays correct after midnight.
    """

    manifest: dict[str, object]
    metrics: dict[str, dict]
    #: target name -> city slug -> predicted value
    predictions: dict[str, dict[str, float]]
    #: city slug -> ISO date of its newest observation
    based_on: dict[str, str]
    #: Mean daily solar kWh/m2 per (city, month), for "vs typical" solar.
    typical_solar: dict[tuple[str, int], float] = field(default_factory=dict)
    #: Size and span of the feature table, for the "How it works" summary.
    data_summary: dict[str, object] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        models: dict[str, object],
        manifest: dict[str, object],
        metrics: dict[str, dict],
        features: pd.DataFrame,
    ) -> ForecastService:
        """Predict every target for each city's newest row (the slow path)."""
        rows = latest_rows(features).reset_index(drop=True)
        cities = rows["city"].astype(str).tolist()
        predictions = {
            target: dict(zip(cities, (float(v) for v in model.predict(rows)), strict=True))
            for target, model in models.items()
        }
        based_on = {
            c: pd.Timestamp(d).date().isoformat() for c, d in zip(cities, rows["date"], strict=True)
        }
        months = pd.to_datetime(features["date"]).dt.month
        typical_solar = features["solar_kwh_m2"].groupby([features["city"], months]).mean()
        dates = pd.to_datetime(features["date"])
        data_summary = {
            "records": len(features),
            "cities": int(features["city"].nunique()),
            "start": dates.min().date().isoformat(),
            "end": dates.max().date().isoformat(),
            "years": round((dates.max() - dates.min()).days / 365.25, 1),
        }
        return cls(
            manifest,
            metrics,
            predictions,
            based_on,
            {(str(c), int(m)): float(v) for (c, m), v in typical_solar.dropna().items()},
            data_summary,
        )

    def to_snapshot(self, features_fingerprint: str | None) -> dict[str, object]:
        return {
            "schema": SNAPSHOT_SCHEMA,
            "model_version": self.version,
            "features_fingerprint": features_fingerprint,
            "generated_at": datetime.now(LOCAL_TZ).isoformat(),
            "manifest": {k: self.manifest.get(k) for k in ("version", "created_at", "models")},
            "metrics": self.metrics,
            "predictions": self.predictions,
            "based_on": self.based_on,
            "typical_solar": [[c, m, v] for (c, m), v in self.typical_solar.items()],
            "data_summary": self.data_summary,
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> ForecastService:
        return cls(
            snapshot["manifest"],
            snapshot["metrics"],
            snapshot["predictions"],
            snapshot["based_on"],
            {(str(c), int(m)): float(v) for c, m, v in snapshot["typical_solar"]},
            snapshot["data_summary"],
        )

    @classmethod
    def load(cls, settings: Settings, write_snapshot: bool = True) -> ForecastService:
        """Serve from the snapshot when it matches the current model and data.

        Otherwise build from the model bundle and feature table once, and save
        a fresh snapshot (if allowed) so the next cold start is fast again.
        """
        version = latest_model_version(settings.models_uri)
        if version is None:
            raise ForecastUnavailable(f"no model bundle under {settings.models_uri}")
        features_fp = fingerprint(settings.features_uri)
        if features_fp is None:
            raise ForecastUnavailable("no feature table; run 'climatelens features'")

        snapshot = read_json(settings.snapshot_uri)
        if (
            snapshot
            and snapshot.get("schema") == SNAPSHOT_SCHEMA
            and snapshot.get("model_version") == version
            and snapshot.get("features_fingerprint") == features_fp
        ):
            log.info("forecast service loaded from snapshot", extra={"version": version})
            return cls.from_snapshot(snapshot)

        try:
            models, manifest, metrics = read_model_bundle(settings.models_uri, version)
        except FileNotFoundError as exc:
            raise ForecastUnavailable(str(exc)) from exc
        features = read_features(settings.features_uri)
        if features.empty:
            raise ForecastUnavailable("no feature table; run 'climatelens features'")
        service = cls.build(models, manifest, metrics, features)
        log.info("forecast service built from models", extra={"version": version})
        if write_snapshot:
            try:
                write_json(settings.snapshot_uri, service.to_snapshot(features_fp))
            except Exception:  # a read-only replica still serves, just slower
                log.warning("could not write forecast snapshot", exc_info=True)
        return service

    @property
    def version(self) -> str:
        return str(self.manifest.get("version", ""))

    def forecasts(self, today: date | None = None) -> list[dict[str, object]]:
        """One forecast per city, in the order of ``config.CITIES``."""
        today = today or local_today()
        out = [self._city_forecast(slug, today) for slug in self.based_on if slug in CITIES_BY_SLUG]
        order = list(CITIES_BY_SLUG)
        return sorted(out, key=lambda f: order.index(f["city"]) if f["city"] in order else 99)

    def forecast(self, slug: str, today: date | None = None) -> dict[str, object] | None:
        return next((f for f in self.forecasts(today) if f["city"] == slug), None)

    def _city_forecast(self, slug: str, today: date) -> dict[str, object]:
        city = CITIES_BY_SLUG[slug]
        based_on = date.fromisoformat(self.based_on[slug])
        age = (today - based_on).days
        tomorrow = today + timedelta(days=1)
        horizon = self.choose_horizon(based_on, tomorrow)
        valid_for = based_on + timedelta(days=horizon)

        def pred(base: str) -> float:
            return float(self.predictions[target_name(base, horizon)][slug])

        frost_p = float(np.clip(pred("target_frost"), 0.0, 1.0))
        solar = pred("target_solar_kwh_m2")
        wind = pred("target_wind_speed")
        wind_kmh = round(wind * 3.6)
        force, wind_description = beaufort(wind_kmh)
        typical_sun = self.typical_solar.get((slug, valid_for.month))
        vs_typical = _num(solar / typical_sun, 2) if typical_sun else None
        return {
            "city": slug,
            "name": city.name,
            "station": {
                "id": city.station_id,
                "name": city.station_name,
                "distance_km": city.station_distance_km,
            },
            "solar_station": {
                "id": city.solar_station_id,
                "name": city.solar_station_name,
                "distance_km": city.solar_distance_km,
            },
            "issued_on": today.isoformat(),
            "based_on": based_on.isoformat(),
            "valid_for": valid_for.isoformat(),
            "horizon_days": horizon,
            "data_age_days": age,
            "stale": valid_for != tomorrow,
            "frost": {
                "probability": round(frost_p, 3),
                "expected": frost_p >= FROST_DECISION_THRESHOLD,
                "risk": frost_risk(frost_p),
            },
            "solar": {
                "kwh_m2": _num(solar, 2),
                "typical_kwh_m2": _num(typical_sun, 2) if typical_sun else None,
                "vs_typical": vs_typical,
                "outlook": solar_outlook(vs_typical),
            },
            "wind": {
                "speed_ms": _num(wind, 2),
                "speed_kmh": wind_kmh,
                "beaufort": force,
                "description": wind_description,
            },
            "model_version": self.version,
        }

    def metrics_summary(self, per_city: bool = False) -> dict[str, object]:
        """Served model vs its baselines, per target (from the bundle's metrics.json)."""
        targets: dict[str, object] = {}
        for target, ev in self.metrics.items():
            base, horizon = split_target(target)
            metric, reference = SKILL_REFERENCE[base]
            served = self.manifest["models"][target]["model"]  # type: ignore[index]
            overall = ev["overall"]
            entry: dict[str, object] = {
                "target": base,
                "horizon_days": horizon,
                "served_model": served,
                "type": "classification" if is_classification(target) else "regression",
                "primary_metric": metric,
                "reference_baseline": reference,
                "score": overall[served][metric],
                "reference_score": overall[reference][metric],
                "skill": overall[served].get(f"{metric}_skill_vs_{reference}"),
                "test_start": ev["test_start"],
                "test_end": ev["test_end"],
                "test_rows": ev["test_rows"],
                "folds": ev["folds"],
                "models": overall,
            }
            if per_city:
                entry["per_city"] = ev["per_city"]
            targets[target] = entry
        return {"model_version": self.version, "targets": targets}

    def available_horizons(self) -> list[int]:
        """Horizons with a served model for every target (old bundles: only 1)."""
        served = self.predictions
        return [h for h in HORIZONS if all(target_name(b, h) in served for b in TARGETS)]

    def choose_horizon(self, based_on: date, tomorrow: date) -> int:
        """Days ahead of ``based_on`` that land on tomorrow, within what is served."""
        available = self.available_horizons() or [1]
        wanted = (tomorrow - based_on).days
        return min(max(wanted, available[0]), available[-1])

    def accuracy(self, slug: str | None = None, horizon: int = 1) -> dict[str, float | None]:
        """Plain accuracy figures for the served models (walk-forward).

        For one city when ``slug`` is given, otherwise across all cities; for
        the given forecast horizon (falls back to 1 day if it is not served).
        """
        out: dict[str, float | None] = {}
        for base, key, metric in (
            ("target_frost", "frost_accuracy", "accuracy"),
            ("target_solar_kwh_m2", "solar_mae", "mae"),
            ("target_wind_speed", "wind_mae", "mae"),
        ):
            target = target_name(base, horizon)
            if target not in self.metrics:
                target = base
            ev = self.metrics.get(target, {})
            served = self.manifest["models"][target]["model"]  # type: ignore[index]
            by_model = ev.get("overall", {})
            if slug:
                by_model = ev.get("per_city", {}).get(slug, by_model)
            scores = by_model.get(served, {})
            out[key] = scores.get(metric)
        return out

    def about(self, horizon: int = 1) -> dict[str, object]:
        """Plain facts about the data and the served models for the dashboard.

        ``served_models`` covers the targets at ``horizon`` - the forecast the
        page is actually showing.
        """
        models = self.manifest.get("models", {})
        shown = [models.get(target_name(b, horizon)) or models.get(b, {}) for b in TARGETS]
        served = sorted({str(m.get("model", "")) for m in shown if m})  # type: ignore[union-attr]
        train_ends = [str(m.get("train_end", "")) for m in models.values()]  # type: ignore[union-attr]
        created = str(self.manifest.get("created_at", ""))
        return {
            **self.data_summary,
            "served_models": served,
            "trained_on": created[:10] or None,
            "trained_through": max(train_ends) if any(train_ends) else None,
        }
