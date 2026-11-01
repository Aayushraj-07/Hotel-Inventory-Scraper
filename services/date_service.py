"""Check-in / check-out date generation (platform-independent).

Spec §5.4 documented assumption (pending assignment-owner clarification):
    * first check-in date = tomorrow (in the configured time zone)
    * generate exactly ``days_to_scrape`` check-in dates (default 30)
    * last check-in date  = tomorrow + (count - 1) days
    * checkout date       = check-in date + stay_nights days

The count stays configurable so switching to 31 dates is a config-only
change if clarification arrives.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class DateServiceError(ValueError):
    """Raised for invalid date-generation inputs."""


def _resolve_timezone(timezone_name: str) -> ZoneInfo:
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise DateServiceError(f"Invalid timezone: {timezone_name!r}.")
    try:
        return ZoneInfo(timezone_name.strip())
    except ZoneInfoNotFoundError as exc:
        raise DateServiceError(f"Unknown timezone: {timezone_name!r}.") from exc


def current_date(timezone_name: str, now_utc: datetime | None = None) -> date:
    """Today's date in the configured time zone.

    ``now_utc`` exists for deterministic tests; production uses the real
    current UTC time.
    """
    tz = _resolve_timezone(timezone_name)
    moment = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise DateServiceError("now_utc must be a timezone-aware datetime.")
    return moment.astimezone(tz).date()


def generate_checkin_dates(
    days_to_scrape: int,
    timezone_name: str,
    stay_nights: int = 1,
    today: date | None = None,
) -> list[date]:
    """Generate the ordered check-in dates for the run.

    The first date is always *tomorrow* relative to ``today`` (or the
    current date in ``timezone_name`` when ``today`` is not supplied).
    """
    if isinstance(days_to_scrape, bool) or not isinstance(days_to_scrape, int):
        raise DateServiceError(
            f"days_to_scrape must be a positive integer, got {days_to_scrape!r}."
        )
    if days_to_scrape < 1:
        raise DateServiceError(f"days_to_scrape must be >= 1, got {days_to_scrape}.")
    if isinstance(stay_nights, bool) or not isinstance(stay_nights, int) or stay_nights < 1:
        raise DateServiceError(f"stay_nights must be a positive integer, got {stay_nights!r}.")

    base_day = today if today is not None else current_date(timezone_name)
    if isinstance(base_day, datetime):
        raise DateServiceError("today must be a date, not a datetime.")
    if not isinstance(base_day, date):
        raise DateServiceError(f"today must be a date, got {base_day!r}.")

    tomorrow = base_day + timedelta(days=1)
    return [tomorrow + timedelta(days=offset) for offset in range(days_to_scrape)]


def checkout_for(checkin_date: date, stay_nights: int = 1) -> date:
    """Checkout date for a check-in date (Phase 1: exactly +1 day)."""
    return checkin_date + timedelta(days=stay_nights)
