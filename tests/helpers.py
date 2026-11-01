"""Shared row builders for tests. Not imported by production code."""

from __future__ import annotations

from datetime import date, timedelta

DEFAULT_TIMESTAMP = "2026-10-31T17:15:00+05:30"
DEFAULT_CATEGORY = "Deluxe Double Room"


def make_row(**overrides) -> dict:
    """A complete, valid 'ok' observation row; override fields as needed."""
    row = {
        "scrape_timestamp": DEFAULT_TIMESTAMP,
        "checkin_date": "2026-11-01",
        "checkout_date": "2026-11-02",
        "room_category": DEFAULT_CATEGORY,
        "rooms_available": 2,
        "availability_source": "room_selector",
        "min_price_inr": 4500,
        "status": "ok",
        "error_detail": None,
        "is_capped": None,
    }
    row.update(overrides)
    return row


def make_sold_out_row(**overrides) -> dict:
    row = make_row(
        rooms_available=0,
        availability_source="not_bookable",
        status="sold_out",
    )
    row.update(overrides)
    return row


def make_no_rooms_row(**overrides) -> dict:
    row = make_row(
        room_category="",
        rooms_available=None,
        availability_source="no_rooms_listed",
        min_price_inr=None,
        status="no_rooms_listed",
    )
    row.update(overrides)
    return row


def make_error_row(**overrides) -> dict:
    row = make_row(
        room_category="",
        rooms_available=None,
        availability_source="page_error",
        min_price_inr=None,
        status="error",
        error_detail="Timeout after 3 attempts",
    )
    row.update(overrides)
    return row


def rows_for(dates: list[date], **overrides) -> list[dict]:
    """One valid 'ok' row per check-in date (checkout = +1 day)."""
    rows = []
    for day in dates:
        row = make_row(
            checkin_date=day.isoformat(),
            checkout_date=(day + timedelta(days=1)).isoformat(),
        )
        row.update(overrides)
        rows.append(row)
    return rows
