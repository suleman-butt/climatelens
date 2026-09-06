"""CLI entrypoints invoked by Cloud Run Jobs (and locally).

climatelens ingest                      # daily incremental
climatelens ingest --backfill-years 3   # initial historical load
climatelens ingest --start 2024-01-01 --end 2024-03-31
climatelens stations                    # verify the city -> station map (online)
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
    resolve_stations,
)

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
    with_pd = frame.to_string(index=False)
    print(with_pd)
    mismatches = frame.loc[~frame["matches_config"]]
    if len(mismatches):
        print(
            f"\n{len(mismatches)} of {len(frame)} cities do not match config.py "
            "- review distances and update CITIES if needed.",
            file=sys.stderr,
        )
        return 1
    print("\nAll configured stations match the nearest qualifying DWD station.")
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

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
