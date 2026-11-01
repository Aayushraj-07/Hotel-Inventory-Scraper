"""Room-table parsing: captured page HTML -> structured observations.

Built strictly against the sanitized fixture
``tests/fixtures/live_2026-11-01_roomtable.html`` (real public Booking.com
property page for 2026-11-01, evidence recorded in ``docs/m0_findings.md``
§8.5). Every selector and wording below was observed there — nothing is
invented (spec §11, §19).

Observed structure of the Booking.com "new rooms table"
-------------------------------------------------------
    room row        <tr class="... e2e-hprt-table-row ...">
                    (also carries data-block-id, data-hotel-rounded-price)
    room name       a.hprt-roomtype-link > span.hprt-roomtype-icon-link
    public id       a.hprt-roomtype-link[data-room-id]
    quantity        select.hprt-nos-select > <option value="N">   (0..max)
    price           div.bui-price-display__value  -> "₹ 1,040"
                    (stay total for the searched nights, taxes excluded;
                     confirmed via the price popover, m0_findings §8 row 7)
    taxes evidence  div[data-excl-charges-raw]    -> "+₹ 52 taxes and charges"

Honesty rules encoded here (spec §7.1–§7.5, §8.4; m0_findings §8.2)
-------------------------------------------------------------------
    * ``rooms_available`` comes only from an observed numeric signal and
      is never invented.
    * A quantity-selector maximum is recorded as
      ``availability_source="room_selector"`` with ``is_capped=True``:
      the selector maximum may hide larger real inventory (spec §7.2).
    * ``sold_out`` is NEVER emitted by this parser. No "not bookable"
      row markup has ever been observed for this property, so there is
      no evidence to justify it. When real evidence is observed, add a
      documented branch — do not guess markup.
    * Scarcity text is matched only with the exact wording observed on
      Booking.com ("We have N left at this price" — observed on
      search-result cards 2026-11-01), and only when it appears *inside
      the room row itself*.

This module is pure: no browser, no network. It behaves identically on
fixture HTML and on live page HTML (spec §14.2 — offline tests).
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from scraper.exceptions import StructureError

log = logging.getLogger("hotel_scraper.parser")

# --- Observed selectors (m0_findings §8.5 — do not invent additions) ------

ROOM_ROW_SELECTOR = "tr.e2e-hprt-table-row"
NAME_LINK_SELECTOR = "a.hprt-roomtype-link"
NAME_TEXT_SELECTOR = ".hprt-roomtype-icon-link"
SELECT_SELECTOR = "select.hprt-nos-select"
PRICE_SELECTOR = ".bui-price-display__value"
TAXES_SELECTOR = "[data-excl-charges-raw]"

# Exact scarcity wording observed on Booking.com (search-result cards,
# 2026-11-01 — m0_findings §8 row 5). Used only inside a room row.
SCARCITY_PATTERN = re.compile(r"We have (\d+) left at this price")

# Substrings observed on the property page for "did the page load?"
# checks (m0_findings §8.5). Substring checks only — no extra parsing.
PROPERTY_PAGE_MARKERS = (
    'data-testid="searchbox-dates-container"',
    'data-testid="breadcrumb-item"',
)
SEARCH_RESULTS_MARKER = 'data-testid="property-card"'


def parse_price(text: str) -> int | None:
    """Parse a displayed INR price fragment into int rupees.

    Observed formats: "₹\\xa01,040" (NBSP after the symbol, thousands
    separator). Returns None when no amount is present — an absent price
    is recorded as unknown, never invented (spec §7.4).
    """
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    return int(digits)


def looks_like_property_page(html: str) -> bool:
    """Whether ``html`` looks like the property page (for classification).

    Used after a page load to distinguish (spec §7.2, §8.4):

        * property page rendered but zero room rows -> ``no_rooms_listed``
        * page never rendered room/property content -> ``error``

    Truth table (all from observed fixtures, m0_findings §8.5):
        * room rows present                -> True
        * search-results markers present   -> False (different page type)
        * both property-page chrome marks  -> True
        * otherwise                         -> False
    """
    if ROOM_ROW_SELECTOR.split(".")[-1] in html:  # "e2e-hprt-table-row"
        return True
    if SEARCH_RESULTS_MARKER in html:
        return False
    return all(marker in html for marker in PROPERTY_PAGE_MARKERS)


def _clean(text: str) -> str:
    """Collapse whitespace without altering the wording (spec §7.5)."""
    return re.sub(r"\s+", " ", text).strip()


def _max_selectable(row) -> int | None:
    """Highest quantity offered by the row's selector, or None.

    Observed: <select class="hprt-nos-select"> with options 0..max
    (max observed 2–7 per room/date — m0_findings §8.5). Missing select
    or unparseable options -> None (unknown, not zero).
    """
    select = row.select_one(SELECT_SELECTOR)
    if select is None:
        return None
    values: list[int] = []
    for option in select.select("option"):
        raw = option.get("value", "")
        if raw.isdigit():
            values.append(int(raw))
    if not values:
        return None
    return max(values)


def _lowest_price(row) -> int | None:
    """Lowest displayed price on the row (spec §7.4), None if absent."""
    prices = []
    for element in row.select(PRICE_SELECTOR):
        parsed = parse_price(element.get_text(" ", strip=True))
        if parsed is not None:
            prices.append(parsed)
    if not prices:
        return None
    return min(prices)


def _taxes_evidence(row) -> int | None:
    """Rounded "+₹N taxes and charges" amount shown under the price."""
    element = row.select_one(TAXES_SELECTOR)
    if element is None:
        return None
    return parse_price(element.get_text(" ", strip=True))


def _scarcity_in_row(row) -> int | None:
    """Number from the observed scarcity wording, only if inside the row."""
    match = SCARCITY_PATTERN.search(row.get_text(" ", strip=True))
    if match is None:
        return None
    return int(match.group(1))


def parse_rooms(html: str) -> list[dict]:
    """Parse rendered room rows into structured observations.

    Returns one dict per displayed room category, in page order::

        {
          "room_category":       exact displayed name (spec §7.5),
          "status":              "ok" for listed rows (see sold-out note),
          "rooms_available":     int | None — observed numeric signal only,
          "availability_source": room_selector | scarcity_message
                                 | no_numeric_signal (spec §7.3),
          "min_price_inr":       int | None (stay total, taxes excluded),
          "room_type_id":        str | None — public data-room-id,
          "qty_selectable_max":  int | None — selector maximum (evidence),
          "is_capped":           True when the selector maximum was used,
          "scarcity_message":    exact matched wording | None,
          "taxes_extra_inr":     rounded "+₹N taxes" evidence | None,
          "error_detail":        str | None (caveats, spec §9),
        }

    Raises:
        StructureError: a room row exists but its name cannot be read —
            that is an unexpected page structure (spec §10), not a
            silently droppable row: losing a displayed category would
            under-report inventory.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(ROOM_ROW_SELECTOR)

    observations: list[dict] = []
    for index, row in enumerate(rows):
        link = row.select_one(NAME_LINK_SELECTOR)
        name_element = link.select_one(NAME_TEXT_SELECTOR) if link else None
        category = _clean(name_element.get_text()) if name_element else ""
        if not category:
            raise StructureError(
                f"Room row #{index + 1} has no readable room name "
                f"({NAME_LINK_SELECTOR} > {NAME_TEXT_SELECTOR} missing or empty). "
                "Booking.com page structure may have changed; refusing to "
                "silently drop a displayed category."
            )

        room_type_id = None
        if link is not None and link.get("data-room-id"):
            room_type_id = str(link.get("data-room-id"))

        qty_max = _max_selectable(row)
        price = _lowest_price(row)
        taxes = _taxes_evidence(row)
        scarcity_count = _scarcity_in_row(row)
        scarcity_text = None
        if scarcity_count is not None:
            match = SCARCITY_PATTERN.search(row.get_text(" ", strip=True))
            scarcity_text = match.group(0) if match else None

        # --- Evidence-based classification (spec §7.2) -------------------
        # Precedence: explicit scarcity number > selector maximum > none.
        # "sold_out" is intentionally absent — never observed (§8.2).
        error_detail = None
        if scarcity_count is not None:
            rooms_available: int | None = scarcity_count
            source = "scarcity_message"
            is_capped: bool | None = None
        elif qty_max is not None and qty_max >= 1:
            rooms_available = qty_max
            source = "room_selector"
            is_capped = True
        elif price is not None:
            rooms_available = None
            source = "no_numeric_signal"
            is_capped = None
        else:
            rooms_available = None
            source = "no_numeric_signal"
            is_capped = None
            error_detail = (
                "Listed room row without observed price, quantity selector "
                "or scarcity evidence; bookability unverified."
            )
            log.warning("%s: %s", category, error_detail)

        observations.append(
            {
                "room_category": category,
                "status": "ok",
                "rooms_available": rooms_available,
                "availability_source": source,
                "min_price_inr": price,
                "room_type_id": room_type_id,
                "qty_selectable_max": qty_max,
                "is_capped": is_capped,
                "scarcity_message": scarcity_text,
                "taxes_extra_inr": taxes,
                "error_detail": error_detail,
            }
        )

    log.debug("Parsed %d room row(s) from page HTML", len(observations))
    return observations
