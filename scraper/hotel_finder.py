"""Locate and verify the correct public property listing (spec §3.1, §12).

Runs once per execution as an automated startup check (approved decision
7): load the configured ``booking_url`` through the rate-limited
:class:`~scraper.browser.BrowserSession` and confirm the page really
belongs to the configured property **before** any date is processed
(spec §13 step 4).

Honesty rules honoured here:

    * No invented site selectors — matching relies only on standard HTML
      signals: the document title, visible body text, and (optionally,
      log-only) the standard ``<link rel="canonical">`` element.
    * On any mismatch the run stops with a clear error. An unverified
      listing must never be scraped (spec §3.1, §5.2).
    * CAPTCHA/bot protection surfaces as
      :class:`~scraper.exceptions.BlockedError` from ``goto`` and is
      never bypassed (spec §4).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from playwright.sync_api import Error as PlaywrightError

from scraper.browser import BrowserSession
from scraper.exceptions import VerificationError

if TYPE_CHECKING:
    from services.property_service import Property

log = logging.getLogger("hotel_scraper.hotel_finder")


def _normalize(text: str) -> str:
    """Casefold and collapse every non-alphanumeric run to one space.

    Makes "Around Stays-Heaven And Harmony" match "Around Stays – Heaven
    and Harmony" (punctuation/spacing/case differences only — wording is
    never rewritten).
    """
    return re.sub(r"[^0-9a-z]+", " ", text.casefold()).strip()


def names_match(configured: str, observed: str) -> bool:
    """True when the configured name appears in the observed text."""
    needle = _normalize(configured)
    if not needle:
        return False
    return needle in _normalize(observed)


@dataclass(frozen=True)
class ListingVerification:
    """Evidence that the loaded page is the configured property."""

    property_id: str
    url: str
    page_title: str
    matched_via: str  # "title" or "visible_text"
    canonical_url: str | None = None


def _canonical_url(session: BrowserSession) -> str | None:
    """Standard <link rel="canonical"> href, when the page provides one."""
    try:
        link = session.page.locator('link[rel="canonical"]')
        if link.count() == 0:
            return None
        return link.first.get_attribute("href")
    except PlaywrightError:
        return None


def verify_listing(
    record: "Property",
    session: BrowserSession,
    *,
    timeout_ms: int | None = None,
) -> ListingVerification:
    """Verify that ``record.booking_url`` shows ``record.property_name``.

    Raises:
        VerificationError: no URL, HTTP error status, or the configured
            property name is absent from both the title and visible text.
        BlockedError / PageTimeoutError / NavigationError: propagated
            unchanged from :meth:`BrowserSession.goto`.
    """
    url = record.booking_url
    if not url:
        raise VerificationError(
            f"Property '{record.property_id}' has no booking_url to verify — "
            "set it from the real public listing found in M0 (never guess)."
        )

    # wait_until="load": wait for the full load event so title and body
    # text are settled before we judge identity (this is one startup load,
    # not the per-date hot path).
    response = session.goto(url, wait_until="load", timeout_ms=timeout_ms)

    if response is not None and response.status >= 400:
        raise VerificationError(
            f"Listing for '{record.property_id}' at {url} returned "
            f"HTTP {response.status} — cannot verify identity."
        )

    title = session.page.title() or ""
    body = session.visible_text()

    if names_match(record.property_name, title):
        matched_via = "title"
    elif names_match(record.property_name, body):
        matched_via = "visible_text"
    else:
        raise VerificationError(
            f"Listing verification FAILED for '{record.property_id}': configured "
            f"name {record.property_name!r} does not appear in the page title or "
            f"visible text (title was: {title.strip()!r}). The URL likely points "
            "to a different or changed listing. Do NOT scrape an unverified "
            "listing — re-run manual M0 verification and update the config."
        )

    verification = ListingVerification(
        property_id=record.property_id,
        url=url,
        page_title=title.strip(),
        matched_via=matched_via,
        canonical_url=_canonical_url(session),
    )
    log.debug(
        "Listing identity confirmed | property_id=%s | via=%s",
        verification.property_id,
        verification.matched_via,
    )
    return verification
