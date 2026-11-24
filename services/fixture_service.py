"""Deterministic offline fixture scenarios (assignment §3.B, §8).

Fixtures replace the browser with a local HTML string, but they go through
the SAME access-state classifier and the same stop rule as a live fetch —
a challenge fixture must never be parsed as inventory. Every row produced
from here is labelled ``data_source=fixture`` by the caller.

Nothing in this module opens a network connection. `manifest.json` carries
the replayed HTTP status for scenarios whose classification is
status-authoritative (e.g. 403); that status is test metadata, not a live
observation.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from scraper.access_state import (
    AccessState,
    classify_access_state,
    raise_for_blocking_state,
)
from scraper.exceptions import VerificationError

log = logging.getLogger("hotel_scraper.fixture")

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
SYNTHETIC_DIR = FIXTURE_DIR / "synthetic"
MANIFEST_PATH = SYNTHETIC_DIR / "manifest.json"
EVIDENCE_FIXTURE = FIXTURE_DIR / "live_2026-11-01_roomtable.html"
EVIDENCE_SCENARIO = "evidence"
EVIDENCE_PROPERTY_NAME = "Around Stays-Heaven And Harmony"

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class Fixture:
    """One scenario's HTML plus its replay/verification metadata."""

    name: str
    path: Path
    html: str
    http_status: int | None
    expected_property_name: str | None
    expected_state: AccessState | None
    description: str

    @property
    def is_synthetic(self) -> bool:
        return self.name != EVIDENCE_SCENARIO


def page_title(html: str) -> str:
    """HTML ``<title>`` text (standard element, no site-specific selector)."""
    match = _TITLE_RE.search(html)
    return match.group(1).strip() if match else ""


def visible_text(html: str) -> str:
    """Visible body text via standard HTML semantics only."""
    try:
        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    except Exception:  # pragma: no cover — malformed HTML must fail open
        return ""


def load_manifest() -> dict[str, dict]:
    """Read the synthetic scenario manifest (fail loud if it is broken)."""
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Fixture manifest must be an object: {MANIFEST_PATH}")
    return {key: value for key, value in data.items() if not key.startswith("_")}


def scenario_names() -> list[str]:
    """All selectable scenario names, with the saved evidence first."""
    return [EVIDENCE_SCENARIO, *sorted(load_manifest())]


def load_fixture(name: str) -> Fixture:
    """Load a registered scenario by name.

    ``evidence`` is the sanitized real page kept from M0; all other names
    resolve through ``manifest.json`` to the small synthetic pages.
    """
    if name == EVIDENCE_SCENARIO:
        if not EVIDENCE_FIXTURE.is_file():
            raise FileNotFoundError(f"Evidence fixture missing: {EVIDENCE_FIXTURE}")
        return Fixture(
            name=name,
            path=EVIDENCE_FIXTURE,
            html=EVIDENCE_FIXTURE.read_text(encoding="utf-8"),
            http_status=None,  # unknown for a saved HTML fragment; no claim
            expected_property_name=EVIDENCE_PROPERTY_NAME,
            expected_state=None,
            description="Sanitized real Booking.com page saved during M0 verification.",
        )

    manifest = load_manifest()
    if name not in manifest:
        available = ", ".join(scenario_names())
        raise ValueError(f"Unknown fixture scenario {name!r}. Available: {available}.")
    meta = manifest[name]
    path = SYNTHETIC_DIR / str(meta["file"])
    if not path.is_file():
        raise FileNotFoundError(f"Fixture for scenario {name!r} missing: {path}")
    expected_state = meta.get("expected_state")
    return Fixture(
        name=name,
        path=path,
        html=path.read_text(encoding="utf-8"),
        http_status=meta.get("http_status"),
        expected_property_name=meta.get("expected_property_name"),
        expected_state=AccessState(expected_state) if expected_state else None,
        description=str(meta.get("description", "")),
    )


def classify_fixture(fixture: Fixture, *, url: str) -> AccessState | None:
    """Classify the fixture exactly like a fetched page; raise on blocks.

    Reusing the centralized classifier here is what makes a WAF/CAPTCHA
    fixture a *stop* signal in offline mode instead of parseable HTML.
    """
    classification = classify_access_state(
        http_status=fixture.http_status,
        title=page_title(fixture.html),
        html=fixture.html,
        visible_text=visible_text(fixture.html),
    )
    raise_for_blocking_state(
        classification, url=url, http_status=fixture.http_status
    )
    return classification.state


def verify_fixture_identity(fixture: Fixture, *, property_name: str) -> None:
    """Refuse to scrape a fixture for a different property (assignment §4).

    Challenge scenarios declare no property name: they stop before identity
    is even relevant, so there is nothing to compare there.
    """
    expected = fixture.expected_property_name
    if expected is None:
        return
    configured = property_name.strip().casefold()
    if expected.casefold() != configured or expected.casefold() not in fixture.html.casefold():
        raise VerificationError(
            f"Fixture {fixture.name!r} represents {expected!r}, not the "
            f"configured property {property_name!r}; refusing to scrape it.",
            url=str(fixture.path),
            access_state=AccessState.PROPERTY_MISMATCH,
        )


def fixture_fetch(fixture: Fixture) -> Callable[[str], str]:
    """Build the ``url -> html`` callable consumed by ``collect_inventory``."""

    def fetch(url: str) -> str:
        state = classify_fixture(fixture, url=url)
        if state in (AccessState.PROPERTY_NOT_FOUND, AccessState.PROPERTY_MISMATCH):
            raise VerificationError(
                f"Fixture {fixture.name!r} classified as {state.value}; "
                "refusing to extract room data.",
                url=url,
                access_state=state,
            )
        log.debug("Serving fixture %s for %s", fixture.name, url)
        return fixture.html

    return fetch
