"""Browser lifecycle, navigation timing and safe shutdown (spec §12).

Rules enforced here:

    * EVERY page load must pass through :class:`RateLimiter` via
      :meth:`BrowserSession.goto` — the assignment requires at least
      3 seconds between page loads, including retries and any extra
      navigation (spec §4, §10, §5.3). One limiter instance per session
      is the single choke point for that interval.
    * CAPTCHA / bot-protection / HTTP access blocks raise
      :class:`~scraper.exceptions.BlockedError`; callers must stop the
      affected activity, never bypass it (spec §4, §10).
    * The browser is closed cleanly on normal completion *and* on
      exceptions — use the session as a context manager (spec §10).

This module knows nothing about Booking.com room content; per-date
navigation + waiting for rendered data lives in ``booking_scraper.py``
(milestone M5).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Response
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from scraper.exceptions import (
    BlockedError,
    NavigationError,
    PageTimeoutError,
    ScraperError,
)

log = logging.getLogger("hotel_scraper.browser")

DEFAULT_NAVIGATION_TIMEOUT_MS = 30_000

# HTTP statuses treated as an access block (spec §10 "access blocks").
BLOCK_STATUS_CODES = frozenset({403, 429, 503})

# Heuristic VISIBLE-TEXT markers for CAPTCHA / bot protection. These only
# trigger *stopping* — they are never used for extraction, so no site
# selectors are assumed here. Refine against real observed pages in M5;
# false positives fail safe (the run stops and reports, per spec §10).
BLOCK_TEXT_MARKERS = (
    "confirm you are human",
    "verify you are human",
    "are you a robot",
    "unusual traffic",
    "captcha challenge",
)

# Challenge-page markers OBSERVED in the live block event of 2026-10-31:
# Booking.com served an AWS WAF interstitial (HTTP 202) with an empty
# <title> and no body text. Evidence taken from that real response — not
# invented (spec: never invent signals). Detected via page HTML because
# the challenge renders no visible text at all.
BLOCK_HTML_MARKERS = (
    "awsWafCookieDomainList",
    "/__challenge_",
    "reportChallengeError",
)


class RateLimiter:
    """Enforces a minimum interval between events (spec §4: >= 3 seconds).

    A single instance is shared by all navigations of a run — including
    retries (spec §5.3 requires one shared rate limiter, not a
    per-property or per-call timer).

    ``clock`` and ``sleep`` are injectable so unit tests assert spacing
    instantly without real waiting.
    """

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError(
                f"min_interval_seconds must be >= 0, got {min_interval_seconds}."
            )
        self._min_interval = float(min_interval_seconds)
        self._clock = clock
        self._sleep = sleep
        self._last_event: float | None = None

    @property
    def min_interval_seconds(self) -> float:
        return self._min_interval

    @property
    def last_event_elapsed_seconds(self) -> float | None:
        """Seconds since the last completed page load, or None before the first."""
        if self._last_event is None:
            return None
        return self._clock() - self._last_event

    def wait(self) -> float:
        """Block until a page load may START; return seconds slept (0.0 if none).

        Call immediately BEFORE each navigation. Does not record the event
        itself — call :meth:`mark` once the load attempt has finished, so
        at least ``min_interval_seconds`` of quiet time separates one load
        from the next (spec §4's "at least 3 seconds between page loads",
        under every reading of "between").
        """
        if self._last_event is None:
            return 0.0
        remaining = self._min_interval - (self._clock() - self._last_event)
        if remaining <= 0:
            return 0.0
        self._sleep(remaining)
        return remaining

    def mark(self) -> None:
        """Record that a page-load attempt has just completed.

        Call after ``page.goto`` returns *or* fails — an attempted load
        still counts for spacing, including failed attempts and retries.
        """
        self._last_event = self._clock()


class BrowserSession:
    """Owns the Playwright browser and the run's shared :class:`RateLimiter`.

    Usage::

        with BrowserSession(page_load_delay_seconds=3.0) as session:
            session.goto(url)   # rate-limited, block-checked
        # browser always closed here, even on exceptions
    """

    def __init__(
        self,
        page_load_delay_seconds: float,
        *,
        headless: bool = True,
        navigation_timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if navigation_timeout_ms <= 0:
            raise ValueError(
                f"navigation_timeout_ms must be > 0, got {navigation_timeout_ms}."
            )
        # The ONE shared limiter for this session's page loads (spec §5.3).
        self._limiter = RateLimiter(
            page_load_delay_seconds, clock=clock, sleep=sleep
        )
        self._headless = headless
        self._navigation_timeout_ms = int(navigation_timeout_ms)
        self._playwright = None
        self._browser = None
        self._page = None

    # --- Introspection ----------------------------------------------------

    @property
    def rate_limiter(self) -> RateLimiter:
        """The single shared rate limiter for all navigations of this run."""
        return self._limiter

    @property
    def is_running(self) -> bool:
        return self._page is not None

    @property
    def page(self):
        if self._page is None:
            raise ScraperError("Browser session is not started; call start() first.")
        return self._page

    # --- Lifecycle ---------------------------------------------------------

    def start(self) -> "BrowserSession":
        """Launch the browser (no-op if already running)."""
        if self._page is not None:
            return self
        log.debug("Starting browser (headless=%s)", self._headless)
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=self._headless)
            context = self._browser.new_context()
            self._page = context.new_page()
        except Exception:
            # Launch failed halfway — release whatever was acquired.
            self.close()
            raise
        return self

    def close(self) -> None:
        """Idempotent safe shutdown; never raises (spec §10)."""
        browser, self._browser = self._browser, None
        playwright, self._playwright = self._playwright, None
        self._page = None
        try:
            if browser is not None:
                browser.close()
        except Exception as exc:  # noqa: BLE001 — shutdown must not raise
            log.warning("Ignoring error while closing browser: %s", exc)
        finally:
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Ignoring error while stopping Playwright: %s", exc)

    def __enter__(self) -> "BrowserSession":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False  # never swallow exceptions

    # --- Navigation --------------------------------------------------------

    def goto(
        self,
        url: str,
        *,
        timeout_ms: int | None = None,
        wait_until: str = "domcontentloaded",
    ) -> Response | None:
        """Navigate to ``url`` after the shared rate-limit wait.

        Distinguishes failures per spec §10:

            * :class:`PageTimeoutError` — load exceeded the timeout
            * :class:`NavigationError`   — the page could not be opened
            * :class:`BlockedError`      — HTTP block status or
              CAPTCHA/bot-protection text (STOP — do not bypass)

        A successful navigation does NOT mean page content (e.g. room
        data) has rendered — waiting for that is M5's responsibility
        (spec §6).
        """
        page = self.page  # raises ScraperError if not started
        timeout = self._navigation_timeout_ms if timeout_ms is None else int(timeout_ms)

        # Rate limit FIRST: the previous load must be >= interval ago.
        slept = self._limiter.wait()
        if slept:
            log.debug("Rate limiter slept %.1fs before navigation", slept)

        log.debug("Navigating (timeout=%dms): %s", timeout, url)
        try:
            response = page.goto(url, timeout=timeout, wait_until=wait_until)
        except PlaywrightTimeoutError as exc:
            raise PageTimeoutError(
                f"Navigation timed out after {timeout}ms: {url}", url=url
            ) from exc
        except PlaywrightError as exc:
            raise NavigationError(f"Navigation failed: {url} ({exc})", url=url) from exc
        finally:
            # Success OR failure: the load attempt happened, so restart the
            # spacing clock — retries must respect the interval too (spec §4).
            self._limiter.mark()

        self._raise_if_blocked(response, url)
        return response

    # --- Block detection ----------------------------------------------------

    def _raise_if_blocked(self, response: Response | None, url: str) -> None:
        if response is not None and response.status in BLOCK_STATUS_CODES:
            raise BlockedError(
                f"Access block: HTTP {response.status} from {url} — stopping "
                "this activity per assignment rules (no bypass attempts).",
                url=url,
            )
        # Challenge pages often render NO visible text (observed 2026-10-31),
        # so the HTML itself is checked for the observed WAF markers first.
        html = self._page_html()
        for marker in BLOCK_HTML_MARKERS:
            if marker in html:
                status = response.status if response is not None else "?"
                raise BlockedError(
                    f"Bot-protection challenge page from {url} (HTTP {status}, "
                    f"HTML matched {marker!r}) — stopping per assignment rules "
                    "(no bypass attempts).",
                    url=url,
                )
        visible = self.visible_text(timeout_ms=2_000).lower()
        for marker in BLOCK_TEXT_MARKERS:
            if marker in visible:
                raise BlockedError(
                    f"Possible CAPTCHA/bot protection on {url} (page text "
                    f"matched {marker!r}) — stopping per assignment rules.",
                    url=url,
                )

    def _page_html(self) -> str:
        """Full page HTML; '' when the DOM cannot be read."""
        try:
            return self.page.content()
        except PlaywrightError:
            return ""

    def visible_text(self, *, timeout_ms: int = 5_000) -> str:
        """Visible body text; '' when the page has no readable body.

        Uses only standard HTML semantics (``<body>``) — no site-specific
        selectors, so it works for verification and block detection alike.
        """
        try:
            return self.page.locator("body").inner_text(timeout=timeout_ms)
        except PlaywrightError:
            return ""
