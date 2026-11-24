"""Per-date inventory collection: retries, statuses, aggregation (spec §6, §10).

Orchestrates one property's run over the generated check-in dates:

    * one search URL per date (:func:`scraper.booking_scraper.build_property_url`,
      observed query parameters only)
    * a PER-STATE retry table (assignment §4): network/timeout/missing-data/
      unknown failures get up to ``settings.max_attempts_per_date``
      attempts; structure/extraction/property failures get NO retry; and
      blocking states (CAPTCHA / WAF / bot protection / HTTP denial) stop
      ALL further page loads immediately — never retried, never bypassed.
      The affected date and every remaining date still get an honest
      ``error`` row so every generated date is covered (spec §8.7); the run
      is flagged ``blocked`` for the exit code.
    * an :class:`~scraper.access_state.AccessDiagnostic` is logged for every
      attempt (state, attempt number, redacted URL/status/title indicators,
      room-info presence, extraction success — assignment §4).
    * parser observations are mapped to CSV-row dicts: exactly the eight
      required columns plus the three approved bonus columns
      (``error_detail``, ``is_capped``, ``data_source`` — spec §8.2/§9).
      ``data_source`` honestly labels each row as live/fixture/snapshot.
      Evidence fields (``room_type_id``, selector maximum, taxes, scarcity
      wording) are retained in the structured per-category logs (§7.2/§8.5).

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
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from services.date_service import checkout_for
from services.property_service import Property, Settings
from services.validation_service import DATA_SOURCES
from scraper.access_state import (
    AccessDiagnostic,
    AccessState,
    redact_text,
    state_for_exception,
)
from scraper.availability_parser import looks_like_property_page, parse_rooms
from scraper.booking_scraper import build_property_url
from scraper.exceptions import BlockedError, MissingDataError, ScraperError

log = logging.getLogger("hotel_scraper.inventory")

# --- Per-state retry policy (assignment §4/§10) -------------------------------
#
# Each failure is mapped to an AccessState (scraper.access_state) and the
# state — not the exception class — decides what happens next:
#
#   RETRYABLE_STATES    -> transient: retry up to max_attempts_per_date
#   BLOCKING_STATES     -> stop the affected activity immediately, never
#                          retry around it (arrives as BlockedError)
#   NO_RETRY_STATES     -> structural/permanent: one attempt, honest error
#                          row for this date, other dates continue
#
# PROPERTY_MISMATCH / PROPERTY_NOT_FOUND normally stop at verification
# (hotel_finder); here they are a safety net that never burns retries.
RETRYABLE_STATES = frozenset(
    {
        AccessState.NETWORK_ERROR,
        AccessState.NAVIGATION_TIMEOUT,
        AccessState.ROOM_DATA_NOT_LOADED,
        AccessState.UNKNOWN_ERROR,
    }
)
NO_RETRY_STATES = frozenset(
    {
        AccessState.PAGE_STRUCTURE_CHANGED,
        AccessState.EXTRACTION_ERROR,
        AccessState.PROPERTY_NOT_FOUND,
        AccessState.PROPERTY_MISMATCH,
    }
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _title_from_html(html: str) -> str:
    """Page <title> from fetched HTML (standard element, no site selector)."""
    match = _TITLE_RE.search(html)
    return match.group(1).strip() if match else ""


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
    data_source: str,
) -> dict:
    """One CSV-contract row (8 required + 3 bonus columns, spec §8.2/§9)."""
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
        "data_source": data_source,
    }


def _error_row(
    *,
    checkin: date,
    checkout: date,
    timestamp: str,
    detail: str,
    data_source: str,
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
        data_source=data_source,
    )


def _no_rooms_row(
    *, checkin: date, checkout: date, timestamp: str, data_source: str
) -> dict:
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
        data_source=data_source,
    )


def _collect_date(
    *,
    property_record: Property,
    settings: Settings,
    checkin: date,
    checkout: date,
    fetch_html: Callable[[str], str],
    timestamp: str,
    data_source: str,
) -> list[dict]:
    """Fetch + parse one date using the per-state retry table (assignment §4).

    Every attempt emits one structured :class:`AccessDiagnostic` record to
    the log. Blocking states always re-raise :class:`BlockedError` so the
    caller stops the whole run (spec §4); other non-retryable states break
    immediately with an honest error row (no wasted attempts).
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
    attempts_used = 0
    stop_reason: str | None = None

    for attempt in range(1, attempts + 1):
        attempts_used = attempt
        diag = AccessDiagnostic(attempt=attempt, requested_url=url)
        try:
            html = fetch_html(url)
            diag.page_title = _title_from_html(html)
            observations = parse_rooms(html)
            diag.extraction_succeeded = True

            if observations:
                diag.room_info_present = True
                log.info(
                    "date=%s attempt=%d/%d ok: %d room categories | diagnostic=%s",
                    checkin.isoformat(),
                    attempt,
                    attempts,
                    len(observations),
                    diag.to_log_dict(),
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
                            data_source=data_source,
                        )
                    )
                return rows

            diag.room_info_present = looks_like_property_page(html)
            if diag.room_info_present:
                # Loaded and inspected, but zero categories (spec §8.4).
                log.info(
                    "date=%s attempt=%d/%d: property page loaded, no rooms listed "
                    "| diagnostic=%s",
                    checkin.isoformat(),
                    attempt,
                    attempts,
                    diag.to_log_dict(),
                )
                return [
                    _no_rooms_row(
                        checkin=checkin,
                        checkout=checkout,
                        timestamp=timestamp,
                        data_source=data_source,
                    )
                ]

            # Neither room rows nor property-page markers: the page never
            # delivered usable content -> ROOM_DATA_NOT_LOADED (retryable).
            raise MissingDataError(
                "Page contained neither room rows nor property-page markers; "
                "room content did not render."
            )
        except BlockedError as exc:
            # Stop this date AND the whole run (spec §4). Never retried.
            diag.access_state = state_for_exception(exc)
            diag.navigation_error = str(exc)
            log.error("attempt diagnostic | %s", diag.to_log_dict())
            raise
        except ScraperError as exc:
            state = state_for_exception(exc)
            diag.access_state = state
            diag.navigation_error = str(exc)
            message = redact_text(str(exc))
            last_detail = f"[{state.value}] {exc.category}: {message}"
            log.warning(
                "date=%s attempt=%d/%d failed [%s/%s]: %s | diagnostic=%s",
                checkin.isoformat(),
                attempt,
                attempts,
                state.value,
                exc.category,
                message,
                diag.to_log_dict(),
            )

            if state in NO_RETRY_STATES:
                stop_reason = f"{state.value} — no retry (per-state policy)"
                break
            if state in RETRYABLE_STATES:
                if attempt < attempts:
                    continue
                stop_reason = f"{state.value} — retries exhausted ({attempts})"
                break

            # Defensive: a blocking state that did not arrive as BlockedError
            # still stops the run — never a retry around a protection signal.
            raise BlockedError(message, url=url, access_state=state) from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            # Parsing/extraction bugs are EXTRACTION_ERROR: no retry, honest
            # error row for this date, other dates continue (assignment §4).
            state = AccessState.EXTRACTION_ERROR
            diag.access_state = state
            message = redact_text(str(exc))
            diag.navigation_error = message
            last_detail = f"[{state.value}] parser: {message}"
            log.warning(
                "date=%s attempt=%d/%d extraction failed: %s | diagnostic=%s",
                checkin.isoformat(),
                attempt,
                attempts,
                message,
                diag.to_log_dict(),
                exc_info=True,
            )
            stop_reason = f"{state.value} — no retry (per-state policy)"
            break

    detail = f"{stop_reason or 'attempt loop ended'}: {last_detail}"
    log.error(
        "date=%s: no usable page after %d attempt(s); recording error row (%s)",
        checkin.isoformat(),
        attempts_used,
        stop_reason,
    )
    return [
        _error_row(
            checkin=checkin,
            checkout=checkout,
            timestamp=timestamp,
            detail=detail,
            data_source=data_source,
        )
    ]


def collect_inventory(
    *,
    property_record: Property,
    settings: Settings,
    checkin_dates: list[date],
    fetch_html: Callable[[str], str],
    now: Callable[[], datetime],
    data_source: str = "live",
) -> CollectionResult:
    """Collect observations for every check-in date (spec §6, §10).

    A failure on one date never stops the others (spec §3.1 item 11) —
    except a block, which stops everything by design (spec §4).

    Args:
        fetch_html: injected browser — ``url -> rendered HTML``.
        now: returns the current timezone-aware observation timestamp
            (injectable for deterministic tests).
        data_source: provenance of every row — ``live``, ``fixture`` or
            ``snapshot`` (assignment §8). Default ``live`` keeps the
            pre-upgrade call contract intact; offline modes pass their own
            label explicitly so no synthetic row can claim to be live.

    Returns:
        :class:`CollectionResult` with CSV-contract rows and counters.
        Rows are NOT yet validated — run
        :func:`services.validation_service.validate_rows` before writing.
    """
    if data_source not in DATA_SOURCES:
        raise ValueError(
            f"data_source {data_source!r} is invalid; expected one of "
            f"{sorted(DATA_SOURCES)}."
        )

    current = now()
    if current.tzinfo is None:
        raise ValueError("now() must return a timezone-aware datetime (spec §8.3).")
    result = CollectionResult()
    timestamp = current.isoformat()

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
                    data_source=data_source,
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
                data_source=data_source,
            )
        except BlockedError as exc:
            block_detail = redact_text(str(exc))
            result.blocked = True
            result.blocked_detail = block_detail
            log.error(
                "ACCESS BLOCK on date=%s — stopping all further page loads "
                "(spec §4, no bypass): %s",
                checkin.isoformat(),
                block_detail,
            )
            result.dates_failed += 1
            result.rows.append(
                _error_row(
                    checkin=checkin,
                    checkout=checkout,
                    timestamp=timestamp,
                    detail=f"access block: {block_detail}",
                    data_source=data_source,
                )
            )
            continue

        result.rows.extend(rows)
        status = rows[0]["status"]
        if status in ("ok", "sold_out"):
            # sold_out is an observed zero, not a failed collection (D15).
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
