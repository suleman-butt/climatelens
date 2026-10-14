"""CLI entrypoints invoked by Cloud Run Jobs (and locally).

climatelens ingest                      # daily incremental
climatelens ingest --backfill-years 3   # initial historical load
climatelens ingest --start 2024-01-01 --end 2024-03-31
climatelens stations                    # verify the city -> station map (online)
climatelens features                    # rebuild the feature table from raw
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from climatelens import __version__
from climatelens.config import configure_logging, get_logger, get_settings
from climatelens.data import (
    backfill_range,
    default_incremental_range,
    ingest_range,
    read_raw,
    resolve_stations,
    write_features,
)
from climatelens.features import build_features, feature_report

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

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
