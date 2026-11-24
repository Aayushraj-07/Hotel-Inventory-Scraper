"""Unit tests for centralized access-state classification (assignment §4).

Offline only — pure functions, no browser, no network. Covers:

    * the 12-state vocabulary and blocking-state set,
    * multi-indicator classification (authoritative statuses; observed WAF
      evidence; two-independent-source minimum for content heuristics),
    * exception -> state mapping for the retry policy (stage 3),
    * URL/text redaction and the per-attempt :class:`AccessDiagnostic`.
"""

from __future__ import annotations

import pytest

from scraper.access_state import (
    BLOCKING_STATES,
    CAPTCHA_TEXT_MARKERS,
    WAF_HTML_MARKERS,
    AccessDiagnostic,
    AccessState,
    classify_access_state,
    redact_text,
    redact_url,
    state_for_exception,
)
from scraper.exceptions import (
    BlockedError,
    MissingDataError,
    NavigationError,
    PageTimeoutError,
    ScraperError,
    StructureError,
    VerificationError,
)

# --- Vocabulary ---------------------------------------------------------------


def test_twelve_states_exactly() -> None:
    expected = {
        "NETWORK_ERROR",
        "NAVIGATION_TIMEOUT",
        "HTTP_ACCESS_DENIED",
        "BOT_PROTECTION",
        "CAPTCHA_PRESENT",
        "AWS_WAF_CHALLENGE",
        "PROPERTY_NOT_FOUND",
        "PROPERTY_MISMATCH",
        "PAGE_STRUCTURE_CHANGED",
        "ROOM_DATA_NOT_LOADED",
        "EXTRACTION_ERROR",
        "UNKNOWN_ERROR",
    }
    assert {state.value for state in AccessState} == expected


def test_blocking_states_are_only_the_stop_set() -> None:
    """Verification/parse states are NOT blocks — they don't stop the run."""
    assert BLOCKING_STATES == frozenset(
        {
            AccessState.HTTP_ACCESS_DENIED,
            AccessState.BOT_PROTECTION,
            AccessState.CAPTCHA_PRESENT,
            AccessState.AWS_WAF_CHALLENGE,
        }
    )
    assert AccessState.PROPERTY_NOT_FOUND not in BLOCKING_STATES
    assert AccessState.ROOM_DATA_NOT_LOADED not in BLOCKING_STATES


def test_states_are_strings_for_logging() -> None:
    assert AccessState.AWS_WAF_CHALLENGE.value == "AWS_WAF_CHALLENGE"
    assert f"{AccessState.CAPTCHA_PRESENT.value}" == "CAPTCHA_PRESENT"


# --- Authoritative HTTP statuses ----------------------------------------------


@pytest.mark.parametrize("status", [401, 403, 407, 429, 503])
def test_denied_statuses_classify_alone(status: int) -> None:
    result = classify_access_state(http_status=status)
    assert result.state is AccessState.HTTP_ACCESS_DENIED
    assert result.indicators == (f"HTTP {status}",)


@pytest.mark.parametrize("status", [404, 410])
def test_not_found_statuses_classify_alone(status: int) -> None:
    result = classify_access_state(http_status=status)
    assert result.state is AccessState.PROPERTY_NOT_FOUND


def test_ok_status_with_content_signals_still_considered() -> None:
    """A 200 does NOT mask CAPTCHA content — statuses are inputs, not gates."""
    result = classify_access_state(
        http_status=200,
        html="<title>captcha</title><div class='g-recaptcha'></div>",
        title="captcha",
        visible_text="please confirm you are human",
    )
    assert result.state is AccessState.CAPTCHA_PRESENT


def test_missing_status_runs_content_checks() -> None:
    result = classify_access_state(html=WAF_CHALLENGE_HTML, title="", visible_text="")
    assert result.state is AccessState.AWS_WAF_CHALLENGE


# --- AWS WAF (observed evidence 2026-10-31) -----------------------------------


WAF_CHALLENGE_HTML = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
    "<title></title></head><body></body>"
    "<script>window.awsWafCookieDomainList = ['booking.com'];</script>"
    '<script src="https://www.booking.com/__challenge_h78IRKX3kpQxScCExxShBN"></script>'
)


def test_waf_202_plus_marker_classifies() -> None:
    result = classify_access_state(
        http_status=202, html=WAF_CHALLENGE_HTML, title="", visible_text=""
    )
    assert result.state is AccessState.AWS_WAF_CHALLENGE
    assert "HTTP 202" in result.indicators
    assert any("challenge" in i for i in result.indicators)


def test_waf_two_markers_without_202_classifies() -> None:
    html = "<script>x.awsWafCookieDomainList=[]</script><script src='/__challenge_1'></script>"
    result = classify_access_state(http_status=200, html=html, title="T", visible_text="Long body")
    assert result.state is AccessState.AWS_WAF_CHALLENGE


def test_single_waf_marker_on_normal_page_does_not_classify() -> None:
    """One marker alone with no corroboration is not enough (§4)."""
    html = "<title>Hotel</title><script>x.awsWafCookieDomainList=[]</script><body>" + ("Room data " * 60) + "</body>"
    result = classify_access_state(http_status=200, html=html, title="Hotel", visible_text="Room data " * 60)
    assert result.state is None


def test_single_waf_marker_with_interstitial_shape_classifies() -> None:
    html = "<title></title><body></body><script src='/__challenge_1'></script>"
    result = classify_access_state(http_status=200, html=html, title="", visible_text="")
    assert result.state is AccessState.AWS_WAF_CHALLENGE


def test_202_alone_is_not_a_block() -> None:
    result = classify_access_state(
        http_status=202,
        html="<html><head><title>Hotel</title></head><body>Fine</body></html>",
        title="Hotel",
        visible_text="Fine",
    )
    assert result.state is None


def test_waf_markers_are_the_observed_set() -> None:
    assert "awsWafCookieDomainList" in WAF_HTML_MARKERS
    assert "/__challenge_" in WAF_HTML_MARKERS
    assert "reportChallengeError" in WAF_HTML_MARKERS


# --- CAPTCHA / bot protection: multi-indicator --------------------------------


def test_single_text_marker_on_normal_page_is_not_classified() -> None:
    """The assignment's key rule: one unreliable text match never blocks."""
    body = "Our FAQ explains how to confirm you are human when logging in. " * 10
    html = f"<html><head><title>Help</title></head><body>{body}</body></html>"
    result = classify_access_state(http_status=200, html=html, title="Help", visible_text=body)
    assert result.state is None
    assert result.indicators == ()


def test_text_marker_plus_interstitial_shape_classifies_as_captcha() -> None:
    result = classify_access_state(
        http_status=200,
        html="<html><body>Please confirm you are human to continue</body></html>",
        title="",
        visible_text="Please confirm you are human to continue",
    )
    assert result.state is AccessState.CAPTCHA_PRESENT
    assert any("human" in i for i in result.indicators)


def test_captcha_markup_plus_marker_classifies() -> None:
    result = classify_access_state(
        http_status=200,
        html="<title>Verify</title><div class='g-recaptcha'></div><body>verify you are human</body>",
        title="Verify",
        visible_text="verify you are human",
    )
    assert result.state is AccessState.CAPTCHA_PRESENT
    assert any("markup" in i for i in result.indicators)


def test_bot_text_with_corroboration_is_bot_protection() -> None:
    result = classify_access_state(
        http_status=200,
        html="<html><body>unusual traffic</body></html>",
        title="",
        visible_text="unusual traffic",
    )
    assert result.state is AccessState.BOT_PROTECTION


def test_captcha_text_markers_listed() -> None:
    assert "confirm you are human" in CAPTCHA_TEXT_MARKERS
    assert "unusual traffic" not in CAPTCHA_TEXT_MARKERS  # bot family, not captcha


def test_normal_property_page_is_never_classified() -> None:
    body = "Around Stays-Heaven And Harmony\nDeluxe Room\nINR 3,500\n" * 20
    html = f"<html><head><title>Heaven and Harmony</title></head><body>{body}</body></html>"
    result = classify_access_state(
        http_status=200, html=html, title="Heaven and Harmony", visible_text=body
    )
    assert result.state is None


def test_empty_page_without_markers_is_not_classified() -> None:
    """Empty title + empty body alone is NOT a state — no marker fired."""
    result = classify_access_state(http_status=200, html="", title="", visible_text="")
    assert result.state is None


# --- Exception -> state mapping ------------------------------------------------


def test_exception_state_mapping() -> None:
    assert state_for_exception(NavigationError("x")) is AccessState.NETWORK_ERROR
    assert state_for_exception(PageTimeoutError("x")) is AccessState.NAVIGATION_TIMEOUT
    assert state_for_exception(StructureError("x")) is AccessState.PAGE_STRUCTURE_CHANGED
    assert state_for_exception(MissingDataError("x")) is AccessState.ROOM_DATA_NOT_LOADED
    assert state_for_exception(ValueError("boom")) is AccessState.EXTRACTION_ERROR
    assert state_for_exception(RuntimeError("?")) is AccessState.UNKNOWN_ERROR
    assert state_for_exception(BlockedError("x")) is AccessState.BOT_PROTECTION


def test_carried_state_wins_over_class_fallback() -> None:
    exc = BlockedError("x", access_state=AccessState.AWS_WAF_CHALLENGE)
    assert state_for_exception(exc) is AccessState.AWS_WAF_CHALLENGE
    ver = VerificationError("x", access_state=AccessState.PROPERTY_MISMATCH)
    assert state_for_exception(ver) is AccessState.PROPERTY_MISMATCH


def test_all_scraper_errors_have_a_state() -> None:
    for exc in (
        NavigationError("x"),
        PageTimeoutError("x"),
        StructureError("x"),
        MissingDataError("x"),
        BlockedError("x"),
        VerificationError("x"),
        ScraperError("x"),
    ):
        assert isinstance(state_for_exception(exc), AccessState)


# --- Redaction -----------------------------------------------------------------


def test_redact_url_keeps_allowlisted_params_only() -> None:
    url = (
        "https://www.booking.com/hotel/in/x.html"
        "?checkin=2026-11-24&checkout=2026-11-25&group_adults=2"
        "&token=SECRET123&session=SESS&email=a@b.com"
    )
    out = redact_url(url)
    assert "checkin=2026-11-24" in out
    assert "group_adults=2" in out
    assert "SECRET123" not in out
    assert "SESS" not in out
    assert "email=a@b.com" not in out
    assert out.count("redacted") == 3


def test_redact_url_strips_userinfo() -> None:
    out = redact_url("https://user:password@example.com/p?a=1")
    assert "password" not in out
    assert "user" not in out.split("//", 1)[1].split("/", 1)[0]
    assert out.startswith("https://example.com/p?")


def test_redact_url_handles_empty_and_unparseable() -> None:
    assert redact_url("") == ""
    assert "://" in redact_url("https://example.com/ok?a=1")


def test_redact_text_scrubs_embedded_urls_and_kv_pairs() -> None:
    text = "Navigation failed: https://x.com/p?token=SECRET&q=1 (err) session=abc"
    out = redact_text(text)
    assert "SECRET" not in out
    assert "session=[redacted]" in out
    assert "Navigation failed" in out  # context preserved
    assert redact_text("") == ""


# --- AccessDiagnostic -----------------------------------------------------------


def test_diagnostic_captures_required_fields() -> None:
    diag = AccessDiagnostic(
        attempt=2,
        requested_url="https://www.booking.com/hotel/in/x.html?checkin=2026-11-24&token=S",
        final_url="https://www.booking.com/hotel/in/x.html?token=S2",
        http_status=202,
        page_title="",
        navigation_error=None,
        access_state=AccessState.AWS_WAF_CHALLENGE,
        indicators=("HTTP 202", "awsWafCookieDomainList"),
        room_info_present=False,
        extraction_succeeded=False,
    )
    d = diag.to_log_dict()
    assert d["attempt"] == 2
    assert d["http_status"] == 202
    assert d["access_state"] == "AWS_WAF_CHALLENGE"
    assert d["room_info_present"] is False
    assert d["extraction_succeeded"] is False
    assert "checkin=2026-11-24" in str(d["requested_url"])
    assert "token=S&" not in str(d["requested_url"]).replace("token=redacted", "")
    assert "S2" not in str(d["final_url"])
    assert d["indicators"] == ["HTTP 202", "awsWafCookieDomainList"]


def test_diagnostic_redacts_navigation_error_and_title() -> None:
    diag = AccessDiagnostic(
        attempt=1,
        requested_url="https://example.com/",
        page_title="Hotel?session=abc",
        navigation_error="net::ERR from https://x.com/p?api_key=ZZZ",
    )
    assert "abc" not in diag.page_title
    assert "ZZZ" not in diag.navigation_error


def test_diagnostic_is_mutable_and_serializable() -> None:
    diag = AccessDiagnostic(attempt=1, requested_url="https://example.com/")
    assert diag.http_status is None
    assert diag.access_state is None
    diag.access_state = AccessState.NETWORK_ERROR
    assert diag.to_log_dict()["access_state"] == "NETWORK_ERROR"
