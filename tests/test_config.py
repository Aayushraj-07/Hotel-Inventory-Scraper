"""Unit tests for configuration loading, validation and property selection
(spec §14.1 — configuration and property-selection items).

All tests run offline; no Booking.com access required.

Note: the enabled property in the shipped config is the M0 substitute
listing (see docs/m0_findings.md) — the assignment target "Hotel Shiv
Vilas" was not found on Booking.com during manual verification.
"""

from pathlib import Path

import pytest
import yaml

from services.property_service import (
    ConfigError,
    load_config,
    require_listing_reference,
    select_enabled_property,
    validate_config,
)

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config" / "config.yaml"

# The enabled property in config/config.yaml (M0 substitute listing).
ENABLED_PROPERTY_ID = "heaven_and_harmony_tapovan"


def valid_config() -> dict:
    """A fresh, valid Phase 1 configuration (mirrors config/config.yaml)."""
    return {
        "settings": {
            "adults": 2,
            "rooms": 1,
            "stay_nights": 1,
            "currency": "INR",
            "days_to_scrape": 30,
            "page_load_delay_seconds": 3,
            "max_attempts_per_date": 3,
            "timezone": "Asia/Kolkata",
        },
        "properties": [
            {
                "property_id": ENABLED_PROPERTY_ID,
                "property_name": "Around Stays-Heaven And Harmony",
                "location": "Tapovan, Rishikesh, India",
                "booking_url": (
                    "https://www.booking.com/hotel/in/"
                    "heaven-and-harmony-tapovan.en-gb.html"
                ),
                "booking_property_id": "17328907",
                "enabled": True,
            },
            {
                # Deferred assignment target (spec §3.1) — not found in M0.
                "property_id": "shiv_vilas",
                "property_name": "Hotel Shiv Vilas",
                "location": "Tapovan, Rishikesh, India",
                "booking_url": None,
                "booking_property_id": None,
                "enabled": False,
            },
        ],
    }


def write_yaml(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


# --- Loading -----------------------------------------------------------------


def test_load_config_reads_yaml_file(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, valid_config())
    assert load_config(path) == valid_config()


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "does_not_exist.yaml")


def test_load_config_invalid_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("settings: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_config(path)


def test_repo_config_file_is_valid() -> None:
    """The shipped config/config.yaml must always validate (spec §17.1/17.2)."""
    settings, properties = validate_config(load_config(REPO_CONFIG))
    assert settings.days_to_scrape == 30
    assert settings.timezone == "Asia/Kolkata"
    selected = select_enabled_property(properties)
    assert selected.property_id == ENABLED_PROPERTY_ID
    # M0 substitute listing carries its verified reference (docs/m0_findings.md).
    assert selected.booking_url is not None
    assert selected.booking_property_id == "17328907"
    # The deferred assignment target remains present but disabled.
    deferred = next(p for p in properties if p.property_id == "shiv_vilas")
    assert deferred.enabled is False


# --- Settings validation ------------------------------------------------------


def test_valid_config_returns_typed_settings_and_properties() -> None:
    settings, properties = validate_config(valid_config())
    assert settings.adults == 2
    assert settings.rooms == 1
    assert settings.stay_nights == 1
    assert settings.currency == "INR"
    assert settings.days_to_scrape == 30
    assert settings.page_load_delay_seconds == 3.0
    assert settings.max_attempts_per_date == 3
    assert len(properties) == 2


def test_missing_settings_section_raises() -> None:
    config = valid_config()
    del config["settings"]
    with pytest.raises(ConfigError, match="settings"):
        validate_config(config)


def test_missing_settings_key_raises() -> None:
    config = valid_config()
    del config["settings"]["days_to_scrape"]
    with pytest.raises(ConfigError, match="Missing required setting"):
        validate_config(config)


@pytest.mark.parametrize(("key", "value"), [("adults", 3), ("rooms", 2), ("stay_nights", 2)])
def test_assignment_search_parameters_are_enforced(key: str, value: int) -> None:
    """Spec §4: 2 adults, 1 room, one night, INR — mandatory in Phase 1."""
    config = valid_config()
    config["settings"][key] = value
    with pytest.raises(ConfigError, match="assignment rule"):
        validate_config(config)


def test_non_inr_currency_rejected() -> None:
    config = valid_config()
    config["settings"]["currency"] = "USD"
    with pytest.raises(ConfigError, match="INR"):
        validate_config(config)


def test_page_load_delay_below_three_seconds_rejected() -> None:
    """Spec §4: at least 3 seconds between page loads."""
    config = valid_config()
    config["settings"]["page_load_delay_seconds"] = 2
    with pytest.raises(ConfigError, match="3 seconds"):
        validate_config(config)


@pytest.mark.parametrize("attempts", [0, 4])
def test_attempts_outside_1_to_3_rejected(attempts: int) -> None:
    """Decision 3: 3 total attempts per date is the hard maximum."""
    config = valid_config()
    config["settings"]["max_attempts_per_date"] = attempts
    with pytest.raises(ConfigError, match="max_attempts_per_date"):
        validate_config(config)


def test_invalid_timezone_rejected() -> None:
    config = valid_config()
    config["settings"]["timezone"] = "Mars/Olympus_Mons"
    with pytest.raises(ConfigError, match="[Uu]nknown timezone"):
        validate_config(config)


def test_zero_days_to_scrape_rejected() -> None:
    config = valid_config()
    config["settings"]["days_to_scrape"] = 0
    with pytest.raises(ConfigError, match="days_to_scrape"):
        validate_config(config)


def test_boolean_rejected_where_integer_required() -> None:
    """bool is a subclass of int in Python — guard against YAML true/false."""
    config = valid_config()
    config["settings"]["days_to_scrape"] = True
    with pytest.raises(ConfigError, match="integer"):
        validate_config(config)


# --- Property validation + selection -----------------------------------------


def test_duplicate_property_ids_rejected() -> None:
    config = valid_config()
    config["properties"][1]["property_id"] = ENABLED_PROPERTY_ID
    with pytest.raises(ConfigError, match="Duplicate property_id"):
        validate_config(config)


def test_no_enabled_property_rejected() -> None:
    config = valid_config()
    config["properties"][0]["enabled"] = False
    with pytest.raises(ConfigError, match="No enabled property"):
        validate_config(config)


def test_two_enabled_properties_rejected() -> None:
    """Spec §5.2 structure: Phase 1 runs exactly one enabled property."""
    config = valid_config()
    config["properties"][1]["enabled"] = True
    with pytest.raises(ConfigError, match="exactly one enabled property"):
        validate_config(config)


def test_single_enabled_property_may_be_any_verified_record() -> None:
    """M0 deviation (docs/m0_findings.md): the assignment target was not
    found, so the Phase 1 guard checks *structure* (exactly one enabled),
    not a specific property ID. Any verified record may be the enabled one.
    """
    config = valid_config()
    config["properties"][0]["enabled"] = False  # defer the substitute
    config["properties"][1]["enabled"] = True   # a later-found property
    _, properties = validate_config(config)
    assert select_enabled_property(properties).property_id == "shiv_vilas"


def test_disabled_deferred_property_does_not_change_selection() -> None:
    """Spec §14.1: a disabled property must not affect Phase 1 execution."""
    _, properties = validate_config(valid_config())
    selected = select_enabled_property(properties)
    assert selected.property_id == ENABLED_PROPERTY_ID
    assert selected.property_name == "Around Stays-Heaven And Harmony"
    assert selected.location == "Tapovan, Rishikesh, India"
    # The original assignment target is still present, just disabled.
    assert any(
        p.property_id == "shiv_vilas" and not p.enabled for p in properties
    )


def test_missing_property_key_raises() -> None:
    config = valid_config()
    del config["properties"][0]["location"]
    with pytest.raises(ConfigError, match="missing required key"):
        validate_config(config)


def test_non_boolean_enabled_rejected() -> None:
    config = valid_config()
    config["properties"][0]["enabled"] = "true"
    with pytest.raises(ConfigError, match="enabled"):
        validate_config(config)


def test_empty_property_id_rejected() -> None:
    config = valid_config()
    config["properties"][0]["property_id"] = "   "
    with pytest.raises(ConfigError, match="property_id"):
        validate_config(config)


def test_empty_properties_list_rejected() -> None:
    config = valid_config()
    config["properties"] = []
    with pytest.raises(ConfigError, match="non-empty list"):
        validate_config(config)


# --- Listing reference (M4 startup check) -------------------------------------


def test_require_listing_reference_raises_when_missing() -> None:
    _, properties = validate_config(valid_config())
    deferred = next(p for p in properties if p.property_id == "shiv_vilas")
    with pytest.raises(ConfigError, match="no verified listing reference"):
        require_listing_reference(deferred)


def test_require_listing_reference_passes_for_enabled_property() -> None:
    _, properties = validate_config(valid_config())
    record = select_enabled_property(properties)
    assert require_listing_reference(record) is record
