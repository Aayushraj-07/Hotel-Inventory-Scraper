"""Unit tests for the shared rate limiter, browser-session wiring and
scraper failure categories (spec §14.1: "A shared rate limiter enforces
the minimum delay across page navigations"; spec §10 failure categories).

No live browser and no network access required — clocks and sleeps are
faked. The real-browser smoke check is performed separately (M3 manual
verification against a neutral site, not Booking.com).
"""

import pytest

from scraper.browser import BrowserSession, RateLimiter
from scraper.exceptions import (
    BlockedError,
    MissingDataError,
    NavigationError,
    PageTimeoutError,
    ScraperError,
    StructureError,
)


class FakeTime:
    """Deterministic clock + sleep: sleeping advances the fake time."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, "sleep must never be called with negative duration"
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- RateLimiter ----------------------------------------------------------------


def test_first_event_is_not_delayed() -> None:
    fake = FakeTime()
    limiter = RateLimiter(3.0, clock=fake.clock, sleep=fake.sleep)
    assert limiter.wait() == 0.0
    assert fake.sleeps == []


def test_wait_does_not_record_the_event_by_itself() -> None:
    """Gate-only: only mark() records a completed load."""
    fake = FakeTime()
    limiter = RateLimiter(3.0, clock=fake.clock, sleep=fake.sleep)
    assert limiter.wait() == 0.0
    assert limiter.wait() == 0.0  # still nothing recorded -> no sleep
    assert fake.sleeps == []


def test_load_within_interval_waits_the_remainder() -> None:
    fake = FakeTime()
    limiter = RateLimiter(3.0, clock=fake.clock, sleep=fake.sleep)
    limiter.mark()            # a load just completed at t=1000
    fake.advance(1.0)         # 1s later we try the next load
    slept = limiter.wait()
    assert slept == pytest.approx(2.0)
    assert fake.sleeps == [pytest.approx(2.0)]


def test_load_after_interval_is_not_delayed() -> None:
    fake = FakeTime()
    limiter = RateLimiter(3.0, clock=fake.clock, sleep=fake.sleep)
    limiter.mark()
    fake.advance(3.5)         # longer than the interval
    assert limiter.wait() == 0.0
    assert fake.sleeps == []


def test_load_exactly_at_interval_is_not_delayed() -> None:
    fake = FakeTime()
    limiter = RateLimiter(3.0, clock=fake.clock, sleep=fake.sleep)
    limiter.mark()
    fake.advance(3.0)
    assert limiter.wait() == 0.0


def test_quiet_time_between_load_completion_and_next_start() -> None:
    """Spec §4: >= 3s of quiet time between one load ending and the next
    starting — the strictest reading of 'between page loads'."""
    fake = FakeTime()
    limiter = RateLimiter(3.0, clock=fake.clock, sleep=fake.sleep)
    limiter.wait()
    fake.advance(0.05)
    limiter.mark()                      # load 1 completes at 1000.05
    completion = fake.now
    limiter.wait()                      # gate for load 2
    assert fake.now - completion >= 3.0 - 1e-9


def test_retries_are_rate_limited_too() -> None:
    """Spec §4: the delay applies to ALL page loads, including retries."""
    fake = FakeTime()
    limiter = RateLimiter(3.0, clock=fake.clock, sleep=fake.sleep)
    start_times: list[float] = []
    completion_times: list[float] = []

    # Three attempts for one date: initial + 2 retries, each "failing fast".
    for _ in range(3):
        limiter.wait()          # gate the attempt
        start_times.append(fake.now)
        fake.advance(0.1)       # the load attempt (fails) happens quickly
        completion_times.append(fake.now)
        limiter.mark()          # failed loads still count

    # Starts are >= 3s apart (initiation spacing) ...
    for earlier, later in zip(start_times, start_times[1:]):
        assert later - earlier >= 3.0 - 1e-9
    # ... completions are >= 3s apart ...
    for earlier, later in zip(completion_times, completion_times[1:]):
        assert later - earlier >= 3.0 - 1e-9
    # ... and each retry waited a full interval after the previous failure.
    assert fake.sleeps == [pytest.approx(3.0), pytest.approx(3.0)]


def test_spacing_is_enforced_across_many_loads() -> None:
    fake = FakeTime()
    limiter = RateLimiter(3.0, clock=fake.clock, sleep=fake.sleep)
    start_times: list[float] = []
    for _ in range(5):
        limiter.wait()
        start_times.append(fake.now)
        fake.advance(0.05)      # load duration
        limiter.mark()
    for earlier, later in zip(start_times, start_times[1:]):
        assert later - earlier >= 3.0 - 1e-9


def test_negative_interval_rejected() -> None:
    with pytest.raises(ValueError, match="min_interval"):
        RateLimiter(-1.0)


def test_last_event_elapsed_is_none_before_first_mark() -> None:
    fake = FakeTime()
    limiter = RateLimiter(3.0, clock=fake.clock, sleep=fake.sleep)
    assert limiter.last_event_elapsed_seconds is None
    limiter.mark()
    fake.advance(1.5)
    assert limiter.last_event_elapsed_seconds == pytest.approx(1.5)


# --- BrowserSession wiring (no launch) ------------------------------------------


def test_session_owns_one_shared_limiter_with_configured_interval() -> None:
    session = BrowserSession(3.0)
    # One shared instance for every navigation of the run (spec §5.3).
    assert session.rate_limiter is session.rate_limiter
    assert session.rate_limiter.min_interval_seconds == 3.0


def test_sessions_do_not_share_limiters() -> None:
    """A run creates one session; separate sessions must not pool timing."""
    first = BrowserSession(3.0)
    second = BrowserSession(3.0)
    assert first.rate_limiter is not second.rate_limiter


def test_page_access_before_start_raises() -> None:
    session = BrowserSession(3.0)
    assert session.is_running is False
    with pytest.raises(ScraperError, match="not started"):
        _ = session.page


def test_goto_before_start_raises() -> None:
    session = BrowserSession(3.0)
    with pytest.raises(ScraperError, match="not started"):
        session.goto("https://example.com/")


def test_close_without_start_is_safe() -> None:
    BrowserSession(3.0).close()  # must not raise


def test_invalid_timeout_rejected() -> None:
    with pytest.raises(ValueError, match="navigation_timeout_ms"):
        BrowserSession(3.0, navigation_timeout_ms=0)


# --- Failure categories (spec §10) ------------------------------------------------


def test_all_failures_share_the_scraper_base_type() -> None:
    for cls in (NavigationError, PageTimeoutError, StructureError,
                MissingDataError, BlockedError):
        assert issubclass(cls, ScraperError)


def test_failure_categories_are_distinct() -> None:
    categories = {
        NavigationError: "navigation",
        PageTimeoutError: "timeout",
        StructureError: "unexpected_structure",
        MissingDataError: "missing_data",
        BlockedError: "blocked",
    }
    for cls, expected in categories.items():
        assert cls.category == expected
        assert cls("msg").category == expected


def test_only_blocked_errors_are_non_retryable() -> None:
    """Spec §4/§10: CAPTCHA/bot protection must stop, never be retried."""
    assert BlockedError("stop").retryable is False
    for cls in (NavigationError, PageTimeoutError, StructureError, MissingDataError):
        assert cls("retry me").retryable is True


def test_error_carries_url_context() -> None:
    exc = NavigationError("failed", url="https://example.com/")
    assert exc.url == "https://example.com/"
    assert "failed" in str(exc)


# --- Block detection (evidence from the real 2026-10-31 WAF event) -------------
#
# These tests use a fake DOM injected into a BrowserSession to exercise
# _raise_if_blocked offline — no network access.


class FakeBodyLocator:
    def __init__(self, text: str) -> None:
        self._text = text

    def inner_text(self, timeout: float | None = None) -> str:
        return self._text


class FakeDomPage:
    def __init__(self, html: str = "", body_text: str = "") -> None:
        self._html = html
        self._body_text = body_text

    def content(self) -> str:
        return self._html

    def locator(self, selector: str) -> FakeBodyLocator:
        return FakeBodyLocator(self._body_text)


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


def session_with_dom(html: str = "", body_text: str = "") -> BrowserSession:
    session = BrowserSession(3.0)
    session._page = FakeDomPage(html=html, body_text=body_text)  # noqa: SLF001
    return session


# The exact challenge page shape observed on 2026-10-31 (trimmed).
WAF_CHALLENGE_HTML = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
    "<title></title></head><body></body>"
    "<script>window.awsWafCookieDomainList = ['booking.com'];</script>"
    '<script src="https://www.booking.com/__challenge_h78IRKX3kpQxScCExxShBN"></script>'
)


def test_waf_challenge_html_detected_as_block() -> None:
    session = session_with_dom(html=WAF_CHALLENGE_HTML)
    with pytest.raises(BlockedError, match="challenge"):
        session._raise_if_blocked(FakeResponse(202), "https://www.booking.com/x")


def test_status_202_alone_is_not_a_block() -> None:
    """HTTP 202 is a legitimate status — only the challenge content blocks."""
    session = session_with_dom(
        html="<html><head><title>Hotel</title></head><body>Fine</body></html>",
        body_text="Fine",
    )
    session._raise_if_blocked(FakeResponse(202), "https://example.com/")  # no raise


def test_http_403_detected_as_block() -> None:
    session = session_with_dom(html="<html><body>denied</body></html>",
                               body_text="denied")
    with pytest.raises(BlockedError, match="Access block"):
        session._raise_if_blocked(FakeResponse(403), "https://example.com/")


def test_visible_challenge_text_detected_as_block() -> None:
    session = session_with_dom(html="<html><body>…</body></html>",
                               body_text="Please confirm you are human to continue")
    with pytest.raises(BlockedError, match="human"):
        session._raise_if_blocked(FakeResponse(200), "https://example.com/")


def test_normal_page_passes_block_check() -> None:
    session = session_with_dom(
        html="<html><head><title>T</title></head><body>Rooms available</body></html>",
        body_text="Rooms available",
    )
    session._raise_if_blocked(FakeResponse(200), "https://example.com/")  # no raise


def test_unreadable_dom_fails_open_to_text_check() -> None:
    """If content() is unavailable, the visible-text marker check still runs."""
    session = BrowserSession(3.0)

    class ExplodingPage:
        def content(self):
            from playwright.sync_api import Error as PlaywrightError
            raise PlaywrightError("detached")

        def locator(self, selector):
            return FakeBodyLocator("confirm you are human")

    session._page = ExplodingPage()  # noqa: SLF001
    with pytest.raises(BlockedError, match="human"):
        session._raise_if_blocked(FakeResponse(200), "https://example.com/")
