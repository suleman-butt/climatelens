"""Pipeline steps shared by the CLI (``jobs.py``) and the API job endpoints.

Each step reads and writes through ``data.py`` using the given settings and
returns a JSON-serialisable summary dict.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from climatelens import __version__
from climatelens.config import Settings, get_logger
from climatelens.data import (
    default_incremental_range,
    ingest_range,
    latest_raw_date,
    read_bundle_metrics,
    read_features,
    read_raw,
    write_features,
    write_model_bundle,
)
from climatelens.evaluate import TargetEvaluation, evaluate_all, train_final
from climatelens.features import FEATURE_COLUMNS, build_features, feature_report
from climatelens.models import LightGBMModel, is_classification

log = get_logger(__name__)


class PipelineError(RuntimeError):
    """A step cannot run because an earlier step has not produced its input."""


def refresh_snapshot(settings: Settings) -> dict[str, object]:
    """Precompute today's forecasts for the web service (after features/train)."""
    from climatelens.predict import ForecastService, ForecastUnavailable

    try:
        service = ForecastService.load(settings)
    except ForecastUnavailable as exc:
        log.info("forecast snapshot skipped", extra={"reason": str(exc)})
        return {"snapshot": "skipped", "reason": str(exc)}
    return {"snapshot": settings.snapshot_uri, "model_version": service.version}


def run_ingest(settings: Settings, start: datetime, end: datetime) -> dict[str, object]:
    result = ingest_range(start, end, settings.raw_uri)
    return {"event": "ingest_result", **result.__dict__}


#: The daily run never re-fetches more than this, even after a long outage.
MAX_CATCH_UP_DAYS = 60


def run_incremental_ingest(settings: Settings, days_back: int = 2) -> dict[str, object]:
    """Fetch the recent window, reaching back to the last stored day if needed.

    A missed daily run (DWD or deploy outage) is filled in by the next one
    instead of leaving a permanent gap, up to :data:`MAX_CATCH_UP_DAYS`.
    """
    start, end = default_incremental_range(days_back=days_back)
    last = latest_raw_date(settings.raw_uri)
    if last is not None:
        resume = datetime(last.year, last.month, last.day, tzinfo=UTC) + timedelta(days=1)
        start = max(min(start, resume), end - timedelta(days=MAX_CATCH_UP_DAYS))
    return run_ingest(settings, start, end)


def run_features(settings: Settings) -> dict[str, object]:
    raw = read_raw(settings.raw_uri)
    if raw.empty:
        raise PipelineError("no raw data found; run 'climatelens ingest' first")
    features = build_features(raw)
    uri = write_features(features, settings.features_uri)
    return {
        "event": "features_result",
        "uri": uri,
        **feature_report(features).as_dict(),
        **refresh_snapshot(settings),
    }


def _load_features(settings: Settings):
    features = read_features(settings.features_uri)
    if features.empty:
        raise PipelineError("no feature table found; run 'climatelens features' first")
    return features


def evaluation_summary(evaluations: dict[str, TargetEvaluation]) -> dict[str, object]:
    return {
        target: {
            "best_model": ev.best_model,
            "folds": ev.folds,
            "test_start": ev.test_start,
            "test_end": ev.test_end,
            "test_rows": ev.test_rows,
            "overall": ev.overall,
        }
        for target, ev in evaluations.items()
    }


def run_evaluate(settings: Settings, per_city: bool = False) -> dict[str, object]:
    evaluations = evaluate_all(_load_features(settings))
    if per_city:
        payload: dict[str, object] = {t: ev.as_dict() for t, ev in evaluations.items()}
    else:
        payload = evaluation_summary(evaluations)
    return {"event": "evaluate_result", "targets": payload}


def _library_versions() -> dict[str, str]:
    import lightgbm
    import pandas
    import sklearn

    return {
        "lightgbm": lightgbm.__version__,
        "pandas": pandas.__version__,
        "scikit-learn": sklearn.__version__,
    }


#: A new bundle is published only if no target's primary walk-forward score
#: (Brier for frost, MAE otherwise; lower is better) is more than this much
#: worse than the served bundle's.
GATE_TOLERANCE = 0.05


def _primary_score(target: str, evaluation: dict) -> float | None:
    key = "brier" if is_classification(target) else "mae"
    best = evaluation.get("best_model")
    return evaluation.get("overall", {}).get(best, {}).get(key) if best else None


def quality_gate(
    new_metrics: dict[str, dict], served_metrics: dict[str, dict] | None
) -> tuple[bool, list[dict[str, object]]]:
    """Compare a new bundle's walk-forward scores with the served bundle's.

    Targets the served bundle does not have (e.g. new horizons) pass.
    """
    checks: list[dict[str, object]] = []
    for target, evaluation in new_metrics.items():
        new = _primary_score(target, evaluation)
        served_eval = (served_metrics or {}).get(target)
        served = _primary_score(target, served_eval) if served_eval else None
        change = (new / served - 1.0) if new is not None and served else None
        ok = change is None or change <= GATE_TOLERANCE
        checks.append(
            {
                "target": target,
                "new": new,
                "served": served,
                "change_pct": None if change is None else round(100 * change, 1),
                "ok": ok,
            }
        )
    return all(c["ok"] for c in checks), checks


def run_train(settings: Settings, force: bool = False) -> dict[str, object]:
    """Evaluate, refit and write a new bundle; publish it if it passes the gate."""
    features = _load_features(settings)
    evaluations = evaluate_all(features)
    trained = train_final(features, evaluations)

    created = datetime.now(UTC)
    manifest: dict[str, object] = {
        "version": created.strftime("%Y%m%dT%H%M%SZ"),
        "created_at": created.isoformat(),
        "code_version": __version__,
        "git_sha": os.environ.get("CI_COMMIT_SHA", ""),
        "targets": list(trained),
        "feature_columns": list(FEATURE_COLUMNS),
        "libraries": _library_versions(),
        "models": {
            target: {
                "model": t.model.name,
                "train_rows": t.train_rows,
                "train_start": t.train_start,
                "train_end": t.train_end,
                **(
                    {
                        "params": t.model.params,
                        "top_features": dict(list(t.model.feature_importance().items())[:10]),
                    }
                    if isinstance(t.model, LightGBMModel)
                    else {}
                ),
            }
            for target, t in trained.items()
        },
    }
    metrics = {t: ev.as_dict() for t, ev in evaluations.items()}
    passed, checks = quality_gate(metrics, read_bundle_metrics(settings.models_uri))
    publish = passed or force
    manifest["quality_gate"] = {"passed": passed, "forced": force and not passed, "checks": checks}
    uri = write_model_bundle(
        {target: t.model for target, t in trained.items()},
        manifest,
        metrics,
        settings.models_uri,
        publish=publish,
    )
    if not publish:
        log.warning(
            "new bundle held back by the quality gate; the served model is unchanged",
            extra={"version": manifest["version"], "failed": [c for c in checks if not c["ok"]]},
        )
    return {
        "event": "train_result",
        "uri": uri,
        "version": manifest["version"],
        "published": publish,
        "quality_gate": manifest["quality_gate"],
        **(refresh_snapshot(settings) if publish else {"snapshot": "unchanged"}),
        "models": manifest["models"],
        "evaluation": evaluation_summary(evaluations),
    }
