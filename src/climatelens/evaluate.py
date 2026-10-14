"""Expanding walk-forward evaluation of the models against the baselines.

Pure DataFrame code (no I/O). Weather is time-dependent, so rows are never
shuffled: each fold trains on every row strictly before a calendar-month
boundary and tests on the month that follows. The training window grows by one
month per fold (expanding window); the first fold needs at least
``min_train_days`` of history so climatology has seen every season once.

Training row ``t`` carries the target observed on ``t + 1``, so the last
training row's target is the first test day's *observation* - known at
forecast time - never a test target.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from climatelens.config import get_logger
from climatelens.features import TARGETS, target_frame
from climatelens.models import Model, candidate_models, is_classification, make_model

log = get_logger(__name__)

MIN_TRAIN_DAYS = 365
FROST_DECISION_THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class Fold:
    index: int
    test_start: pd.Timestamp  # inclusive; also the exclusive end of training
    test_end: pd.Timestamp  # exclusive


def walk_forward_folds(dates: pd.Series, min_train_days: int = MIN_TRAIN_DAYS) -> list[Fold]:
    """Monthly expanding-window folds over the given row dates."""
    dates = pd.to_datetime(dates)
    if dates.empty:
        return []
    first, last = dates.min().normalize(), dates.max().normalize()
    earliest_test = first + pd.Timedelta(min_train_days, unit="D")
    # Month starts from the first boundary with enough history, through the last row.
    starts = pd.date_range(earliest_test, last, freq="MS")
    folds = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else last + pd.Timedelta(1, unit="D")
        folds.append(Fold(index=i, test_start=start, test_end=end))
    return folds


# --- Metrics ------------------------------------------------------------------


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    err = np.asarray(y_pred, dtype="float64") - np.asarray(y_true, dtype="float64")
    return {
        "n": int(len(err)),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err**2))),
    }


def classification_metrics(y_true: np.ndarray, p_pred: np.ndarray) -> dict[str, float]:
    y = np.asarray(y_true, dtype="float64")
    p = np.clip(np.asarray(p_pred, dtype="float64"), 0.0, 1.0)
    return {
        "n": int(len(y)),
        "accuracy": float(np.mean((p >= FROST_DECISION_THRESHOLD) == (y == 1.0))),
        "brier": float(np.mean((p - y) ** 2)),
        "positive_rate": float(np.mean(y)),
    }


def _metrics(target: str, y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    if is_classification(target):
        return classification_metrics(y_true, y_pred)
    return regression_metrics(y_true, y_pred)


def _add_skill(target: str, by_model: dict[str, dict[str, float]]) -> None:
    """Skill relative to a reference baseline: 1 - score / reference (higher is better)."""
    if is_classification(target):
        metric, reference = "brier", "climatology"
    else:
        metric, reference = "mae", "persistence"
    ref = by_model.get(reference, {}).get(metric)
    for scores in by_model.values():
        if ref:
            scores[f"{metric}_skill_vs_{reference}"] = float(1.0 - scores[metric] / ref)


def _round(scores: dict[str, float]) -> dict[str, float]:
    return {k: (v if k == "n" else round(v, 4)) for k, v in scores.items()}


# --- Walk-forward -------------------------------------------------------------


@dataclass
class TargetEvaluation:
    target: str
    folds: int = 0
    test_start: str = ""
    test_end: str = ""
    test_rows: int = 0
    overall: dict[str, dict[str, float]] = field(default_factory=dict)
    per_city: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    best_model: str = ""

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


def walk_forward_predictions(
    features: pd.DataFrame,
    target: str,
    model_factory: Callable[[str], list[Model]] = candidate_models,
    min_train_days: int = MIN_TRAIN_DAYS,
) -> pd.DataFrame:
    """Out-of-sample predictions for every fold and model, in long format.

    ``model_factory`` returns fresh, unfitted models; it is called once per fold
    so no state leaks between folds.
    """
    rows = target_frame(features, target)
    folds = walk_forward_folds(rows["date"], min_train_days=min_train_days)
    records = []
    for fold in folds:
        train = rows.loc[rows["date"] < fold.test_start]
        test = rows.loc[(rows["date"] >= fold.test_start) & (rows["date"] < fold.test_end)]
        if train.empty or test.empty:
            continue
        for model in model_factory(target):
            model.fit(train)
            pred = model.predict(test)
            records.append(
                pd.DataFrame(
                    {
                        "fold": fold.index,
                        "city": test["city"].to_numpy(),
                        "date": test["date"].to_numpy(),
                        "model": model.name,
                        "y_true": test[target].to_numpy(dtype="float64"),
                        "y_pred": pred,
                    }
                )
            )
        log.info(
            "fold done",
            extra={
                "target": target,
                "fold": fold.index,
                "test_start": fold.test_start.strftime("%Y-%m-%d"),
                "train_rows": len(train),
                "test_rows": len(test),
            },
        )
    if not records:
        return pd.DataFrame(columns=["fold", "city", "date", "model", "y_true", "y_pred"])
    return pd.concat(records, ignore_index=True)


def summarise(predictions: pd.DataFrame, target: str) -> TargetEvaluation:
    """Overall and per-city metrics per model from walk-forward predictions."""
    result = TargetEvaluation(target=target)
    if predictions.empty:
        return result
    result.folds = int(predictions["fold"].nunique())
    result.test_start = pd.Timestamp(predictions["date"].min()).strftime("%Y-%m-%d")
    result.test_end = pd.Timestamp(predictions["date"].max()).strftime("%Y-%m-%d")
    first_model = predictions["model"].iloc[0]
    result.test_rows = int((predictions["model"] == first_model).sum())

    for name, group in predictions.groupby("model", sort=False):
        result.overall[str(name)] = _metrics(target, group["y_true"], group["y_pred"])
    _add_skill(target, result.overall)

    for city, city_group in predictions.groupby("city"):
        by_model = {
            str(name): _metrics(target, g["y_true"], g["y_pred"])
            for name, g in city_group.groupby("model", sort=False)
        }
        _add_skill(target, by_model)
        result.per_city[str(city)] = {m: _round(s) for m, s in by_model.items()}

    key = "brier" if is_classification(target) else "mae"
    result.best_model = min(result.overall, key=lambda m: result.overall[m][key])
    result.overall = {m: _round(s) for m, s in result.overall.items()}
    return result


def evaluate_all(
    features: pd.DataFrame,
    targets: tuple[str, ...] = TARGETS,
    min_train_days: int = MIN_TRAIN_DAYS,
) -> dict[str, TargetEvaluation]:
    """Walk-forward evaluation of every candidate model for each target."""
    results = {}
    for target in targets:
        preds = walk_forward_predictions(features, target, min_train_days=min_train_days)
        results[target] = summarise(preds, target)
        log.info(
            "target evaluated",
            extra={"target": target, "best_model": results[target].best_model},
        )
    return results


# --- Final training -------------------------------------------------------------


@dataclass
class TrainedTarget:
    target: str
    model: Model
    train_rows: int
    train_start: str
    train_end: str


def train_final(
    features: pd.DataFrame, evaluations: dict[str, TargetEvaluation]
) -> dict[str, TrainedTarget]:
    """Refit each target's walk-forward winner on every row with a known target."""
    trained = {}
    for target, evaluation in evaluations.items():
        if not evaluation.best_model:
            raise ValueError(f"no evaluation result for {target!r}; cannot pick a model")
        rows = target_frame(features, target)
        model = make_model(target, evaluation.best_model).fit(rows)
        trained[target] = TrainedTarget(
            target=target,
            model=model,
            train_rows=len(rows),
            train_start=rows["date"].min().strftime("%Y-%m-%d"),
            train_end=rows["date"].max().strftime("%Y-%m-%d"),
        )
        log.info(
            "final model trained",
            extra={"target": target, "model": model.name, "train_rows": len(rows)},
        )
    return trained
