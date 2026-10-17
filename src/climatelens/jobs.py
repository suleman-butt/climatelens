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
import sys
from datetime import UTC, datetime

from climatelens import __version__
from climatelens.config import configure_logging, get_logger, get_settings
from climatelens.data import backfill_range, default_incremental_range, resolve_stations
from climatelens.pipeline import (
    PipelineError,
    run_evaluate,
    run_features,
    run_ingest,
    run_train,
)

log = get_logger("climatelens.jobs")


def _parse_day(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _cmd_ingest(args: argparse.Namespace) -> int:
    if args.backfill_years:
        start, end = backfill_range(args.backfill_years)
    elif args.start or args.end:
        if not (args.start and args.end):
            log.error("provide both --start and --end")
            return 2
        start, end = _parse_day(args.start), _parse_day(args.end)
    else:
        start, end = default_incremental_range(days_back=args.days_back)

    result = run_ingest(get_settings(), start, end)
    _print(result)
    if result["rows_written"] == 0:
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
    _print(run_features(get_settings()))
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    _print(run_evaluate(get_settings(), per_city=args.per_city))
    return 0


def _cmd_train(args: argparse.Namespace) -> int:
    _print(run_train(get_settings(), force=args.force))
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
    train.add_argument(
        "--force", action="store_true", help="publish even if the quality gate fails"
    )
    train.set_defaults(func=_cmd_train)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PipelineError as exc:
        log.error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
