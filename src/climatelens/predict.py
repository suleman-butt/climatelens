"""Next-day forecasts from the served model bundle and the latest feature rows.

The forecast for city ``c`` is issued from its most recent feature row (day
``t``, the last day with observations) and is valid for ``t + 1``. When the
data is older than :data:`STALE_AFTER_DAYS` the forecast is still returned but
flagged ``stale`` - the forecast date is then in the past.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from climatelens.config import CITIES_BY_SLUG, Settings, get_logger
from climatelens.data import read_features, read_model_bundle
from climatelens.evaluate import FROST_DECISION_THRESHOLD
from climatelens.features import latest_rows
from climatelens.models import is_classification

log = get_logger(__name__)

#: Observations older than this (days before today) mark a forecast as stale.
STALE_AFTER_DAYS = 3

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


@dataclass
class ForecastService:
    models: dict[str, object]
    manifest: dict[str, object]
    metrics: dict[str, dict]
    latest: pd.DataFrame
    #: Mean daily solar kWh/m2 per (city, month), for "vs typical" solar.
    typical_solar: dict[tuple[str, int], float] = field(default_factory=dict)
    #: Size and span of the feature table, for the "How it works" summary.
    data_summary: dict[str, object] = field(default_factory=dict)

    @classmethod
    def load(cls, settings: Settings) -> ForecastService:
        try:
            models, manifest, metrics = read_model_bundle(settings.models_uri)
        except FileNotFoundError as exc:
            raise ForecastUnavailable(str(exc)) from exc
        features = read_features(settings.features_uri)
        if features.empty:
            raise ForecastUnavailable("no feature table; run 'climatelens features'")
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
        log.info("forecast service loaded", extra={"version": manifest.get("version")})
        return cls(
            models,
            manifest,
            metrics,
            latest_rows(features),
            {(str(c), int(m)): float(v) for (c, m), v in typical_solar.dropna().items()},
            data_summary,
        )

    @property
    def version(self) -> str:
        return str(self.manifest.get("version", ""))

    def forecasts(self, today: date | None = None) -> list[dict[str, object]]:
        """One forecast per city, in the order of ``config.CITIES``."""
        today = today or datetime.now(UTC).date()
        rows = self.latest.reset_index(drop=True)
        preds = {target: model.predict(rows) for target, model in self.models.items()}
        out = [self._city_forecast(rows, i, preds, today) for i in range(len(rows))]
        order = list(CITIES_BY_SLUG)
        return sorted(out, key=lambda f: order.index(f["city"]) if f["city"] in order else 99)

    def forecast(self, slug: str, today: date | None = None) -> dict[str, object] | None:
        return next((f for f in self.forecasts(today) if f["city"] == slug), None)

    def _city_forecast(
        self, rows: pd.DataFrame, i: int, preds: dict[str, np.ndarray], today: date
    ) -> dict[str, object]:
        slug = str(rows.loc[i, "city"])
        city = CITIES_BY_SLUG[slug]
        based_on = pd.Timestamp(rows.loc[i, "date"]).date()
        age = (today - based_on).days

        frost_p = float(np.clip(preds["target_frost"][i], 0.0, 1.0))
        solar = preds["target_solar_kwh_m2"][i]
        wind = float(preds["target_wind_speed"][i])
        wind_kmh = round(wind * 3.6)
        force, wind_description = beaufort(wind_kmh)
        valid_for = based_on + timedelta(days=1)
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
            "based_on": based_on.isoformat(),
            "valid_for": valid_for.isoformat(),
            "data_age_days": age,
            "stale": age > STALE_AFTER_DAYS,
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
            metric, reference = SKILL_REFERENCE[target]
            served = self.manifest["models"][target]["model"]  # type: ignore[index]
            overall = ev["overall"]
            entry: dict[str, object] = {
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

    def accuracy(self, slug: str | None = None) -> dict[str, float | None]:
        """Plain accuracy figures for the served models (walk-forward).

        For one city when ``slug`` is given, otherwise across all cities.
        """
        out: dict[str, float | None] = {}
        for target, key, metric in (
            ("target_frost", "frost_accuracy", "accuracy"),
            ("target_solar_kwh_m2", "solar_mae", "mae"),
            ("target_wind_speed", "wind_mae", "mae"),
        ):
            ev = self.metrics.get(target, {})
            served = self.manifest["models"][target]["model"]  # type: ignore[index]
            by_model = ev.get("overall", {})
            if slug:
                by_model = ev.get("per_city", {}).get(slug, by_model)
            scores = by_model.get(served, {})
            out[key] = scores.get(metric)
        return out

    def about(self) -> dict[str, object]:
        """Plain facts about the data and the served models for the dashboard."""
        models = self.manifest.get("models", {})
        served = sorted({str(m.get("model", "")) for m in models.values()})  # type: ignore[union-attr]
        train_ends = [str(m.get("train_end", "")) for m in models.values()]  # type: ignore[union-attr]
        created = str(self.manifest.get("created_at", ""))
        return {
            **self.data_summary,
            "served_models": served,
            "trained_on": created[:10] or None,
            "trained_through": max(train_ends) if any(train_ends) else None,
        }
