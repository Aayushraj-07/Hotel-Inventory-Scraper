"""Unit tests for date generation (spec §14.1 — date items, §5.4 assumption).

All tests run offline; no Booking.com access required.
"""

from datetime import date, datetime, timezone

import pytest

from services.date_service import (
    DateServiceError,
    checkout_for,
    current_date,
    generate_checkin_dates,
)

TZ = "Asia/Kolkata"


def test_generates_exactly_configured_number_of_dates() -> None:
    dates = generate_checkin_dates(30, TZ, today=date(2026, 3, 10))
    assert len(dates) == 30


def test_first_date_is_tomorrow_and_last_is_tomorrow_plus_29() -> None:
    """Spec §5.4: tomorrow .. tomorrow + 29 for exactly 30 dates."""
    dates = generate_checkin_dates(30, TZ, today=date(2026, 3, 10))
    assert dates[0] == date(2026, 3, 11)
    assert dates[-1] == date(2026, 4, 9)  # tomorrow + 29 days


def test_dates_are_contiguous_and_ascending() -> None:
    dates = generate_checkin_dates(30, TZ, today=date(2026, 3, 10))
    for earlier, later in zip(dates, dates[1:]):
        assert (later - earlier).days == 1


def test_checkout_is_exactly_one_day_after_checkin() -> None:
    """Spec §8.7: checkout_date must be exactly one day after checkin_date."""
    dates = generate_checkin_dates(30, TZ, today=date(2026, 3, 10))
    for checkin in dates:
        assert checkout_for(checkin) == checkin + (date(2026, 3, 1) - date(2026, 2, 28))


def test_date_count_is_configurable() -> None:
    """Assumption is configurable: switching to 31 dates is a config change."""
    assert len(generate_checkin_dates(1, TZ, today=date(2026, 3, 10))) == 1
    assert len(generate_checkin_dates(5, TZ, today=date(2026, 3, 10))) == 5
    assert len(generate_checkin_dates(31, TZ, today=date(2026, 3, 10))) == 31


@pytest.mark.parametrize("count", [0, -1])
def test_non_positive_date_count_rejected(count: int) -> None:
    with pytest.raises(DateServiceError, match="days_to_scrape"):
        generate_checkin_dates(count, TZ, today=date(2026, 3, 10))


def test_current_date_uses_configured_timezone() -> None:
    """20:30 UTC on Jan 1 is already Jan 2 in Asia/Kolkata (+05:30)."""
    now_utc = datetime(2026, 1, 1, 20, 30, tzinfo=timezone.utc)
    assert current_date("Asia/Kolkata", now_utc=now_utc) == date(2026, 1, 2)
    assert current_date("UTC", now_utc=now_utc) == date(2026, 1, 1)


def test_tomorrow_resolves_from_timezone_aware_today() -> None:
    now_utc = datetime(2026, 1, 1, 20, 30, tzinfo=timezone.utc)
    today = current_date("Asia/Kolkata", now_utc=now_utc)
    dates = generate_checkin_dates(30, TZ, today=today)
    assert dates[0] == date(2026, 1, 3)  # Jan 2 in Kolkata -> tomorrow Jan 3


def test_unknown_timezone_rejected() -> None:
    with pytest.raises(DateServiceError, match="Unknown timezone"):
        generate_checkin_dates(30, "Mars/Olympus_Mons")


def test_naive_datetime_rejected_as_now_utc() -> None:
    with pytest.raises(DateServiceError, match="timezone-aware"):
        current_date("Asia/Kolkata", now_utc=datetime(2026, 1, 1, 12, 0))
