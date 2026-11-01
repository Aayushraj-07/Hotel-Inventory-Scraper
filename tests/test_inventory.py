"""Unit tests for per-date collection: retries, statuses, block-stop,
row mapping and date isolation (spec §6, §8.4, §8.7, §10, §14.1).

All offline (spec §14.2): the browser is an injected ``fetch_html``
stub; the real fixture supplies the success-path HTML. No Booking.com
access, no network, no real waiting.
"""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services.inventory_service import collect_inventory
from services.property_service import Property, Settings
from services.validation_service import validate_rows
from scraper.exceptions import BlockedError, NavigationError, StructureError

FIXTURES = Path(__file__).parent / "fixtures"
ROOMTABLE_HTML = (FIXTURES / "live_2026-11-01_roomtable.html").read_text(
    encoding="utf-8"
)

# Loaded page chrome WITHOUT any room rows -> parser: property page, zero
# categories (observed markers, m0_findings §8.5).
PROPERTY_PAGE_NO_ROOMS_HTML = (
    "<html><body>"
    '<div data-testid="searchbox-dates-container"></div>'
    '<div data-testid="breadcrumb-item"></div>'
    "</body></html>"
)

# Neither room rows nor property markers -> content never rendered.
GARBAGE_HTML = "<html><body>maintenance page</body></html>"

# Room row exists but has no readable name -> StructureError (M5 contract).
NAMELESS_ROW_HTML = (
    "<html><body><table>"
    '<tr class="e2e-hprt-table-row"><td>unnamed</td></tr>'
    "</table></body></html>"
)

FIXED_NOW = datetime(2026, 11, 1, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
DATES = [date(2026, 11, 2), date(2026, 11, 3)]
EXPECTED_CATEGORIES = [
    "Standard Double Room",
    "Deluxe Double Room",
    "Deluxe Quadruple Room",
    "6-Bed Mixed Dormitory Room",
]


def make_settings(**overrides) -> Settings:
    values = dict(
        adults=2,
        rooms=1,
        stay_nights=1,
        currency="INR",
        days_to_scrape=len(DATES),
        page_load_delay_seconds=3.0,
        max_attempts_per_date=3,
        timezone="Asia/Kolkata",
    )
    values.update(overrides)
    return Settings(**values)


PROPERTY = Property(
    property_id="test_hotel",
    property_name="Test Hotel",
    location="Testville, India",
    booking_url="https://www.booking.com/hotel/in/test-listing.en-gb.html",
    booking_property_id="1",
    enabled=True,
)


class RecordingFetch:
    """Stub browser: serves the scripted HTML/errors, records every call.

    Strict: every date must be scripted explicitly (pop, no fallback) —
    a surprise fallback would hide bugs like serving the wrong page.
    """

    def __init__(self, script) -> None:
        self._script = list(script)
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if not self._script:
            raise AssertionError(f"Unexpected extra fetch: {url}")
        action = self._script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def collect(fetch, *, dates=DATES, settings=None):
    return collect_inventory(
        property_record=PROPERTY,
        settings=settings or make_settings(),
        checkin_dates=dates,
        fetch_html=fetch,
        now=lambda: FIXED_NOW,
    )


# --- Success path ----------------------------------------------------------


def test_success_path_maps_fixture_observations_to_rows() -> None:
    fetch = RecordingFetch([ROOMTABLE_HTML, ROOMTABLE_HTML])
    result = collect(fetch)

    assert result.blocked is False
    assert (result.dates_ok, result.dates_no_rooms, result.dates_failed) == (2, 0, 0)
    assert len(result.rows) == 2 * 4  # 4 categories per date

    for offset, day in enumerate(DATES):
        rows = result.rows[offset * 4:(offset + 1) * 4]
        assert [r["room_category"] for r in rows] == EXPECTED_CATEGORIES
        assert [r["rooms_available"] for r in rows] == [2, 7, 2, 3]
        assert [r["min_price_inr"] for r in rows] == [1040, 1144, 2080, 2184]
        for row in rows:
            assert row["checkin_date"] == day.isoformat()
            assert row["checkout_date"] == date.fromordinal(
                day.toordinal() + 1
            ).isoformat()
            assert row["status"] == "ok"
            assert row["availability_source"] == "room_selector"
            assert row["is_capped"] is True
            assert row["error_detail"] is None
            assert row["scrape_timestamp"] == FIXED_NOW.isoformat()


def test_success_rows_pass_the_full_validation_contract() -> None:
    result = collect(RecordingFetch([ROOMTABLE_HTML, ROOMTABLE_HTML]))
    validate_rows(result.rows, DATES, stay_nights=1)  # raises on violation


def test_each_date_builds_its_own_search_url() -> None:
    fetch = RecordingFetch([ROOMTABLE_HTML, ROOMTABLE_HTML])
    collect(fetch)
    assert len(fetch.calls) == 2
    assert "checkin=2026-11-02" in fetch.calls[0]
    assert "checkout=2026-11-03" in fetch.calls[0]
    assert "checkin=2026-11-03" in fetch.calls[1]
    for url in fetch.calls:
        assert "group_adults=2" in url
        assert "no_rooms=1" in url
        assert "group_children=0" in url
        assert "selected_currency=INR" in url


# --- no_rooms_listed (spec §8.4) -------------------------------------------


def test_loaded_page_without_rooms_becomes_date_level_record() -> None:
    fetch = RecordingFetch([PROPERTY_PAGE_NO_ROOMS_HTML, PROPERTY_PAGE_NO_ROOMS_HTML])
    result = collect(fetch)

    assert (result.dates_ok, result.dates_no_rooms, result.dates_failed) == (0, 2, 0)
    assert len(result.rows) == 2  # one date-level record per date
    for row in result.rows:
        assert row["status"] == "no_rooms_listed"
        assert row["availability_source"] == "no_rooms_listed"
        assert row["room_category"] == ""
        assert row["rooms_available"] is None  # unknown, not zero
    validate_rows(result.rows, DATES, stay_nights=1)


# --- Retry policy (spec §10) -----------------------------------------------


def test_transient_failures_are_retried_then_succeed() -> None:
    fetch = RecordingFetch(
        [NavigationError("boom 1"), NavigationError("boom 2"), ROOMTABLE_HTML]
    )
    result = collect(fetch, dates=[date(2026, 11, 2)])

    assert len(fetch.calls) == 3  # 2 failures + 1 success
    assert result.dates_ok == 1
    assert len(result.rows) == 4
    validate_rows(result.rows, [date(2026, 11, 2)], stay_nights=1)


def test_exhausted_retries_write_error_row_and_next_date_still_runs() -> None:
    # max_attempts_per_date = 2, both dates fail -> 2 fetches per date.
    settings = make_settings(max_attempts_per_date=2)
    fetch = RecordingFetch([NavigationError(f"down {i}") for i in range(4)])
    result = collect(fetch, settings=settings)

    assert len(fetch.calls) == 4  # 2 attempts x 2 dates — isolation holds
    assert (result.dates_ok, result.dates_failed) == (0, 2)
    for row in result.rows:
        assert row["status"] == "error"
        assert row["availability_source"] == "page_error"
        assert row["room_category"] == ""
        assert "after 2 attempt(s)" in row["error_detail"]
        assert "navigation" in row["error_detail"]
    validate_rows(result.rows, DATES, stay_nights=1)


def test_unrendered_page_is_retried_then_error() -> None:
    settings = make_settings(max_attempts_per_date=3)
    fetch = RecordingFetch([GARBAGE_HTML] * 3)
    result = collect(fetch, settings=settings, dates=[date(2026, 11, 2)])

    assert len(fetch.calls) == 3
    assert result.rows[0]["status"] == "error"
    assert "missing_data" in result.rows[0]["error_detail"]


def test_structure_error_is_retried_then_error() -> None:
    fetch = RecordingFetch([NAMELESS_ROW_HTML] * 3)
    result = collect(fetch, dates=[date(2026, 11, 2)])

    assert len(fetch.calls) == 3
    assert result.rows[0]["status"] == "error"
    assert "unexpected_structure" in result.rows[0]["error_detail"]


# --- Block handling (spec §4 — stop, never bypass) -------------------------


def test_block_stops_every_further_page_load() -> None:
    dates = [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)]
    fetch = RecordingFetch([BlockedError("AWS WAF challenge")])
    result = collect(fetch, dates=dates)

    assert len(fetch.calls) == 1  # no retry, no further dates after a block
    assert result.blocked is True
    assert "AWS WAF" in (result.blocked_detail or "")
    assert len(result.rows) == 3  # every date still covered (spec §8.7)
    assert "access block" in result.rows[0]["error_detail"]
    for row in result.rows[1:]:
        assert "not attempted" in row["error_detail"]
    for row in result.rows:
        assert row["status"] == "error"
        assert row["availability_source"] == "page_error"
    validate_rows(result.rows, dates, stay_nights=1)


def test_block_mid_run_keeps_successful_earlier_rows() -> None:
    dates = [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)]
    fetch = RecordingFetch([ROOMTABLE_HTML, BlockedError("blocked now")])
    result = collect(fetch, dates=dates)

    assert len(fetch.calls) == 2  # no third date, no retry after block
    assert result.blocked is True
    assert result.dates_ok == 1
    assert [r["status"] for r in result.rows[:4]] == ["ok"] * 4
    assert [r["status"] for r in result.rows[4:]] == ["error"] * 2
    validate_rows(result.rows, dates, stay_nights=1)


# --- Input guards ----------------------------------------------------------


def test_collect_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        collect_inventory(
            property_record=PROPERTY,
            settings=make_settings(),
            checkin_dates=[date(2026, 11, 2)],
            fetch_html=RecordingFetch([ROOMTABLE_HTML]),
            now=lambda: datetime(2026, 11, 1, 12, 0),  # no tzinfo
        )
