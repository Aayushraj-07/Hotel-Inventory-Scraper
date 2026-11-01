"""Typed failure categories for the scraper layer (spec §10).

``services/inventory_service.py`` (milestone M6) uses these types to apply
the retry policy:

    * transient categories (navigation, timeout, structure, missing data)
      -> retry up to the configured attempt limit (3 total per date)
    * ``BlockedError`` (CAPTCHA / bot protection / access block)
      -> STOP the affected activity immediately; never retry in a way that
         attempts to bypass the protection (spec §4, §10)

Each class exposes ``category`` for structured logging (spec §10: "log the
date, attempt number, failure category and concise error details").
"""

from __future__ import annotations


class ScraperError(Exception):
    """Base class for all scraper-layer failures."""

    category = "unknown"
    retryable = True

    def __init__(self, message: str, *, url: str | None = None) -> None:
        super().__init__(message)
        self.url = url


class NavigationError(ScraperError):
    """The page could not be opened (DNS, connection, refused, etc.)."""

    category = "navigation"


class PageTimeoutError(ScraperError):
    """The page did not load within the allowed time."""

    category = "timeout"


class StructureError(ScraperError):
    """The page loaded but its structure was not what the parser expects."""

    category = "unexpected_structure"


class MissingDataError(ScraperError):
    """The page loaded but expected data (e.g. room content) was absent."""

    category = "missing_data"


class BlockedError(ScraperError):
    """CAPTCHA, bot protection or another access block was detected.

    Never retry around it (spec §4). Log it, record an ``error`` row for
    the affected date, and explain the event in the README/run report.
    """

    category = "blocked"
    retryable = False


class VerificationError(ScraperError):
    """The configured listing could not be verified as the right property.

    Raised by startup verification (spec §13 step 4): the URL failed to
    respond, or the loaded page does not carry the configured property
    name. Never scrape an unverified listing — fix the configuration via
    manual M0 verification instead. Not retried automatically.
    """

    category = "verification"
    retryable = False
