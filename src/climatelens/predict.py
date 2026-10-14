"""Next-day forecasts from the served model bundle and the latest feature rows.

The forecast for city ``c`` is issued from its most recent feature row (day
``t``, the last day with observations) and is valid for ``t + 1``. When the
data is older than :data:`STALE_AFTER_DAYS` the forecast is still returned but
flagged ``stale`` - the forecast date is then in the past.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from climatelens.config import CITIES_BY_SLUG, Settings, get_logger
from climatelens.data import read_features, read_model_bundle
from climatelens.evaluate import FROST_DECISION_THRESHOLD
from climatelens.features import latest_rows, wind_power_proxy
from climatelens.models import is_classification

log = get_logger(__name__)

#: Observations older than this (days before today) mark a forecast as stale.
STALE_AFTER_DAYS = 3

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


@dataclass
class ForecastService:
    models: dict[str, object]
    manifest: dict[str, object]
    metrics: dict[str, dict]
    latest: pd.DataFrame
    #: Mean of v^3 per city over the feature table, for "vs typical" wind power.
    typical_wind_power: dict[str, float]

    @classmethod
    def load(cls, settings: Settings) -> ForecastService:
        try:
            models, manifest, metrics = read_model_bundle(settings.models_uri)
        except FileNotFoundError as exc:
            raise ForecastUnavailable(str(exc)) from exc
        features = read_features(settings.features_uri)
        if features.empty:
            raise ForecastUnavailable("no feature table; run 'climatelens features'")
        typical = (
            wind_power_proxy(features["wind_speed"]).groupby(features["city"]).mean().to_dict()
        )
        log.info("forecast service loaded", extra={"version": manifest.get("version")})
        return cls(models, manifest, metrics, latest_rows(features), typical)

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
        power = float(wind_power_proxy(wind))
        typical = self.typical_wind_power.get(slug, float("nan"))
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
            "valid_for": (based_on + timedelta(days=1)).isoformat(),
            "data_age_days": age,
            "stale": age > STALE_AFTER_DAYS,
            "frost": {
                "probability": round(frost_p, 3),
                "expected": frost_p >= FROST_DECISION_THRESHOLD,
            },
            "solar": {"kwh_m2": _num(solar, 2)},
            "wind": {
                "speed_ms": _num(wind, 2),
                "power_proxy": _num(power, 1),
                "power_vs_typical": _num(power / typical, 2) if typical else None,
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
