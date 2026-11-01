"""Row-level validation for the output CSV contract (spec §8, §14.1).

Runs on the *complete* row set before serialization. A violation here is
an application bug or a mis-shaped observation — it must never silently
reach the CSV.

Locked vocabularies (spec §7.3 / §8.4 — consistent and documented):
    * status:             ok | sold_out | no_rooms_listed | error
    * availability_source: room_selector | scarcity_message | not_bookable
                           | no_numeric_signal | no_rooms_listed | page_error

Row value contract:
    * dates/timestamps are ISO strings ("YYYY-MM-DD" / ISO 8601)
    * rooms_available is a non-negative int, or None/"" when unknown
    * empty room_category is allowed only for date-level records
      (status no_rooms_listed or error), per spec §8.6
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from services.csv_service import REQUIRED_COLUMNS

class ValidationError(Exception):
    """Raised when collected rows violate the output contract."""


VALID_STATUSES = frozenset({"ok", "sold_out", "no_rooms_listed", "error"})

AVAILABILITY_SOURCES = frozenset(
    {
        "room_selector",
        "scarcity_message",
        "not_bookable",
        "no_numeric_signal",
        "no_rooms_listed",
        "page_error",
    }
)

# Statuses that use a date-level record with an empty room_category.
DATE_LEVEL_STATUSES = frozenset({"no_rooms_listed", "error"})


def _parse_iso_date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a 'YYYY-MM-DD' string, got {value!r}.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{label} is not a valid ISO date: {value!r}.") from exc
    if parsed.isoformat() != value:
        raise ValidationError(
            f"{label} must use canonical YYYY-MM-DD format, got {value!r}."
        )
    return parsed


def _normalize_category(value: object, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a string, got {value!r}.")
    return value


def _validate_rooms_available(value: object, label: str, status: str) -> None:
    # Documented convention: empty/None = unknown, 0 only when positively
    # established (spec §7.2, §8.6). Negative counts are forbidden (§8.7).
    if value is None or value == "":
        if status == "sold_out":
            raise ValidationError(f"{label} must be 0 for status 'sold_out'.")
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{label} must be an integer or empty (unknown), got {value!r}."
        )
    if value < 0:
        raise ValidationError(f"{label} must not be negative, got {value}.")
    if status == "sold_out" and value != 0:
        raise ValidationError(
            f"{label} must be 0 for status 'sold_out', got {value} (spec §8.4)."
        )


def _validate_price(value: object, label: str) -> None:
    if value is None or value == "":
        return
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValidationError(f"{label} has unsupported type: {value!r}.")
    if isinstance(value, (int, float)) and value < 0:
        raise ValidationError(f"{label} must not be negative, got {value}.")


def _validate_timestamp(value: object, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{label} must be a non-empty ISO 8601 string, got {value!r}.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{label} is not ISO 8601: {value!r}.") from exc
    if parsed.tzinfo is None:
        raise ValidationError(
            f"{label} must include a time-zone offset (spec §8.3), got {value!r}."
        )


def validate_rows(
    rows: list[dict],
    checkin_dates: list[date],
    stay_nights: int = 1,
) -> list[dict]:
    """Validate the complete row set; return ``rows`` when valid.

    Checks (spec §8.4, §8.6, §8.7):
        * all eight required columns present in every row
        * status and availability_source from the locked vocabularies
        * ISO dates; checkout exactly ``stay_nights`` after check-in
        * every expected check-in date covered by >= 1 record
        * no duplicate (checkin_date, room_category) pairs
        * no negative/invalid room counts; sold_out implies 0
        * empty category only for no_rooms_listed/error (and vice versa)
        * error rows carry an explanatory error_detail
        * scrape_timestamp is timezone-aware ISO 8601

    Raises:
        ValidationError: on the first violation, with a precise message.
    """
    if not rows:
        raise ValidationError(
            "No rows produced: every check-in date needs at least one record."
        )
    if not checkin_dates:
        raise ValidationError("No expected check-in dates supplied.")

    expected: dict[str, date] = {}
    for day in checkin_dates:
        if isinstance(day, datetime) or not isinstance(day, date):
            raise ValidationError(
                f"checkin_dates must contain date objects, got {day!r}."
            )
        expected[day.isoformat()] = day

    seen: set[tuple[str, str]] = set()
    covered: set[str] = set()

    for index, row in enumerate(rows):
        label = f"row[{index}]"
        if not isinstance(row, dict):
            raise ValidationError(f"{label} must be a mapping, got {type(row).__name__}.")

        missing = [column for column in REQUIRED_COLUMNS if column not in row]
        if missing:
            raise ValidationError(
                f"{label} missing required column(s): {', '.join(missing)}."
            )

        # --- Dates (spec §8.7) ------------------------------------------
        checkin = _parse_iso_date(row["checkin_date"], f"{label}.checkin_date")
        checkout = _parse_iso_date(row["checkout_date"], f"{label}.checkout_date")
        if checkout != checkin + timedelta(days=stay_nights):
            raise ValidationError(
                f"{label}.checkout_date must be exactly {stay_nights} day(s) after "
                f"checkin_date ({checkin.isoformat()} -> {checkout.isoformat()})."
            )
        checkin_key = checkin.isoformat()
        if checkin_key not in expected:
            raise ValidationError(
                f"{label}.checkin_date {checkin_key!r} is not among the generated "
                "check-in dates for this run."
            )
        covered.add(checkin_key)

        # --- Status + source vocabularies --------------------------------
        status = row["status"]
        if status not in VALID_STATUSES:
            raise ValidationError(
                f"{label}.status {status!r} is invalid; expected one of "
                f"{sorted(VALID_STATUSES)}."
            )
        source = row["availability_source"]
        if not isinstance(source, str) or source not in AVAILABILITY_SOURCES:
            raise ValidationError(
                f"{label}.availability_source {source!r} is invalid; expected one of "
                f"{sorted(AVAILABILITY_SOURCES)}."
            )
        if status == "error" and source != "page_error":
            raise ValidationError(
                f"{label}: status 'error' requires availability_source "
                f"'page_error', got {source!r}."
            )
        if status == "no_rooms_listed" and source != "no_rooms_listed":
            raise ValidationError(
                f"{label}: status 'no_rooms_listed' requires availability_source "
                f"'no_rooms_listed', got {source!r}."
            )

        # --- Room category (spec §7.5, §8.6) -----------------------------
        category = _normalize_category(row["room_category"], f"{label}.room_category")
        is_date_level = not category.strip()
        if is_date_level and status not in DATE_LEVEL_STATUSES:
            raise ValidationError(
                f"{label}: empty room_category is only allowed for date-level "
                f"statuses {sorted(DATE_LEVEL_STATUSES)}, got {status!r}."
            )
        if status in DATE_LEVEL_STATUSES and not is_date_level:
            raise ValidationError(
                f"{label}: status {status!r} is date-level and requires an empty "
                "room_category (spec §8.6)."
            )

        # --- Availability count (spec §7.2, §8.4) ------------------------
        _validate_rooms_available(row["rooms_available"], f"{label}.rooms_available", status)

        # --- Error detail (spec §8.4 — bonus column enabled) -------------
        if status == "error":
            detail = row.get("error_detail")
            if not isinstance(detail, str) or not detail.strip():
                raise ValidationError(
                    f"{label}: status 'error' requires a non-empty error_detail "
                    "(bonus column is enabled)."
                )

        # --- Price, timestamp, capped flag -------------------------------
        _validate_price(row["min_price_inr"], f"{label}.min_price_inr")
        _validate_timestamp(row["scrape_timestamp"], f"{label}.scrape_timestamp")
        capped = row.get("is_capped")
        if capped is not None and not isinstance(capped, bool):
            raise ValidationError(
                f"{label}.is_capped must be a boolean or empty, got {capped!r}."
            )

        # --- Duplicates (spec §8.7) --------------------------------------
        key = (checkin_key, category.strip())
        if key in seen:
            shown = category.strip() or "<date-level record>"
            raise ValidationError(
                f"{label}: duplicate record for check-in {checkin_key} / "
                f"category {shown!r} (spec §8.7 forbids duplicates)."
            )
        seen.add(key)

    # --- Coverage (spec §8.7) --------------------------------------------
    uncovered = sorted(set(expected) - covered)
    if uncovered:
        raise ValidationError(
            f"No output record for check-in date(s): {', '.join(uncovered)}. "
            "Every generated date needs at least one row "
            "(ok/sold_out/no_rooms_listed/error)."
        )

    return rows
