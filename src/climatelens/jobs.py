"""CLI entrypoints invoked by Cloud Run Jobs (and locally).

climatelens ingest                      # daily incremental
climatelens ingest --backfill-years 3   # initial historical load
climatelens ingest --start 2024-01-01 --end 2024-03-31
climatelens stations                    # verify the city -> station map (online)
climatelens features                    # rebuild the feature table from raw
climatelens evaluate                    # walk-forward models vs baselines (no artefacts)
climatelens train                       # evaluate, refit winners, write a model bundle
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime

from climatelens import __version__
from climatelens.config import configure_logging, get_logger, get_settings
from climatelens.data import (
    backfill_range,
    default_incremental_range,
    ingest_range,
    read_features,
    read_raw,
    resolve_stations,
    write_features,
    write_model_bundle,
)
from climatelens.evaluate import evaluate_all, train_final
from climatelens.features import FEATURE_COLUMNS, build_features, feature_report
from climatelens.models import LightGBMModel

log = get_logger("climatelens.jobs")


def _parse_day(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)


def _cmd_ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.backfill_years:
        start, end = backfill_range(args.backfill_years)
    elif args.start or args.end:
        if not (args.start and args.end):
            log.error("provide both --start and --end")
            return 2
        start, end = _parse_day(args.start), _parse_day(args.end)
    else:
        start, end = default_incremental_range(days_back=args.days_back)

    result = ingest_range(start, end, settings.raw_uri)
    print(json.dumps({"event": "ingest_result", **result.__dict__}, default=str, indent=2))
    if result.rows_written == 0:
        log.warning("ingest wrote zero rows")
        return 1
    return 0


def _cmd_stations(args: argparse.Namespace) -> int:
    frame = resolve_stations()
    print(frame.to_string(index=False))
    drift = frame.loc[~(frame["station_ok"] & frame["solar_ok"])]
    if len(drift):
        print(
            f"\n{len(drift)} of {len(frame)} cities have drifted from the nearest live "
            "station. Review the distances; update config.CITIES and log the change "
            "only if a move is warranted.",
            file=sys.stderr,
        )
        return 1
    print("\nEvery configured station is still the nearest active long-history DWD station.")
    return 0


def _cmd_features(args: argparse.Namespace) -> int:
    settings = get_settings()
    raw = read_raw(settings.raw_uri)
    if raw.empty:
        log.error("no raw data found; run 'climatelens ingest' first")
        return 1
    features = build_features(raw)
    uri = write_features(features, settings.features_uri)
    report = feature_report(features)
    print(json.dumps({"event": "features_result", "uri": uri, **report.as_dict()}, indent=2))
    return 0


def _load_features():
    features = read_features(get_settings().features_uri)
    if features.empty:
        log.error("no feature table found; run 'climatelens features' first")
    return features


def _evaluation_summary(evaluations) -> dict[str, object]:
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


def _cmd_evaluate(args: argparse.Namespace) -> int:
    features = _load_features()
    if features.empty:
        return 1
    evaluations = evaluate_all(features)
    if args.per_city:
        payload = {t: ev.as_dict() for t, ev in evaluations.items()}
    else:
        payload = _evaluation_summary(evaluations)
    print(json.dumps({"event": "evaluate_result", "targets": payload}, indent=2))
    return 0


def _library_versions() -> dict[str, str]:
    import lightgbm
    import pandas
    import sklearn

    return {
        "lightgbm": lightgbm.__version__,
        "pandas": pandas.__version__,
        "scikit-learn": sklearn.__version__,
    }


def _cmd_train(args: argparse.Namespace) -> int:
    settings = get_settings()
    features = _load_features()
    if features.empty:
        return 1
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
    uri = write_model_bundle(
        {target: t.model for target, t in trained.items()}, manifest, metrics, settings.models_uri
    )
    print(
        json.dumps(
            {
                "event": "train_result",
                "uri": uri,
                "version": manifest["version"],
                "models": manifest["models"],
                "evaluation": _evaluation_summary(evaluations),
            },
            indent=2,
            default=str,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="climatelens", description=__doc__)
    parser.add_argument("--version", action="version", version=f"climatelens {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="fetch, validate and store DWD observations")
    ingest.add_argument("--backfill-years", type=int, default=0, help="historical backfill window")
    ingest.add_argument("--start", help="inclusive start date YYYY-MM-DD")
    ingest.add_argument("--end", help="inclusive end date YYYY-MM-DD")
    ingest.add_argument("--days-back", type=int, default=2, help="incremental lookback (default 2)")
    ingest.set_defaults(func=_cmd_ingest)

    stations = sub.add_parser("stations", help="verify the city -> DWD station mapping (online)")
    stations.set_defaults(func=_cmd_stations)

    features = sub.add_parser("features", help="rebuild the feature table from raw Parquet")
    features.set_defaults(func=_cmd_features)

    evaluate = sub.add_parser("evaluate", help="walk-forward evaluation vs baselines")
    evaluate.add_argument("--per-city", action="store_true", help="include per-city metrics")
    evaluate.set_defaults(func=_cmd_evaluate)

    train = sub.add_parser("train", help="evaluate, refit the best model per target, save it")
    train.set_defaults(func=_cmd_train)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
