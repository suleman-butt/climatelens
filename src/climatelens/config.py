"""Runtime configuration and the fixed city -> DWD station universe.

Selection rule (spec 4.1): the 12 most populous German cities that have a DWD
observation station reporting temperature, wind and radiation within 15 km of the
city centre. The mapping is frozen on the selection date below and is **never**
resolved dynamically at runtime - reproducibility beats currency.

Station selection date: 2026-09-06

The ``dwd_station_id`` values below are seeded from well-known DWD identifiers and
the coordinates are official city-centre points. Run ``climatelens stations``
once (needs outbound HTTPS to opendata.dwd.de) to verify each station still
reports CLIMATE_SUMMARY + SOLAR within 15 km and to print a ready-to-paste block.
Any change to this table must be recorded in docs/project-log.md.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

STATION_SELECTION_DATE = "2026-09-06"

#: Maximum allowed distance between a city centre and its DWD station.
MAX_STATION_DISTANCE_KM = 15.0


class Settings(BaseSettings):
    """Environment-driven settings. All keys are prefixed ``CLIMATELENS_``."""

    model_config = SettingsConfigDict(
        env_prefix="CLIMATELENS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "local"
    log_level: str = "INFO"

    gcp_project: str = ""
    gcp_region: str = "europe-west3"
    data_bucket: str = ""
    models_bucket: str = ""
    bigquery_dataset: str = "climatelens"

    #: Local filesystem root used for raw Parquet when no GCS bucket is set.
    data_dir: Path = Path("./data")
    #: Local filesystem root used for model artefacts when no GCS bucket is set.
    model_dir: Path = Path("./models")

    @property
    def raw_uri(self) -> str:
        """Base location for raw partitioned Parquet (GCS if configured)."""
        if self.data_bucket:
            return f"gs://{self.data_bucket}/raw"
        return str(self.data_dir / "raw")


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    return Settings()


@dataclass(frozen=True, slots=True)
class City:
    """A city in the fixed universe and its assigned DWD station."""

    name: str
    slug: str
    dwd_station_id: str
    station_name: str
    latitude: float
    longitude: float
    elevation_m: float


# 12 cities, ordered by population. station_name / dwd_station_id / elevation_m
# are seeded values pending an online check via ``climatelens stations``.
CITIES: tuple[City, ...] = (
    City("Berlin", "berlin", "00433", "Berlin-Tempelhof", 52.5200, 13.4050, 48.0),
    City("Hamburg", "hamburg", "01975", "Hamburg-Fuhlsbuettel", 53.5511, 9.9937, 11.0),
    City("Munich", "munich", "03379", "Muenchen-Stadt", 48.1372, 11.5755, 515.0),
    City("Cologne", "cologne", "02667", "Koeln-Bonn", 50.9375, 6.9603, 92.0),
    City("Frankfurt am Main", "frankfurt", "01420", "Frankfurt-Main", 50.1109, 8.6821, 100.0),
    City("Stuttgart", "stuttgart", "04928", "Stuttgart-Schnarrenberg", 48.7758, 9.1829, 314.0),
    City("Dortmund", "dortmund", "01303", "Dortmund", 51.5136, 7.4653, 125.0),
    City("Essen", "essen", "01303", "Essen-Bredeney", 51.4556, 7.0116, 150.0),
    City("Leipzig", "leipzig", "02928", "Leipzig-Holzhausen", 51.3397, 12.3731, 138.0),
    City("Dresden", "dresden", "01048", "Dresden-Klotzsche", 51.0504, 13.7373, 227.0),
    City("Hannover", "hannover", "02014", "Hannover", 52.3759, 9.7320, 55.0),
    City("Nuremberg", "nuremberg", "03668", "Nuernberg", 49.4521, 11.0767, 314.0),
)

CITIES_BY_SLUG: dict[str, City] = {c.slug: c for c in CITIES}
CITIES_BY_STATION: dict[str, City] = {c.dwd_station_id: c for c in CITIES}


def city_slugs() -> list[str]:
    return [c.slug for c in CITIES]


def station_ids() -> list[str]:
    return [c.dwd_station_id for c in CITIES]


class _JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON so Cloud Logging parses fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None) -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel((level or get_settings().log_level).upper())
    if any(getattr(h, "_climatelens", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    handler._climatelens = True  # type: ignore[attr-defined]
    root.handlers = [handler]


def get_logger(name: str) -> logging.LoggerAdapter:
    """Return a logger whose ``extra`` dict is merged into the JSON output."""

    class _Adapter(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            extra = kwargs.pop("extra", {})
            kwargs["extra"] = {"extra_fields": extra}
            return msg, kwargs

    return _Adapter(logging.getLogger(name), {})
