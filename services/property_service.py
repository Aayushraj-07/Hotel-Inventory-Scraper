"""Configuration loading, validation and Phase 1 property selection.

Responsibilities (spec §5, §12):
    * load the YAML configuration file (fail fast with clear messages)
    * validate global search settings (including mandatory assignment
      rules from spec §4: 2 adults, 1 room, one night, INR, >= 3 s
      between page loads, max 3 attempts per date)
    * validate property records (non-empty unique property IDs, boolean
      enabled flag, exactly one enabled property for Phase 1)
    * select the single enabled property for the run

Booking.com-specific behaviour does NOT live here — this module only
handles configuration data.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Phase 1 structural rule (spec §5.2): exactly ONE enabled property.
#
# DEVIATION (M0, 2026-10-31 — see docs/m0_findings.md): spec §5.2 expects
# that property to be "Hotel Shiv Vilas", but no such listing was found on
# Booking.com during manual verification. With developer approval the
# exact-ID guard is disabled until Shiv Vilas is located; the structural
# single-enabled-property rule remains enforced. This is a configuration
# check only — no hotel-specific extraction logic exists anywhere in Phase 1.

# Assignment minimums / limits (spec §4) — enforced as config validation.
MIN_PAGE_LOAD_DELAY_SECONDS = 3.0
MAX_ATTEMPTS_PER_DATE = 3


class ConfigError(Exception):
    """Raised when the configuration file is missing, malformed or invalid."""


@dataclass(frozen=True)
class Settings:
    """Validated global run settings (spec §5)."""

    adults: int
    rooms: int
    stay_nights: int
    currency: str
    days_to_scrape: int
    page_load_delay_seconds: float
    max_attempts_per_date: int
    timezone: str


@dataclass(frozen=True)
class Property:
    """A single property record from the configuration (spec §5)."""

    property_id: str
    property_name: str
    location: str
    booking_url: str | None
    booking_property_id: str | None
    enabled: bool


REQUIRED_SETTING_KEYS = (
    "adults",
    "rooms",
    "stay_nights",
    "currency",
    "days_to_scrape",
    "page_load_delay_seconds",
    "max_attempts_per_date",
    "timezone",
)

REQUIRED_PROPERTY_KEYS = (
    "property_id",
    "property_name",
    "location",
    "enabled",
)


def load_config(path: str | Path) -> dict:
    """Read and parse the YAML configuration file."""
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"Configuration file not found: {config_path}")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(
            f"Top-level content of {config_path} must be a mapping with "
            "'settings' and 'properties' sections."
        )
    return data


def _require_mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"'{label}' must be a mapping, got {type(value).__name__}.")
    return value


def _require_int(
    value: object,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    # bool is a subclass of int in Python — reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{label} must be an integer, got {value!r}.")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{label} must be >= {minimum}, got {value}.")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{label} must be <= {maximum}, got {value}.")
    return value


def _require_non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string, got {value!r}.")
    return value.strip()


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _require_non_empty_string(value, label)


def _validate_timezone(value: object) -> str:
    name = _require_non_empty_string(value, "settings.timezone")
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Unknown timezone: {name!r}.") from exc
    return name


def _validate_settings(raw: object) -> Settings:
    section = _require_mapping(raw, "settings")
    missing = [key for key in REQUIRED_SETTING_KEYS if key not in section]
    if missing:
        raise ConfigError(f"Missing required setting(s): {', '.join(missing)}.")

    # --- Mandatory assignment rules (spec §4) ---------------------------
    adults = _require_int(section["adults"], "settings.adults", minimum=1)
    if adults != 2:
        raise ConfigError(
            f"Phase 1 requires settings.adults == 2 (assignment rule); got {adults}."
        )
    rooms = _require_int(section["rooms"], "settings.rooms", minimum=1)
    if rooms != 1:
        raise ConfigError(
            f"Phase 1 requires settings.rooms == 1 (assignment rule); got {rooms}."
        )
    stay_nights = _require_int(section["stay_nights"], "settings.stay_nights", minimum=1)
    if stay_nights != 1:
        raise ConfigError(
            f"Phase 1 requires settings.stay_nights == 1 (assignment rule); "
            f"got {stay_nights}."
        )
    currency = _require_non_empty_string(section["currency"], "settings.currency").upper()
    if currency != "INR":
        raise ConfigError(
            f"Phase 1 requires settings.currency == INR (assignment rule); "
            f"got {currency}."
        )

    days_to_scrape = _require_int(
        section["days_to_scrape"], "settings.days_to_scrape", minimum=1, maximum=365
    )

    delay = section["page_load_delay_seconds"]
    if isinstance(delay, bool) or not isinstance(delay, (int, float)):
        raise ConfigError(
            f"settings.page_load_delay_seconds must be a number, got {delay!r}."
        )
    if float(delay) < MIN_PAGE_LOAD_DELAY_SECONDS:
        raise ConfigError(
            f"settings.page_load_delay_seconds must be >= "
            f"{MIN_PAGE_LOAD_DELAY_SECONDS:g} seconds (assignment minimum); "
            f"got {delay}."
        )

    max_attempts = _require_int(
        section["max_attempts_per_date"],
        "settings.max_attempts_per_date",
        minimum=1,
        maximum=MAX_ATTEMPTS_PER_DATE,
    )

    timezone_name = _validate_timezone(section["timezone"])

    return Settings(
        adults=adults,
        rooms=rooms,
        stay_nights=stay_nights,
        currency=currency,
        days_to_scrape=days_to_scrape,
        page_load_delay_seconds=float(delay),
        max_attempts_per_date=max_attempts,
        timezone=timezone_name,
    )


def _validate_property(raw: object, index: int, seen_ids: set[str]) -> Property:
    label = f"properties[{index}]"
    section = _require_mapping(raw, label)
    missing = [key for key in REQUIRED_PROPERTY_KEYS if key not in section]
    if missing:
        raise ConfigError(f"{label} missing required key(s): {', '.join(missing)}.")

    property_id = _require_non_empty_string(section["property_id"], f"{label}.property_id")
    if property_id in seen_ids:
        raise ConfigError(f"Duplicate property_id '{property_id}'.")
    seen_ids.add(property_id)

    property_name = _require_non_empty_string(
        section["property_name"], f"{label}.property_name"
    )
    location = _require_non_empty_string(section["location"], f"{label}.location")

    enabled = section["enabled"]
    if not isinstance(enabled, bool):
        raise ConfigError(f"{label}.enabled must be true or false, got {enabled!r}.")

    return Property(
        property_id=property_id,
        property_name=property_name,
        location=location,
        booking_url=_optional_string(section.get("booking_url"), f"{label}.booking_url"),
        booking_property_id=_optional_string(
            section.get("booking_property_id"), f"{label}.booking_property_id"
        ),
        enabled=enabled,
    )


def validate_config(config: dict) -> tuple[Settings, list[Property]]:
    """Validate the whole configuration; return typed settings + properties."""
    if not isinstance(config, dict):
        raise ConfigError("Configuration must be a mapping.")

    for section_name in ("settings", "properties"):
        if section_name not in config:
            raise ConfigError(f"Missing required top-level section '{section_name}'.")

    settings = _validate_settings(config["settings"])

    properties_raw = config["properties"]
    if not isinstance(properties_raw, list) or not properties_raw:
        raise ConfigError("'properties' must be a non-empty list of property records.")

    properties: list[Property] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(properties_raw):
        properties.append(_validate_property(raw, index, seen_ids))

    # --- Phase 1 selection rules (spec §5.2) ----------------------------
    enabled = [p for p in properties if p.enabled]
    if not enabled:
        raise ConfigError(
            "No enabled property found; Phase 1 requires exactly one enabled "
            "property (Hotel Shiv Vilas)."
        )
    if len(enabled) > 1:
        ids = ", ".join(p.property_id for p in enabled)
        raise ConfigError(
            f"Phase 1 allows exactly one enabled property, found "
            f"{len(enabled)}: {ids}."
        )
    # Exact-ID guard removed per recorded M0 deviation (docs/m0_findings.md):
    # the assignment target "Hotel Shiv Vilas" was not found on Booking.com;
    # any single verified record may be the enabled Phase 1 property.

    return settings, properties


def select_enabled_property(properties: list[Property]) -> Property:
    """Return the single enabled property (Phase 1: Hotel Shiv Vilas)."""
    enabled = [p for p in properties if p.enabled]
    if len(enabled) != 1:
        raise ConfigError(
            f"Expected exactly one enabled property, found {len(enabled)}."
        )
    return enabled[0]


def require_listing_reference(property_record: Property) -> Property:
    """Fail unless a verified Booking.com listing reference exists (spec §5.2).

    Used as an automated startup check from milestone M4 onwards —
    values must come from manual verification (M0), never from guessing.
    """
    if not property_record.booking_url and not property_record.booking_property_id:
        raise ConfigError(
            f"Property '{property_record.property_id}' has no verified listing "
            "reference: set booking_url and/or booking_property_id in the "
            "config from the real public listing (M0) — do not guess IDs."
        )
    return property_record
