"""Unit tests for listing verification (spec §3.1, §17.3, §14.1).

All tests run offline against a fake session — no Booking.com access.
The live verification run is performed separately (M4 live check).
"""

from types import SimpleNamespace

import pytest

from scraper.exceptions import BlockedError, PageTimeoutError, VerificationError
from scraper.hotel_finder import ListingVerification, names_match, verify_listing
from services.property_service import Property

URL = "https://www.booking.com/hotel/in/heaven-and-harmony-tapovan.en-gb.html"


def make_property(**overrides) -> Property:
    base = {
        "property_id": "heaven_and_harmony_tapovan",
        "property_name": "Around Stays-Heaven And Harmony",
        "location": "Tapovan, Rishikesh, India",
        "booking_url": URL,
        "booking_property_id": "17328907",
        "enabled": True,
    }
    base.update(overrides)
    return Property(**base)


class FakeLink:
    def __init__(self, href: str | None) -> None:
        self._href = href

    @property
    def first(self) -> "FakeLink":
        return self

    def count(self) -> int:
        return 0 if self._href is None else 1

    def get_attribute(self, name: str) -> str | None:
        return self._href if name == "href" else None


class FakePage:
    def __init__(self, title: str) -> None:
        self._title = title

    def title(self) -> str:
        return self._title

    def locator(self, selector: str) -> FakeLink:
        # Only the canonical link is faked; identity uses title/body only.
        return FakeLink("https://www.booking.com/hotel/in/heaven-and-harmony-tapovan.html")


class FakeSession:
    """Duck-typed stand-in for BrowserSession (goto/page/visible_text)."""

    def __init__(
        self,
        *,
        title: str = "",
        body: str = "",
        status: int = 200,
        goto_error: Exception | None = None,
        canonical: str | None = None,
    ) -> None:
        self._page = FakePage(title)
        if canonical is not None:
            self._page.locator = lambda selector: FakeLink(canonical)  # type: ignore[method-assign]
        self._body = body
        self._status = status
        self._goto_error = goto_error
        self.goto_calls: list[dict] = []

    @property
    def page(self) -> FakePage:
        return self._page

    def visible_text(self, **kwargs) -> str:
        return self._body

    def goto(self, url: str, **kwargs):
        self.goto_calls.append({"url": url, **kwargs})
        if self._goto_error is not None:
            raise self._goto_error
        return SimpleNamespace(status=self._status)


# --- Name normalization --------------------------------------------------------


def test_names_match_ignores_case_punctuation_and_spacing() -> None:
    assert names_match(
        "Around Stays-Heaven And Harmony",
        "AROUND STAYS – heaven and harmony, Rishikesh (India)",
    )


def test_names_match_requires_full_name_not_partial() -> None:
    assert not names_match("Around Stays-Heaven And Harmony", "Heaven And Harmony")


def test_names_match_rejects_empty_needle() -> None:
    assert not names_match("", "anything")
    assert not names_match("   ", "anything")


def test_names_match_rejects_mismatched_property() -> None:
    assert not names_match("Hotel Shiv Vilas", "Around Stays-Heaven And Harmony")


# --- verify_listing ------------------------------------------------------------


def test_verification_succeeds_via_title() -> None:
    session = FakeSession(
        title="Around Stays-Heaven And Harmony, Rishikesh (India) - Hotel - Booking.com"
    )
    result = verify_listing(make_property(), session)
    assert isinstance(result, ListingVerification)
    assert result.property_id == "heaven_and_harmony_tapovan"
    assert result.url == URL
    assert result.matched_via == "title"
    assert result.page_title.startswith("Around Stays")


def test_verification_falls_back_to_visible_text() -> None:
    """Some sites abbreviate titles; the visible page name still counts."""
    session = FakeSession(
        title="Hotel Page - Booking.com",
        body="Welcome\nAround Stays-Heaven And Harmony\n1, Badrinath Marg, Tapovan",
    )
    result = verify_listing(make_property(), session)
    assert result.matched_via == "visible_text"


def test_verification_uses_full_load_event() -> None:
    """Spec §6: identity is judged after the load event, not domcontentloaded."""
    session = FakeSession(title="Around Stays-Heaven And Harmony")
    verify_listing(make_property(), session)
    assert session.goto_calls[0]["wait_until"] == "load"
    assert session.goto_calls[0]["url"] == URL


def test_verification_reports_canonical_url_when_present() -> None:
    session = FakeSession(title="Around Stays-Heaven And Harmony")
    result = verify_listing(make_property(), session)
    assert result.canonical_url is not None
    assert "heaven-and-harmony-tapovan" in result.canonical_url


def test_verification_fails_when_name_absent_everywhere() -> None:
    session = FakeSession(
        title="Some Other Hotel - Booking.com",
        body="Completely different property in another city.",
    )
    with pytest.raises(VerificationError) as excinfo:
        verify_listing(make_property(), session)
    message = str(excinfo.value)
    assert "heaven_and_harmony_tapovan" in message
    assert "Around Stays-Heaven And Harmony" in message
    assert "Do NOT scrape" in message


def test_verification_fails_on_http_error_status() -> None:
    session = FakeSession(title="Around Stays-Heaven And Harmony", status=404)
    with pytest.raises(VerificationError, match="HTTP 404"):
        verify_listing(make_property(), session)


def test_verification_fails_without_booking_url() -> None:
    session = FakeSession(title="anything")
    with pytest.raises(VerificationError, match="no booking_url"):
        verify_listing(make_property(booking_url=None), session)


def test_blocked_error_propagates_untouched() -> None:
    """Spec §4: a block during verification must surface as BlockedError,
    never be swallowed or converted into a generic verification failure."""
    session = FakeSession(goto_error=BlockedError("CAPTCHA page", url=URL))
    with pytest.raises(BlockedError, match="CAPTCHA"):
        verify_listing(make_property(), session)


def test_timeout_error_propagates_untouched() -> None:
    session = FakeSession(goto_error=PageTimeoutError("too slow", url=URL))
    with pytest.raises(PageTimeoutError, match="too slow"):
        verify_listing(make_property(), session)
