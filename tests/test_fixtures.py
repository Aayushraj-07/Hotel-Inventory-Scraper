"""Offline fixture-scenario tests (assignment §3.B, §8).

No network and no browser: fixtures are local HTML replayed through the same
classifier/parser as a live page. Challenge scenarios must STOP here, never
be parsed as inventory.
"""

from pathlib import Path

import pytest

from scraper.access_state import AccessState
from scraper.availability_parser import parse_rooms
from scraper.exceptions import BlockedError, StructureError, VerificationError
from services.fixture_service import (
    EVIDENCE_SCENARIO,
    EVIDENCE_PROPERTY_NAME,
    classify_fixture,
    fixture_fetch,
    load_fixture,
    load_manifest,
    scenario_names,
    verify_fixture_identity,
)

EXPECTED_SYNTHETIC = {
    "available",
    "sold_out",
    "no_rooms",
    "waf_challenge",
    "captcha",
    "bot_protection",
    "http_denied",
    "property_mismatch",
    "structure_changed",
}
BLOCKING_SCENARIOS = {
    "waf_challenge": AccessState.AWS_WAF_CHALLENGE,
    "captcha": AccessState.CAPTCHA_PRESENT,
    "bot_protection": AccessState.BOT_PROTECTION,
    "http_denied": AccessState.HTTP_ACCESS_DENIED,
}
URL = "https://www.booking.com/hotel/in/test.en-gb.html?checkin=2026-12-01"


# --- Registry / manifest -----------------------------------------------------


def test_manifest_defines_exactly_nine_synthetic_scenarios() -> None:
    manifest = load_manifest()
    assert set(manifest) == EXPECTED_SYNTHETIC
    assert len(manifest) == 9


def test_scenario_names_include_evidence_plus_nine_synthetic() -> None:
    names = scenario_names()
    assert names[0] == EVIDENCE_SCENARIO
    assert set(names) == {EVIDENCE_SCENARIO, *EXPECTED_SYNTHETIC}
    assert len(names) == 10


@pytest.mark.parametrize("name", scenario_names())
def test_every_registered_fixture_loads(name: str) -> None:
    fixture = load_fixture(name)
    assert fixture.path.is_file()
    assert "<html" in fixture.html.lower()
    assert fixture.name == name
    assert (fixture.name == EVIDENCE_SCENARIO) == (not fixture.is_synthetic)


def test_unknown_scenario_is_rejected_with_the_available_list() -> None:
    with pytest.raises(ValueError, match="Unknown fixture scenario") as exc:
        load_fixture("not_a_scenario")
    assert "available" in str(exc.value)


def test_manifest_expected_states_are_valid_access_states() -> None:
    for meta in load_manifest().values():
        if meta.get("expected_state"):
            assert AccessState(meta["expected_state"]) in AccessState


# --- Classification: fixtures cannot bypass the stop rule -------------------


@pytest.mark.parametrize("scenario, expected", BLOCKING_SCENARIOS.items())
def test_blocking_fixture_stops_with_the_expected_state(
    scenario: str, expected: AccessState
) -> None:
    fixture = load_fixture(scenario)
    with pytest.raises(BlockedError) as exc:
        classify_fixture(fixture, url=URL)
    assert exc.value.access_state is expected
    assert "no bypass" in str(exc.value)

    # The fetch path used by collect_inventory must stop too.
    with pytest.raises(BlockedError):
        fixture_fetch(fixture)(URL)


@pytest.mark.parametrize(
    "scenario", ["available", "sold_out", "no_rooms", "structure_changed"]
)
def test_clean_fixture_is_not_classified_as_a_block(scenario: str) -> None:
    fixture = load_fixture(scenario)
    assert classify_fixture(fixture, url=URL) is None
    assert fixture_fetch(fixture)(URL) == fixture.html


def test_manifest_state_annotations_match_runtime_classification() -> None:
    for name, meta in load_manifest().items():
        expected = meta.get("expected_state")
        if not expected or AccessState(expected) in (
            AccessState.PROPERTY_MISMATCH,
            AccessState.PAGE_STRUCTURE_CHANGED,
        ):
            continue  # identity/parser states are not classifier outputs
        fixture = load_fixture(name)
        with pytest.raises(BlockedError) as exc:
            classify_fixture(fixture, url=URL)
        assert exc.value.access_state is AccessState(expected), name


# --- Parsing behaviour of the useful scenarios ------------------------------


def test_available_fixture_parses_two_capped_rows() -> None:
    rows = parse_rooms(fixture_fetch(load_fixture("available"))(URL))
    assert [row["status"] for row in rows] == ["ok", "ok"]
    assert [row["rooms_available"] for row in rows] == [2, 3]
    assert [row["taxes_extra_inr"] for row in rows] == [52, 104]


def test_sold_out_fixture_parses_an_observed_zero() -> None:
    rows = parse_rooms(fixture_fetch(load_fixture("sold_out"))(URL))
    assert [row["status"] for row in rows] == ["sold_out", "ok"]
    assert rows[0]["rooms_available"] == 0


def test_no_rooms_fixture_has_no_room_rows() -> None:
    assert parse_rooms(fixture_fetch(load_fixture("no_rooms"))(URL)) == []


def test_structure_changed_fixture_raises_structure_error() -> None:
    with pytest.raises(StructureError):
        parse_rooms(fixture_fetch(load_fixture("structure_changed"))(URL))


# --- Identity verification ---------------------------------------------------


def test_evidence_fixture_matches_the_configured_property() -> None:
    verify_fixture_identity(
        load_fixture(EVIDENCE_SCENARIO), property_name=EVIDENCE_PROPERTY_NAME
    )


@pytest.mark.parametrize("scenario", EXPECTED_SYNTHETIC - {"waf_challenge", "captcha", "bot_protection", "http_denied", "property_mismatch"})
def test_honest_synthetic_fixtures_match_the_configured_property(scenario: str) -> None:
    verify_fixture_identity(load_fixture(scenario), property_name=EVIDENCE_PROPERTY_NAME)


def test_property_mismatch_fixture_is_refused() -> None:
    with pytest.raises(VerificationError) as exc:
        verify_fixture_identity(
            load_fixture("property_mismatch"), property_name=EVIDENCE_PROPERTY_NAME
        )
    assert exc.value.access_state is AccessState.PROPERTY_MISMATCH


def test_identity_check_is_case_insensitive() -> None:
    verify_fixture_identity(
        load_fixture(EVIDENCE_SCENARIO),
        property_name=EVIDENCE_PROPERTY_NAME.lower(),
    )


def test_fixture_paths_are_inside_the_repository() -> None:
    repo = Path(__file__).resolve().parent.parent
    for name in scenario_names():
        assert repo in load_fixture(name).path.resolve().parents
