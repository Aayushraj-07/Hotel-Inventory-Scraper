"""Centralized access-state classification and fetch diagnostics (assignment §4).

Single source of truth for *how* a fetch or verification went, replacing the
scattered block heuristics that previously lived inside
``scraper/browser.py``. Three responsibilities:

    1. :class:`AccessState` — the 12-state vocabulary used across logging,
       retry policy and CSV error rows.
    2. :func:`classify_access_state` — multi-indicator classification of a
       page response (HTTP status + HTML + visible text + title).
    3. :class:`AccessDiagnostic` + the ``redact_*`` helpers — the per-attempt
       diagnostic record the assignment requires, with sensitive query
       parameters excluded.

Honesty rules (assignment §4/§8):

    * HTTP status codes are authoritative on their own.
    * Text/markup findings are NOT sufficient alone — a classification from
      page content requires at least two independent indicator sources
      (visible-text markers, captcha markup, title, interstitial page
      shape). One unreliable text match on a normal page never triggers a
      block.
    * Observed evidence is labelled as such; heuristic markers are labelled
      as heuristic. Nothing here claims to bypass any protection — every
      classified state exists to STOP the affected activity and report.
    * Nothing captured in a diagnostic contains cookies, headers or
      non-allowlisted query parameters (``redact_url`` / ``redact_text``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from scraper.exceptions import (
    BlockedError,
    MissingDataError,
    NavigationError,
    PageTimeoutError,
    ScraperError,
    StructureError,
    VerificationError,
)

log = logging.getLogger("hotel_scraper.access_state")


class AccessState(str, Enum):
    """The 12 access/failure states recognized by the assignment (§4)."""

    NETWORK_ERROR = "NETWORK_ERROR"
    NAVIGATION_TIMEOUT = "NAVIGATION_TIMEOUT"
    HTTP_ACCESS_DENIED = "HTTP_ACCESS_DENIED"
    BOT_PROTECTION = "BOT_PROTECTION"
    CAPTCHA_PRESENT = "CAPTCHA_PRESENT"
    AWS_WAF_CHALLENGE = "AWS_WAF_CHALLENGE"
    PROPERTY_NOT_FOUND = "PROPERTY_NOT_FOUND"
    PROPERTY_MISMATCH = "PROPERTY_MISMATCH"
    PAGE_STRUCTURE_CHANGED = "PAGE_STRUCTURE_CHANGED"
    ROOM_DATA_NOT_LOADED = "ROOM_DATA_NOT_LOADED"
    EXTRACTION_ERROR = "EXTRACTION_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# States where the ONLY honest action is to stop the affected activity —
# never retry around them (assignment §8, spec §4/§10).
BLOCKING_STATES = frozenset(
    {
        AccessState.HTTP_ACCESS_DENIED,
        AccessState.BOT_PROTECTION,
        AccessState.CAPTCHA_PRESENT,
        AccessState.AWS_WAF_CHALLENGE,
    }
)

# --- Classification inputs ----------------------------------------------------

# HTTP statuses that DENY access — authoritative on their own (401/403/407
# authentication/authorization, 429 rate limiting, 503 service refusal).
ACCESS_DENIED_STATUSES = frozenset({401, 403, 407, 429, 503})

# HTTP statuses that mean the property page does not exist (assignment §4).
NOT_FOUND_STATUSES = frozenset({404, 410})

# AWS WAF interstitial markers OBSERVED in the live block event of
# 2026-10-31 (Booking.com served HTTP 202 with an empty <title>, no body
# text, and these strings in the page HTML). Evidence from the real
# response — not invented (spec: never invent signals).
WAF_HTML_MARKERS = (
    "awsWafCookieDomainList",
    "/__challenge_",
    "reportChallengeError",
)

# Standard CAPTCHA widget markup (public framework identifiers, not
# site-specific selectors). Heuristic — booking.com pages were never
# observed serving these.
CAPTCHA_MARKUP_MARKERS = (
    "g-recaptcha",
    "g-recaptcha-response",
    "h-captcha",
    "hcaptcha.com",
    "/recaptcha/",
)

# Explicit challenge phrases in VISIBLE text. Heuristic markers (refined in
# M5); they trigger stopping only, never extraction. "unusual traffic" is
# kept apart because it indicates generic bot filtering rather than a
# solvable CAPTCHA.
CAPTCHA_TEXT_MARKERS = (
    "confirm you are human",
    "verify you are human",
    "are you a robot",
    "captcha challenge",
)
BOT_TEXT_MARKERS = ("unusual traffic",)

# Challenge-ish <title> markers (heuristic; a normal property listing always
# carries a real title).
TITLE_MARKERS = ("captcha",)

# Interstitial page shape: challenge pages observed/known to render an empty
# or near-empty <title> and very little visible text. Used only as a
# CORROBORATING source, never on its own.
SHAPE_MAX_TITLE_CHARS = 10
SHAPE_MAX_TEXT_CHARS = 300


# --- Classification result ----------------------------------------------------


@dataclass(frozen=True)
class AccessClassification:
    """Result of :func:`classify_access_state`.

    ``state is None`` means the response shows no access problem (the
    caller proceeds to normal parsing). ``indicators`` is the concise,
    human-readable evidence list for logs (assignment §4: log the detected
    state with its indicators).
    """

    state: AccessState | None
    indicators: tuple[str, ...] = ()


def classify_access_state(
    *,
    http_status: int | None = None,
    title: str = "",
    html: str = "",
    visible_text: str = "",
) -> AccessClassification:
    """Classify one fetched page; returns ``state=None`` when it looks clear.

    Multi-indicator rules (assignment §4):

        * ``ACCESS_DENIED_STATUSES`` and ``NOT_FOUND_STATUSES`` are
          authoritative on their own.
        * AWS WAF: HTTP 202 together with at least one observed WAF HTML
          marker, OR at least two observed WAF HTML markers regardless of
          status.
        * CAPTCHA / bot protection from page content requires at least TWO
          independent sources among: visible-text markers, captcha markup,
          title (marker or empty), interstitial body shape.
    """
    title = title or ""
    html = html or ""
    visible_text = visible_text or ""

    # Interstitial page shape: challenge pages observed/known to render an
    # empty or near-empty <title> and very little visible text. Counts as ONE
    # corroborating source — never a classification on its own.
    title_empty = not title.strip()
    body_short = len(visible_text.strip()) < SHAPE_MAX_TEXT_CHARS

    # 1) HTTP statuses — authoritative alone.
    if http_status is not None:
        if http_status in ACCESS_DENIED_STATUSES:
            return AccessClassification(
                AccessState.HTTP_ACCESS_DENIED, (f"HTTP {http_status}",)
            )
        if http_status in NOT_FOUND_STATUSES:
            return AccessClassification(
                AccessState.PROPERTY_NOT_FOUND, (f"HTTP {http_status}",)
            )

    # 2) AWS WAF interstitial — observed evidence (2026-10-31):
    #    HTTP 202 + >=1 observed marker, OR >=2 observed markers, OR a
    #    marker corroborated by the interstitial page shape.
    waf_hits = tuple(marker for marker in WAF_HTML_MARKERS if marker in html)
    if (
        (http_status == 202 and waf_hits)
        or len(waf_hits) >= 2
        or (waf_hits and title_empty and body_short)
    ):
        evidence: list[str] = []
        if http_status == 202:
            evidence.append("HTTP 202")
        evidence.extend(waf_hits)
        return AccessClassification(AccessState.AWS_WAF_CHALLENGE, tuple(evidence))

    # 3) Content-based signals — require at least two independent sources.
    text_l = visible_text.casefold()
    title_l = title.casefold()
    html_l = html.casefold()

    text_hits = tuple(
        marker for marker in CAPTCHA_TEXT_MARKERS + BOT_TEXT_MARKERS if marker in text_l
    )
    markup_hits = tuple(marker for marker in CAPTCHA_MARKUP_MARKERS if marker in html_l)
    title_marker_hits = tuple(marker for marker in TITLE_MARKERS if marker in title_l)

    if not (text_hits or markup_hits or title_marker_hits):
        return AccessClassification(None, ())

    sources: list[str] = []
    if text_hits:
        sources.append("text: " + ", ".join(repr(m) for m in text_hits))
    if markup_hits:
        sources.append("markup: " + ", ".join(repr(m) for m in markup_hits))
    if title_marker_hits:
        sources.append("title: " + ", ".join(repr(m) for m in title_marker_hits))
    if title_empty:
        sources.append("empty title")
    if body_short:
        sources.append(f"short body text (<{SHAPE_MAX_TEXT_CHARS} chars)")

    # Count INDEPENDENT sources: page words (visible text), page chrome
    # (widget markup), the <title>, and page shape are distinct channels.
    # A lone marker with no corroboration is NOT classified (assignment:
    # "Do not classify a page based on one unreliable text match alone").
    independent = int(bool(text_hits))
    independent += int(bool(markup_hits))
    independent += int(bool(title_marker_hits))
    independent += int(bool(title_empty or body_short))
    if independent < 2:
        return AccessClassification(None, ())

    if markup_hits or title_marker_hits or any(
        m in CAPTCHA_TEXT_MARKERS for m in text_hits
    ):
        return AccessClassification(AccessState.CAPTCHA_PRESENT, tuple(sources))
    return AccessClassification(AccessState.BOT_PROTECTION, tuple(sources))


def raise_for_blocking_state(
    classification: AccessClassification,
    *,
    url: str,
    http_status: int | None = None,
) -> None:
    """Raise :class:`BlockedError` if the state requires stopping (§4).

    The single enforcement point shared by the live browser and the offline
    fixture/snapshot fetch paths, so no acquisition mode can accidentally
    parse a challenge page as inventory. Non-blocking states (e.g.
    ``PROPERTY_NOT_FOUND``) are left to the caller's verification layer.
    """
    state = classification.state
    if state is None or state not in BLOCKING_STATES:
        return

    indicators = ", ".join(classification.indicators) or "no indicator detail"
    if state is AccessState.HTTP_ACCESS_DENIED:
        message = (
            f"Access block: HTTP {http_status} from {url} — stopping this "
            "activity per assignment rules (no bypass attempts)."
        )
    elif state is AccessState.AWS_WAF_CHALLENGE:
        message = (
            f"AWS WAF challenge page from {url} (HTTP {http_status}, "
            f"indicators: {indicators}) — stopping per assignment rules "
            "(no bypass attempts)."
        )
    else:
        message = (
            f"{state.value} on {url} (HTTP {http_status}, indicators: "
            f"{indicators}) — stopping per assignment rules (no bypass attempts)."
        )
    log.warning(
        "Access state %s | url=%s | indicators=[%s]",
        state.value,
        redact_url(url),
        indicators,
    )
    raise BlockedError(message, url=url, access_state=state)


def state_for_exception(exc: BaseException) -> AccessState:
    """Map an exception to its :class:`AccessState` for logging/retry policy.

    An ``access_state`` carried on the exception (set where the failure was
    actually observed) always wins over the class-based fallback.
    """
    if isinstance(exc, ScraperError):
        carried = getattr(exc, "access_state", None)
        if isinstance(carried, AccessState):
            return carried
        if isinstance(exc, BlockedError):
            return AccessState.BOT_PROTECTION
        if isinstance(exc, PageTimeoutError):
            return AccessState.NAVIGATION_TIMEOUT
        if isinstance(exc, NavigationError):
            return AccessState.NETWORK_ERROR
        if isinstance(exc, StructureError):
            return AccessState.PAGE_STRUCTURE_CHANGED
        if isinstance(exc, MissingDataError):
            return AccessState.ROOM_DATA_NOT_LOADED
        if isinstance(exc, VerificationError):
            return AccessState.UNKNOWN_ERROR
        return AccessState.UNKNOWN_ERROR
    if isinstance(exc, (TimeoutError, OSError)):
        return AccessState.NETWORK_ERROR
    if isinstance(exc, (ValueError, KeyError, IndexError, TypeError)):
        return AccessState.EXTRACTION_ERROR
    return AccessState.UNKNOWN_ERROR


# --- URL / text redaction -----------------------------------------------------

# Query parameters safe to keep verbatim in logs and diagnostics: booking
# search parameters only. Everything else (tokens, sessions, e-mail-style
# identifiers, tracking) is dropped — allowlist, not blocklist.
ALLOWED_QUERY_PARAMS = frozenset(
    {
        "checkin",
        "checkout",
        "group_adults",
        "group_children",
        "no_rooms",
        "selected_currency",
        "dest_id",
        "dest_type",
        "currency",
        "lang",
    }
)

_REDACTED = "[redacted]"

_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'<>]+")
_SENSITIVE_KV_RE = re.compile(
    r"(?i)\b(token|session|auth|secret|password|cookie|api[-_]?key|key|sig|signature)"
    r"=([^&\s\"']+)"
)


def redact_url(url: str) -> str:
    """Strip userinfo and non-allowlisted query parameters from ``url``.

    Safe to call with any string: unparseable input is replaced wholesale,
    empty input passes through. Scheme/host/path are kept so the log still
    identifies the page that was fetched.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return _REDACTED
    netloc = parts.netloc.rsplit("@", 1)[-1]  # drop user:password@
    pairs = [
        (key, value if key.casefold() in ALLOWED_QUERY_PARAMS else _REDACTED)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (parts.scheme, netloc, parts.path, urlencode(pairs, safe="[]"), parts.fragment)
    )


def redact_text(text: str) -> str:
    """Redact embedded URLs and sensitive ``key=value`` pairs in free text.

    Applied to navigation-error messages and page titles before they reach
    logs or diagnostics.
    """
    if not text:
        return text
    out = _URL_IN_TEXT_RE.sub(lambda m: redact_url(m.group(0)), text)
    return _SENSITIVE_KV_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", out)


# --- Per-attempt diagnostics --------------------------------------------------


@dataclass
class AccessDiagnostic:
    """One attempt's access diagnostic (assignment §4).

    Required fields: attempt number; requested and final URLs (sensitive
    query parameters excluded); HTTP status when available; page title;
    navigation error; detected access state; whether the expected room
    information exists; whether extraction succeeded. Redaction is applied
    at construction so no construction site can leak a parameter.
    """

    attempt: int
    requested_url: str
    final_url: str | None = None
    http_status: int | None = None
    page_title: str = ""
    navigation_error: str | None = None
    access_state: AccessState | None = None
    indicators: tuple[str, ...] = ()
    room_info_present: bool | None = None
    extraction_succeeded: bool | None = None

    def __setattr__(self, name: str, value: object) -> None:
        """Redact on every assignment, not only at construction.

        Callers fill in the title/error *after* the fetch outcome is known;
        redacting only in ``__post_init__`` would let those later values
        bypass the allowlist (assignment §4: diagnostics must be safe to
        log). Redaction is idempotent, so repeated assignment is harmless.
        """
        if name in {"requested_url", "final_url"} and value is not None:
            value = redact_url(str(value))
        elif name in {"page_title", "navigation_error"} and value is not None:
            value = redact_text(str(value))
        super().__setattr__(name, value)

    def to_log_dict(self) -> dict[str, object]:
        """Flat, JSON-friendly view for structured logging (all redacted)."""
        return {
            "attempt": self.attempt,
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "page_title": self.page_title,
            "navigation_error": self.navigation_error,
            "access_state": self.access_state.value if self.access_state else None,
            "indicators": list(self.indicators),
            "room_info_present": self.room_info_present,
            "extraction_succeeded": self.extraction_succeeded,
        }
