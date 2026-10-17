"""Forecast models and transparent baselines for the three next-day targets.

Pure DataFrame / NumPy code (no I/O). Every model follows the same tiny
interface - ``fit(train) -> self`` and ``predict(frame) -> np.ndarray`` - where
``train`` and ``frame`` are rows of the feature table (see ``features.py``).
For frost, ``predict`` returns the probability of frost, not a hard label.

Baselines (spec 5.3):

* :class:`Persistence`  - tomorrow equals today (same-day value of the target).
* :class:`Climatology`  - per-city mean of the target for the day of year,
  smoothed over a +/- 7 day circular window, learned from training rows only.
* :class:`MajorityClass` - frost only: always predict the training majority.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd

from climatelens.features import ALL_TARGETS, CURRENT_VALUES, FEATURE_COLUMNS, split_target

CLASSIFICATION_TARGETS: tuple[str, ...] = ("target_frost",)
REGRESSION_TARGETS: tuple[str, ...] = ("target_solar_kwh_m2", "target_wind_speed")

#: Half-width (days) of the day-of-year smoothing window for climatology.
CLIMATOLOGY_WINDOW_DAYS = 7

#: Fixed LightGBM settings: small, regularised, deterministic.
LGBM_PARAMS: dict[str, object] = {
    "n_estimators": 400,
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_child_samples": 40,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "random_state": 42,
    "n_jobs": 1,
    "deterministic": True,
    "force_row_wise": True,
    "verbose": -1,
}


class Model(Protocol):
    name: str

    def fit(self, train: pd.DataFrame) -> Model: ...

    def predict(self, frame: pd.DataFrame) -> np.ndarray: ...


def _check_target(target: str) -> None:
    if target not in ALL_TARGETS:
        raise ValueError(f"unknown target {target!r}; expected one of {ALL_TARGETS}")


def is_classification(target: str) -> bool:
    _check_target(target)
    return split_target(target)[0] in CLASSIFICATION_TARGETS


def _day_of_year(dates: pd.Series) -> np.ndarray:
    # Fold Feb 29 onto Feb 28 so every year has 365 slots.
    dates = pd.to_datetime(dates)
    doy = dates.dt.dayofyear.to_numpy()
    leap_after_feb = dates.dt.is_leap_year.to_numpy() & (doy > 59)
    return np.where(leap_after_feb, doy - 1, doy)


class Climatology:
    """Smoothed per-city day-of-year mean of the target (frost: frequency)."""

    name = "climatology"

    def __init__(self, target: str, window_days: int = CLIMATOLOGY_WINDOW_DAYS) -> None:
        _check_target(target)
        self.target = target
        self.horizon = split_target(target)[1]
        self.window_days = window_days
        self.table_: dict[str, np.ndarray] = {}
        self.city_mean_: dict[str, float] = {}
        self.global_mean_: float = float("nan")

    def fit(self, train: pd.DataFrame) -> Climatology:
        rows = train.loc[train[self.target].notna()]
        doy = _day_of_year(self._target_dates(rows))
        self.table_, self.city_mean_ = {}, {}
        self.global_mean_ = float(rows[self.target].mean())
        offsets = np.arange(-self.window_days, self.window_days + 1)
        for city, idx in rows.groupby("city").indices.items():
            values = rows[self.target].to_numpy()[idx]
            days = doy[idx] - 1  # 0..364
            sums = np.bincount(days, weights=values, minlength=365)
            counts = np.bincount(days, minlength=365).astype("float64")
            # Circular moving sum over the window, so late Dec borrows from early Jan.
            win_sums = sum(np.roll(sums, k) for k in offsets)
            win_counts = sum(np.roll(counts, k) for k in offsets)
            with np.errstate(invalid="ignore", divide="ignore"):
                table = win_sums / win_counts
            self.city_mean_[str(city)] = float(values.mean())
            self.table_[str(city)] = table
        return self

    def _target_dates(self, frame: pd.DataFrame) -> pd.Series:
        # Row t's target is observed on t + horizon: use that day's season.
        return pd.to_datetime(frame["date"]) + pd.Timedelta(self.horizon, unit="D")

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        doy = _day_of_year(self._target_dates(frame)) - 1
        out = np.full(len(frame), np.nan)
        cities = frame["city"].astype(str).to_numpy()
        for city in np.unique(cities):
            mask = cities == city
            table = self.table_.get(city)
            if table is not None:
                out[mask] = table[doy[mask]]
            fallback = self.city_mean_.get(city, self.global_mean_)
            out[mask] = np.where(np.isnan(out[mask]), fallback, out[mask])
        return out


class Persistence:
    """Target day equals today (any horizon); climatology fills missing days."""

    name = "persistence"

    def __init__(self, target: str) -> None:
        _check_target(target)
        self.target = target
        self.current = CURRENT_VALUES[split_target(target)[0]]
        self.fallback_ = Climatology(target)

    def fit(self, train: pd.DataFrame) -> Persistence:
        self.fallback_.fit(train)
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        today = frame[self.current].to_numpy(dtype="float64")
        missing = np.isnan(today)
        if missing.any():
            today = today.copy()
            today[missing] = self.fallback_.predict(frame.loc[missing])
        return today


class MajorityClass:
    """Frost only: always predict the most frequent training class (as 0/1)."""

    name = "majority_class"

    def __init__(self, target: str) -> None:
        if not is_classification(target):
            raise ValueError("majority-class baseline only applies to classification targets")
        self.target = target
        self.majority_: float = 0.0

    def fit(self, train: pd.DataFrame) -> MajorityClass:
        rate = float(train[self.target].dropna().mean())
        self.majority_ = 1.0 if rate > 0.5 else 0.0
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), self.majority_)


class LightGBMModel:
    """LightGBM on :data:`FEATURE_COLUMNS`; NaNs are handled natively, not imputed."""

    name = "lightgbm"

    def __init__(self, target: str, params: dict[str, object] | None = None) -> None:
        _check_target(target)
        self.target = target
        self.params = {**LGBM_PARAMS, **(params or {})}
        self.features = list(FEATURE_COLUMNS)
        self.estimator_ = None

    def fit(self, train: pd.DataFrame) -> LightGBMModel:
        from lightgbm import LGBMClassifier, LGBMRegressor

        rows = train.loc[train[self.target].notna()]
        X = rows[self.features].astype("float64")
        y = rows[self.target].to_numpy()
        if is_classification(self.target):
            self.estimator_ = LGBMClassifier(**self.params).fit(X, y.astype(int))
        else:
            self.estimator_ = LGBMRegressor(**self.params).fit(X, y)
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        if self.estimator_ is None:
            raise RuntimeError("model is not fitted")
        X = frame[self.features].astype("float64")
        if is_classification(self.target):
            return self.estimator_.predict_proba(X)[:, 1]
        pred = self.estimator_.predict(X)
        # Radiation and wind speed cannot be negative.
        return np.clip(pred, 0.0, None)

    def feature_importance(self) -> dict[str, float]:
        if self.estimator_ is None:
            return {}
        gains = self.estimator_.booster_.feature_importance(importance_type="gain")
        ranked = sorted(zip(self.features, gains, strict=True), key=lambda kv: -kv[1])
        return {name: round(float(gain), 1) for name, gain in ranked}


def candidate_models(target: str) -> list[Model]:
    """Fresh, unfitted baselines + model to compare for one target."""
    models: list[Model] = [Persistence(target), Climatology(target)]
    if is_classification(target):
        models.append(MajorityClass(target))
    models.append(LightGBMModel(target))
    return models


def make_model(target: str, name: str) -> Model:
    """A fresh, unfitted candidate model by name (e.g. ``"lightgbm"``)."""
    for model in candidate_models(target):
        if model.name == name:
            return model
    raise ValueError(f"no model {name!r} for {target!r}")
