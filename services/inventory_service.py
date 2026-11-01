"""Per-date inventory collection: retries, statuses, aggregation (spec §6, §10).

Orchestrates one property's run over the generated check-in dates:

    * one search URL per date (:func:`scraper.booking_scraper.build_property_url`,
      observed query parameters only)
    * up to ``settings.max_attempts_per_date`` attempts per date for
      RETRYABLE failures (navigation, timeout, structure, missing data)
    * ``BlockedError`` (CAPTCHA / bot protection / access block) stops ALL
      further page loads immediately — never retried, never bypassed
      (spec §4). The affected date and every remaining date still get an
      honest ``error`` row so every generated date is covered (spec §8.7);
      the run is flagged ``blocked`` for the exit code.
    * parser observations are mapped to CSV-row dicts: exactly the eight
      required columns plus the two approved bonus columns
      (``error_detail``, ``is_capped`` — spec §8.2/§9). Evidence fields
      (``room_type_id``, selector maximum, taxes, scarcity wording) are
      retained in the structured per-category logs (spec §7.2, §8.5).

Status handling (spec §8.4): rows -> ``ok``; loaded property page with
zero rows -> one date-level ``no_rooms_listed`` record; exhausted
retries / block -> ``error`` with a concise ``error_detail``. A failure
is never represented as ``sold_out`` or ``no_rooms_listed``.

Testability (spec §14.2): the browser arrives as an injected
``fetch_html(url) -> html`` callable, so this module runs fully offline
against the sanitized fixtures. Production passes a closure over
:class:`scraper.browser.BrowserSession` (which owns the shared 3-second
rate limiter and block detection).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from services.date_service import checkout_for
from services.property_service import Property, Settings
from scraper.availability_parser import looks_like_property_page, parse_rooms
from scraper.booking_scraper import build_property_url
from scraper.exceptions import BlockedError, MissingDataError, ScraperError

log = logging.getLogger("hotel_scraper.inventory")


@dataclass
class CollectionResult:
    """Aggregated outcome of one property's per-date collection."""

    rows: list[dict] = field(default_factory=list)
    blocked: bool = False
    blocked_detail: str | None = None
    dates_ok: int = 0          # dates with >= 1 room category
    dates_no_rooms: int = 0    # loaded page, zero categories
    dates_failed: int = 0      # error rows (retries exhausted or blocked)


def _observation_row(
    *,
    checkin: date,
    checkout: date,
    timestamp: str,
    room_category: str,
    rooms_available: int | None,
    availability_source: str,
    min_price_inr: int | None,
    status: str,
    error_detail: str | None,
    is_capped: bool | None,
) -> dict:
    """One CSV-contract row (8 required + 2 bonus columns, spec §8.2/§9)."""
    return {
        "scrape_timestamp": timestamp,
        "checkin_date": checkin.isoformat(),
        "checkout_date": checkout.isoformat(),
        "room_category": room_category,
        "rooms_available": rooms_available,
        "availability_source": availability_source,
        "min_price_inr": min_price_inr,
        "status": status,
        "error_detail": error_detail,
        "is_capped": is_capped,
    }


def _error_row(
    *,
    checkin: date,
    checkout: date,
    timestamp: str,
    detail: str,
) -> dict:
    """Date-level ``error`` record (spec §8.4/§8.6)."""
    return _observation_row(
        checkin=checkin,
        checkout=checkout,
        timestamp=timestamp,
        room_category="",
        rooms_available=None,
        availability_source="page_error",
        min_price_inr=None,
        status="error",
        error_detail=detail,
        is_capped=None,
    )


def _no_rooms_row(*, checkin: date, checkout: date, timestamp: str) -> dict:
    """Date-level ``no_rooms_listed`` record (spec §8.4/§8.6)."""
    return _observation_row(
        checkin=checkin,
        checkout=checkout,
        timestamp=timestamp,
        room_category="",
        rooms_available=None,
        availability_source="no_rooms_listed",
        min_price_inr=None,
        status="no_rooms_listed",
        error_detail=None,
        is_capped=None,
    )


def _collect_date(
    *,
    property_record: Property,
    settings: Settings,
    checkin: date,
    checkout: date,
    fetch_html: Callable[[str], str],
    timestamp: str,
) -> list[dict]:
    """Fetch + parse one date, retrying retryable failures (spec §10).

    Raises:
        BlockedError: always, immediately — the caller stops the run.
            Blocks are never retried (spec §4).
    """
    url = build_property_url(
        property_record.booking_url or "",
        checkin=checkin,
        checkout=checkout,
        adults=settings.adults,
        rooms=settings.rooms,
        currency=settings.currency,
    )
    attempts = settings.max_attempts_per_date
    last_detail = "no attempt made"

    for attempt in range(1, attempts + 1):
        try:
            html = fetch_html(url)
            observations = parse_rooms(html)

            if observations:
                log.info(
                    "date=%s attempt=%d/%d ok: %d room categories",
                    checkin.isoformat(),
                    attempt,
                    attempts,
                    len(observations),
                )
                rows = []
                for obs in observations:
                    # Evidence preserved in structured logs (spec §7.2, §8.5).
                    log.info(
                        "observed | date=%s | category=%r | status=%s | source=%s "
                        "| rooms_available=%s | price_inr=%s | room_type_id=%s "
                        "| selector_max=%s | taxes_inr=%s | scarcity=%r",
                        checkin.isoformat(),
                        obs["room_category"],
                        obs["status"],
                        obs["availability_source"],
                        obs["rooms_available"],
                        obs["min_price_inr"],
                        obs["room_type_id"],
                        obs["qty_selectable_max"],
                        obs["taxes_extra_inr"],
                        obs["scarcity_message"],
                    )
                    rows.append(
                        _observation_row(
                            checkin=checkin,
                            checkout=checkout,
                            timestamp=timestamp,
                            room_category=obs["room_category"],
                            rooms_available=obs["rooms_available"],
                            availability_source=obs["availability_source"],
                            min_price_inr=obs["min_price_inr"],
                            status=obs["status"],
                            error_detail=obs["error_detail"],
                            is_capped=obs["is_capped"],
                        )
                    )
                return rows

            if looks_like_property_page(html):
                # Loaded and inspected, but zero categories (spec §8.4).
                log.info(
                    "date=%s attempt=%d/%d: property page loaded, no rooms listed",
                    checkin.isoformat(),
                    attempt,
                    attempts,
                )
                return [_no_rooms_row(checkin=checkin, checkout=checkout, timestamp=timestamp)]

            # Neither room rows nor property-page markers: the page never
            # delivered usable content -> retryable (spec §6, §10).
            raise MissingDataError(
                "Page contained neither room rows nor property-page markers; "
                "room content did not render."
            )
        except BlockedError:
            # Stop this date AND the whole run (spec §4). Never retried.
            raise
        except ScraperError as exc:
            last_detail = f"{exc.category}: {exc}"
            log.warning(
                "date=%s attempt=%d/%d failed [%s]: %s",
                checkin.isoformat(),
                attempt,
                attempts,
                exc.category,
                exc,
            )

    log.error("date=%s: all %d attempts failed; recording error row", checkin.isoformat(), attempts)
    return [
        _error_row(
            checkin=checkin,
            checkout=checkout,
            timestamp=timestamp,
            detail=f"after {attempts} attempt(s): {last_detail}",
        )
    ]


def collect_inventory(
    *,
    property_record: Property,
    settings: Settings,
    checkin_dates: list[date],
    fetch_html: Callable[[str], str],
    now: Callable[[], datetime],
) -> CollectionResult:
    """Collect observations for every check-in date (spec §6, §10).

    A failure on one date never stops the others (spec §3.1 item 11) —
    except a block, which stops everything by design (spec §4).

    Args:
        fetch_html: injected browser — ``url -> rendered HTML``.
        now: returns the current timezone-aware observation timestamp
            (injectable for deterministic tests).

    Returns:
        :class:`CollectionResult` with CSV-contract rows and counters.
        Rows are NOT yet validated — run
        :func:`services.validation_service.validate_rows` before writing.
    """
    result = CollectionResult()
    timestamp = now().isoformat()
    if now().tzinfo is None:
        raise ValueError("now() must return a timezone-aware datetime (spec §8.3).")

    for checkin in checkin_dates:
        checkout = checkout_for(checkin, settings.stay_nights)

        if result.blocked:
            # Activity stopped by a block on an earlier date: no further
            # page loads, but the date still gets coverage (spec §8.7).
            result.dates_failed += 1
            result.rows.append(
                _error_row(
                    checkin=checkin,
                    checkout=checkout,
                    timestamp=timestamp,
                    detail="not attempted: run stopped by access block on an "
                    "earlier date (spec §4 — no bypass attempts)",
                )
            )
            continue

        try:
            rows = _collect_date(
                property_record=property_record,
                settings=settings,
                checkin=checkin,
                checkout=checkout,
                fetch_html=fetch_html,
                timestamp=timestamp,
            )
        except BlockedError as exc:
            result.blocked = True
            result.blocked_detail = str(exc)
            log.error(
                "ACCESS BLOCK on date=%s — stopping all further page loads "
                "(spec §4, no bypass): %s",
                checkin.isoformat(),
                exc,
            )
            result.dates_failed += 1
            result.rows.append(
                _error_row(
                    checkin=checkin,
                    checkout=checkout,
                    timestamp=timestamp,
                    detail=f"access block: {exc}",
                )
            )
            continue

        result.rows.extend(rows)
        status = rows[0]["status"]
        if status == "ok":
            result.dates_ok += 1
        elif status == "no_rooms_listed":
            result.dates_no_rooms += 1
        else:
            result.dates_failed += 1

    log.info(
        "Collection finished | dates_ok=%d | dates_no_rooms=%d | dates_failed=%d "
        "| blocked=%s | rows=%d",
        result.dates_ok,
        result.dates_no_rooms,
        result.dates_failed,
        result.blocked,
        len(result.rows),
    )
    return result
