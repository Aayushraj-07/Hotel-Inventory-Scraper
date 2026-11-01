"""Tests for the per-date fetch layer (spec §6, §14.1, §14.2).

Two levels:

    * URL building — pure, instant, asserts the OBSERVED query-parameter
      names (m0_findings §2) and preserved base-URL query.
    * offline end-to-end — a real headless browser opens the sanitized
      fixture from disk (``file://``): navigation -> wait for room rows
      -> HTML -> parser. No Booking.com access, no network (spec §14.2).
      Skips cleanly if the local browser cannot launch.
"""

from datetime import date
from pathlib import Path

import pytest

from scraper.availability_parser import parse_rooms
from scraper.booking_scraper import build_property_url, fetch_property_html
from scraper.browser import BrowserSession

FIXTURES = Path(__file__).parent / "fixtures"
ROOMTABLE = FIXTURES / "live_2026-11-01_roomtable.html"

BASE_URL = "https://www.booking.com/hotel/in/example-listing.en-gb.html"


# --- URL building ----------------------------------------------------------


def test_url_carries_observed_search_parameters() -> None:
    url = build_property_url(
        BASE_URL,
        checkin=date(2026, 11, 1),
        checkout=date(2026, 11, 2),
        adults=2,
        rooms=1,
        currency="INR",
    )
    assert "checkin=2026-11-01" in url
    assert "checkout=2026-11-02" in url
    assert "group_adults=2" in url
    assert "no_rooms=1" in url
    assert "group_children=0" in url
    assert "selected_currency=INR" in url
    assert url.startswith(BASE_URL)


def test_existing_base_query_is_preserved() -> None:
    url = build_property_url(
        BASE_URL + "?lang=en-gb",
        checkin=date(2026, 11, 1),
        checkout=date(2026, 11, 2),
        adults=2,
        rooms=1,
        currency="INR",
    )
    assert "lang=en-gb" in url


def test_checkout_must_be_after_checkin() -> None:
    with pytest.raises(ValueError, match="must be after"):
        build_property_url(
            BASE_URL,
            checkin=date(2026, 11, 2),
            checkout=date(2026, 11, 2),
            adults=2,
            rooms=1,
            currency="INR",
        )


# --- Offline end-to-end: browser -> fixture HTML -> parser -----------------


def test_fetch_local_fixture_end_to_end() -> None:
    """Real browser, local file, zero network: fetch then parse.

    Asserts the same four hand-parsed categories as test_parser — this
    proves the wait-for-rows + content() path preserves the data the
    parser relies on.
    """
    expected_names = [
        "Standard Double Room",
        "Deluxe Double Room",
        "Deluxe Quadruple Room",
        "6-Bed Mixed Dormitory Room",
    ]
    try:
        with BrowserSession(page_load_delay_seconds=0.0) as session:
            html = fetch_property_html(
                session, ROOMTABLE.resolve().as_uri(), rooms_wait_timeout_ms=5_000
            )
    except Exception as exc:  # noqa: BLE001 — environment without a browser
        pytest.skip(f"Local browser unavailable, skipping offline e2e: {exc}")

    rows = parse_rooms(html)
    assert [row["room_category"] for row in rows] == expected_names
