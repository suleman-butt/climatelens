"""Runtime configuration and the fixed city -> DWD station universe.

Selection rule (spec 4.1, as implemented): the 12 most populous German cities,
each mapped to two stations, resolved once on the selection date and then frozen
(never resolved dynamically at runtime - reproducibility beats currency):

* ``station_id``       - nearest active, long-history CLIMATE_SUMMARY station
  that actually reports wind + pressure (many only record temperature and
  precipitation). Temperature, wind, humidity, pressure, sunshine, cloud. Within
  15 km for every city except Cologne (16.0 km - nearest is Koeln-Bonn airport).
  Frankfurt uses Offenbach-Wetterpark (7.8 km). Dortmund has no qualifying
  station within 30 km and was replaced by Bremen (2026-09-06).
* ``solar_station_id`` - nearest active SOLAR station (global radiation only).
  Only 56 stations nationwide report daily radiation, so for Berlin, Munich,
  Cologne, Essen and Hannover this station is 17-60 km away. This is a documented
  data-availability limitation (see docs/decisions.md), analogous to the pollen
  proxy.

Station selection date: 2026-09-06 (resolved via ``climatelens stations``).
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
    """A city in the fixed universe and its two assigned DWD stations."""

    name: str
    slug: str
    centre_latitude: float
    centre_longitude: float
    # Primary station: temperature, wind, humidity, pressure, sunshine, cloud.
    station_id: str
    station_name: str
    station_latitude: float
    station_longitude: float
    station_elevation_m: float
    station_distance_km: float
    # Solar station: daily global radiation only (sparse network, may be far).
    solar_station_id: str
    solar_station_name: str
    solar_distance_km: float


# 12 cities, ordered by population. Resolved via ``climatelens stations`` on the
# selection date; do not hand-edit without re-running it and logging the change.
# Fields: name, slug, centre lat/lon, station id/name/lat/lon/elevation_m/dist_km,
#         solar station id/name/dist_km.
# fmt: off
CITIES: tuple[City, ...] = (
    City("Berlin", "berlin", 52.5200, 13.4050, "00433", "Berlin-Tempelhof", 52.4676, 13.4020, 48.0, 5.83, "03987", "Potsdam", 27.89),  # noqa: E501
    City("Hamburg", "hamburg", 53.5511, 9.9937, "01975", "Hamburg-Fuhlsbuettel", 53.6332, 9.9881, 11.0, 9.14, "01975", "Hamburg-Fuhlsbuettel", 9.14),  # noqa: E501
    City("Munich", "munich", 48.1372, 11.5755, "03379", "Muenchen-Stadt", 48.1632, 11.5429, 515.0, 3.77, "05404", "Weihenstephan-Duernast", 30.78),  # noqa: E501
    City("Cologne", "cologne", 50.9375, 6.9603, "02667", "Koeln-Bonn", 50.8645, 7.1575, 91.0, 16.04, "07365", "Bochum Ruhruniversitaet", 60.32),  # noqa: E501
    City("Frankfurt am Main", "frankfurt", 50.1109, 8.6821, "07341", "Offenbach-Wetterpark", 50.0900, 8.7862, 119.0, 7.78, "01420", "Frankfurt-Main", 14.87),  # noqa: E501
    City("Stuttgart", "stuttgart", 48.7758, 9.1829, "04928", "Stuttgart Schnarrenberg", 48.8281, 9.2000, 314.0, 5.95, "04928", "Stuttgart Schnarrenberg", 5.95),  # noqa: E501
    City("Essen", "essen", 51.4556, 7.0116, "01303", "Essen-Bredeney", 51.4041, 6.9677, 150.0, 6.49, "07365", "Bochum Ruhruniversitaet", 17.44),  # noqa: E501
    City("Leipzig", "leipzig", 51.3397, 12.3731, "02928", "Leipzig-Holzhausen", 51.3151, 12.4462, 138.0, 5.77, "02932", "Leipzig-Halle", 14.05),  # noqa: E501
    City("Bremen", "bremen", 53.0793, 8.8017, "00691", "Bremen", 53.0451, 8.7981, 4.0, 3.80, "00691", "Bremen", 3.80),  # noqa: E501
    City("Dresden", "dresden", 51.0504, 13.7373, "01048", "Dresden-Klotzsche", 51.1278, 13.7543, 228.0, 8.69, "01048", "Dresden-Klotzsche", 8.69),  # noqa: E501
    City("Hannover", "hannover", 52.3759, 9.7320, "02014", "Hannover", 52.4644, 9.6779, 55.0, 10.50, "00662", "Braunschweig", 49.44),  # noqa: E501
    City("Nuremberg", "nuremberg", 49.4521, 11.0767, "03668", "Nuernberg", 49.5030, 11.0549, 314.0, 5.87, "03668", "Nuernberg", 5.87),  # noqa: E501
)
# fmt: on

CITIES_BY_SLUG: dict[str, City] = {c.slug: c for c in CITIES}
#: Primary station id -> city slug (one-to-one).
CLIMATE_STATION_TO_SLUG: dict[str, str] = {c.station_id: c.slug for c in CITIES}
#: (solar station id, city slug) pairs - one solar station can feed several cities.
SOLAR_STATION_CITY_PAIRS: tuple[tuple[str, str], ...] = tuple(
    (c.solar_station_id, c.slug) for c in CITIES
)


def city_slugs() -> list[str]:
    return [c.slug for c in CITIES]


def climate_station_ids() -> list[str]:
    return sorted({c.station_id for c in CITIES})


def solar_station_ids() -> list[str]:
    return sorted({c.solar_station_id for c in CITIES})


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
