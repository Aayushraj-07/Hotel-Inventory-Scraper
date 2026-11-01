"""Unit tests for row validation (spec §14.1 validation items, §8 contract).

All tests run offline; no Booking.com access required.
"""

from datetime import date

import pytest

from services.validation_service import ValidationError, validate_rows
from tests.helpers import (
    make_error_row,
    make_no_rooms_row,
    make_row,
    make_sold_out_row,
    rows_for,
)

DATES = [date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)]


def valid_mixed_rows() -> list[dict]:
    """Three dates: one ok, one sold_out, one no_rooms_listed."""
    return [
        make_row(checkin_date="2026-11-01", checkout_date="2026-11-02"),
        make_sold_out_row(checkin_date="2026-11-02", checkout_date="2026-11-03"),
        make_no_rooms_row(checkin_date="2026-11-03", checkout_date="2026-11-04"),
    ]


# --- Happy paths --------------------------------------------------------------


def test_valid_mixed_rows_pass_and_are_returned() -> None:
    rows = valid_mixed_rows()
    assert validate_rows(rows, DATES) is rows


def test_valid_error_row_passes() -> None:
    rows = [
        make_row(checkin_date="2026-11-01", checkout_date="2026-11-02"),
        make_row(checkin_date="2026-11-02", checkout_date="2026-11-03"),
        make_error_row(checkin_date="2026-11-03", checkout_date="2026-11-04"),
    ]
    assert validate_rows(rows, DATES) == rows


def test_ok_row_with_unknown_count_passes() -> None:
    """Spec §7.2: no defensible count -> empty, never fabricated."""
    rows = rows_for(DATES, rooms_available=None, availability_source="no_numeric_signal")
    validate_rows(rows, DATES)


def test_scarcity_message_source_passes() -> None:
    rows = rows_for(DATES, rooms_available=2, availability_source="scarcity_message")
    validate_rows(rows, DATES)


def test_is_capped_boolean_allowed() -> None:
    rows = rows_for(DATES, is_capped=True)
    validate_rows(rows, DATES)


def test_stay_nights_parameter_controls_checkout_rule() -> None:
    """One-night rule: a +30-day checkout must fail with stay_nights=1."""
    rows = [
        make_row(checkin_date=d.isoformat(), checkout_date="2026-12-01")
        for d in DATES
    ]
    with pytest.raises(ValidationError, match="checkout_date"):
        validate_rows(rows, DATES, stay_nights=1)


# --- Counts -------------------------------------------------------------------


def test_negative_count_rejected() -> None:
    rows = rows_for(DATES, rooms_available=-1)
    with pytest.raises(ValidationError, match="negative"):
        validate_rows(rows, DATES)


def test_non_integer_count_rejected() -> None:
    rows = rows_for(DATES, rooms_available="2")
    with pytest.raises(ValidationError, match="rooms_available"):
        validate_rows(rows, DATES)


def test_float_count_rejected() -> None:
    rows = rows_for(DATES, rooms_available=2.5)
    with pytest.raises(ValidationError, match="rooms_available"):
        validate_rows(rows, DATES)


def test_sold_out_with_nonzero_count_rejected() -> None:
    """Spec §8.4: sold_out implies rooms_available must be 0."""
    rows = [
        make_sold_out_row(checkin_date="2026-11-01", checkout_date="2026-11-02",
                          rooms_available=3),
        make_row(checkin_date="2026-11-02", checkout_date="2026-11-03"),
        make_row(checkin_date="2026-11-03", checkout_date="2026-11-04"),
    ]
    with pytest.raises(ValidationError, match="sold_out"):
        validate_rows(rows, DATES)


def test_sold_out_with_empty_count_rejected() -> None:
    rows = [
        make_sold_out_row(checkin_date="2026-11-01", checkout_date="2026-11-02",
                          rooms_available=None),
        make_row(checkin_date="2026-11-02", checkout_date="2026-11-03"),
        make_row(checkin_date="2026-11-03", checkout_date="2026-11-04"),
    ]
    with pytest.raises(ValidationError, match="sold_out"):
        validate_rows(rows, DATES)


# --- Duplicates and coverage ---------------------------------------------------


def test_duplicate_date_category_pair_rejected() -> None:
    rows = valid_mixed_rows()
    rows.append(make_row(checkin_date="2026-11-01", checkout_date="2026-11-02"))
    with pytest.raises(ValidationError, match="duplicate"):
        validate_rows(rows, DATES)


def test_duplicate_date_level_record_rejected() -> None:
    rows = valid_mixed_rows()
    rows.append(make_no_rooms_row(checkin_date="2026-11-03", checkout_date="2026-11-04"))
    with pytest.raises(ValidationError, match="duplicate"):
        validate_rows(rows, DATES)


def test_uncovered_date_rejected() -> None:
    rows = valid_mixed_rows()[:2]
    with pytest.raises(ValidationError, match="No output record"):
        validate_rows(rows, DATES)


def test_row_for_unknown_date_rejected() -> None:
    rows = valid_mixed_rows()
    rows[0] = make_row(checkin_date="2026-12-01", checkout_date="2026-12-02")
    with pytest.raises(ValidationError, match="not among the generated"):
        validate_rows(rows, DATES)


def test_empty_rows_rejected() -> None:
    with pytest.raises(ValidationError, match="No rows"):
        validate_rows([], DATES)


def test_empty_expected_dates_rejected() -> None:
    with pytest.raises(ValidationError, match="expected check-in dates"):
        validate_rows(valid_mixed_rows(), [])


# --- Dates --------------------------------------------------------------------


def test_checkout_not_one_day_after_rejected() -> None:
    rows = valid_mixed_rows()
    rows[0] = make_row(checkin_date="2026-11-01", checkout_date="2026-11-03")
    with pytest.raises(ValidationError, match="checkout_date"):
        validate_rows(rows, DATES)


@pytest.mark.parametrize("bad_date", ["Nov 1 2026", "2026-11-1", "01-11-2026", 20261101])
def test_non_iso_or_non_canonical_checkin_rejected(bad_date: object) -> None:
    rows = valid_mixed_rows()
    rows[0] = make_row(checkin_date=bad_date, checkout_date="2026-11-02")
    with pytest.raises(ValidationError, match="checkin_date"):
        validate_rows(rows, DATES)


def test_non_string_checkout_rejected() -> None:
    rows = valid_mixed_rows()
    rows[0] = make_row(checkin_date="2026-11-01", checkout_date=None)
    with pytest.raises(ValidationError, match="checkout_date"):
        validate_rows(rows, DATES)


# --- Status / source / category rules ------------------------------------------


def test_invalid_status_rejected() -> None:
    rows = rows_for(DATES, status="available")
    with pytest.raises(ValidationError, match="status"):
        validate_rows(rows, DATES)


def test_invalid_availability_source_rejected() -> None:
    rows = rows_for(DATES, availability_source="vibes")
    with pytest.raises(ValidationError, match="availability_source"):
        validate_rows(rows, DATES)


def test_error_status_requires_page_error_source() -> None:
    rows = [
        make_row(checkin_date="2026-11-01", checkout_date="2026-11-02"),
        make_row(checkin_date="2026-11-02", checkout_date="2026-11-03"),
        make_error_row(checkin_date="2026-11-03", checkout_date="2026-11-04",
                       availability_source="room_selector"),
    ]
    with pytest.raises(ValidationError, match="page_error"):
        validate_rows(rows, DATES)


def test_error_status_requires_error_detail() -> None:
    rows = [
        make_row(checkin_date="2026-11-01", checkout_date="2026-11-02"),
        make_row(checkin_date="2026-11-02", checkout_date="2026-11-03"),
        make_error_row(checkin_date="2026-11-03", checkout_date="2026-11-04",
                       error_detail=None),
    ]
    with pytest.raises(ValidationError, match="error_detail"):
        validate_rows(rows, DATES)


def test_no_rooms_status_requires_matching_source() -> None:
    rows = [
        make_row(checkin_date="2026-11-01", checkout_date="2026-11-02"),
        make_row(checkin_date="2026-11-02", checkout_date="2026-11-03"),
        make_no_rooms_row(checkin_date="2026-11-03", checkout_date="2026-11-04",
                          availability_source="no_numeric_signal"),
    ]
    with pytest.raises(ValidationError, match="no_rooms_listed"):
        validate_rows(rows, DATES)


def test_empty_category_with_ok_status_rejected() -> None:
    rows = rows_for(DATES, room_category="")
    with pytest.raises(ValidationError, match="empty room_category"):
        validate_rows(rows, DATES)


def test_category_with_no_rooms_status_rejected() -> None:
    rows = [
        make_row(checkin_date="2026-11-01", checkout_date="2026-11-02"),
        make_row(checkin_date="2026-11-02", checkout_date="2026-11-03"),
        make_no_rooms_row(checkin_date="2026-11-03", checkout_date="2026-11-04",
                          room_category="Deluxe Double Room"),
    ]
    with pytest.raises(ValidationError, match="date-level"):
        validate_rows(rows, DATES)


def test_non_string_category_rejected() -> None:
    rows = rows_for(DATES, room_category=123)
    with pytest.raises(ValidationError, match="room_category"):
        validate_rows(rows, DATES)


# --- Missing columns, timestamps, price ----------------------------------------


def test_missing_required_column_rejected() -> None:
    rows = rows_for(DATES)
    del rows[0]["min_price_inr"]
    with pytest.raises(ValidationError, match="missing required column"):
        validate_rows(rows, DATES)


def test_non_iso_timestamp_rejected() -> None:
    rows = rows_for(DATES, scrape_timestamp="31/10/2026 17:15")
    with pytest.raises(ValidationError, match="scrape_timestamp"):
        validate_rows(rows, DATES)


def test_timestamp_without_offset_rejected() -> None:
    """Spec §8.3: ISO 8601 with time-zone offset."""
    rows = rows_for(DATES, scrape_timestamp="2026-10-31T17:15:00")
    with pytest.raises(ValidationError, match="time-zone offset"):
        validate_rows(rows, DATES)


def test_negative_price_rejected() -> None:
    rows = rows_for(DATES, min_price_inr=-500)
    with pytest.raises(ValidationError, match="min_price_inr"):
        validate_rows(rows, DATES)


def test_empty_price_allowed() -> None:
    rows = rows_for(DATES, min_price_inr=None)
    validate_rows(rows, DATES)


def test_non_boolean_is_capped_rejected() -> None:
    rows = rows_for(DATES, is_capped="yes")
    with pytest.raises(ValidationError, match="is_capped"):
        validate_rows(rows, DATES)


def test_checkin_dates_must_be_date_objects() -> None:
    with pytest.raises(ValidationError, match="date objects"):
        validate_rows(valid_mixed_rows(), ["2026-11-01"])
