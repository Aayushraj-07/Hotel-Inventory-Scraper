"""Per-date page fetch for one property (spec §6, §12).

Responsibilities kept deliberately small:

    * build the public property-page URL for one check-in date using the
      query parameters OBSERVED on the real listing URL (m0_findings §2/§8):
      ``checkin``, ``checkout``, ``group_adults``, ``group_rooms``,
      ``group_children``, ``selected_currency``;
    * navigate through :class:`scraper.browser.BrowserSession` so EVERY
      page load passes the shared 3-second rate limiter and the block
      check (spec §4, §10);
    * wait for the OBSERVED room rows to render (spec §6 — a successful
      navigation is not proof that room data loaded);
    * return the rendered HTML for the pure parser
      (:mod:`scraper.availability_parser`).

A wait timeout is intentionally NOT an error by itself: a date with no
available rooms legitimately renders no rows. The caller classifies the
returned HTML (rows -> observations; property page without rows ->
``no_rooms_listed``; anything else -> ``error``, spec §8.4). This module
contains no property-specific logic — property details come from
configuration (spec §5.3, §19).
"""

from __future__ import annotations

import logging
from datetime import date
from urllib.parse import urlencode, urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scraper.availability_parser import ROOM_ROW_SELECTOR
from scraper.browser import BrowserSession

log = logging.getLogger("hotel_scraper.booking")

# Best-effort wait for asynchronously rendered room rows (spec §6).
DEFAULT_ROOMS_WAIT_TIMEOUT_MS = 15_000


def build_property_url(
    base_url: str,
    *,
    checkin: date,
    checkout: date,
    adults: int,
    rooms: int,
    currency: str,
    children: int = 0,
) -> str:
    """One date's public search URL for the configured property.

    Parameter names are the ones observed in the real listing URL during
    M0 (docs/m0_findings.md §2) — not invented. Any query already present
    on the configured base URL is preserved, with the date parameters
    taking precedence for this run.
    """
    if checkout <= checkin:
        raise ValueError(
            f"checkout ({checkout.isoformat()}) must be after "
            f"checkin ({checkin.isoformat()})."
        )
    parts = urlsplit(base_url)
    params: dict[str, str] = {}
    for key, value in (
        pair.split("=", 1) for pair in parts.query.split("&") if pair
    ):
        params[key] = value
    params.update(
        {
            "checkin": checkin.isoformat(),
            "checkout": checkout.isoformat(),
            "group_adults": str(adults),
            "no_rooms": str(rooms),
            "group_children": str(children),
            "selected_currency": currency,
        }
    )
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment)
    )


def fetch_property_html(
    session: BrowserSession,
    url: str,
    *,
    rooms_wait_timeout_ms: int = DEFAULT_ROOMS_WAIT_TIMEOUT_MS,
) -> str:
    """Navigate to ``url`` and return the rendered HTML.

    Raises the typed failures from ``session.goto`` (navigation, timeout,
    ``BlockedError`` — never bypassed, spec §4). A room-rows wait timeout
    does not raise: see the module docstring; classification is the
    caller's job.
    """
    session.goto(url)
    page = session.page
    try:
        page.wait_for_selector(
            ROOM_ROW_SELECTOR, state="attached", timeout=rooms_wait_timeout_ms
        )
        log.debug("Room rows rendered: %s", url)
    except PlaywrightTimeoutError:
        # Not an error: may be a date with no bookable rooms (spec §8.4).
        log.info(
            "No room rows within %dms; returning HTML for classification: %s",
            rooms_wait_timeout_ms,
            url,
        )
    return page.content()
