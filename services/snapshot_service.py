"""Locally saved HTML snapshot mode (assignment §3.C, §8).

A snapshot is a real page the user already saved — not a request made by
this run. It is processed through the same classifier, parser, validator and
CSV writer as live data.

Two input shapes are supported:

    * a single HTML file -> exactly ONE check-in date (from ``--checkin``
      or detected in the file);
    * a directory of HTML files -> one file per check-in date (batch), the
      way a real multi-date snapshot set is collected: a human saves each
      date's page in their own working browser, then this run merges them
      into one CSV.

Dates are detected per file, duplicate or undetectable dates are rejected,
and every row is labelled ``data_source=snapshot`` with a ``_SNAPSHOT``
filename suffix. No network request is ever made in this mode.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from services.fixture_service import page_title, visible_text
from services.property_service import ConfigError
from scraper.access_state import AccessState, classify_access_state, raise_for_blocking_state
from scraper.exceptions import MissingDataError, VerificationError

log = logging.getLogger("hotel_scraper.snapshot")

# Only standard HTML/URL semantics: a check-in marker next to an ISO date,
# or an ISO date in the file name. Never guess a date from room prices/text.
_CHECKIN_IN_HTML_RE = re.compile(
    r"(?i)(?:checkin|check-in|check_in)[^0-9]{0,48}(\d{4}-\d{2}-\d{2})"
)
_DATE_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# Per-date search URL built by booking_scraper (checkin=YYYY-MM-DD&...).
_CHECKIN_IN_URL_RE = re.compile(r"[?&]checkin=(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Snapshot:
    """A validated local HTML snapshot and the date it represents."""

    path: Path
    html: str
    checkin: date

    @property
    def url(self) -> str:
        """File URI used for honest, redactable diagnostics (not a fetch)."""
        return self.path.resolve().as_uri()


def detect_checkin(html: str, path: Path) -> date | None:
    """Best-effort check-in detection from standard page/file markers."""
    for candidate in (_CHECKIN_IN_HTML_RE.search(html), _DATE_IN_NAME_RE.search(path.name)):
        if candidate is None:
            continue
        try:
            return date.fromisoformat(candidate.group(1))
        except ValueError:
            continue
    return None


def load_snapshot(
    path: Path, *, requested_checkin: str | None, property_name: str
) -> Snapshot:
    """Read, classify and verify a snapshot; resolve its single check-in date."""
    if not path.is_file():
        raise ConfigError(f"Snapshot HTML file not found: {path}")
    html = _read_html(path)
    _classify_and_verify(path, html, property_name=property_name)

    if requested_checkin:
        try:
            checkin = date.fromisoformat(requested_checkin)
        except ValueError as exc:  # argparse already checks this; defensive
            raise ConfigError(
                f"--checkin must be an ISO date (YYYY-MM-DD), got {requested_checkin!r}."
            ) from exc
    else:
        checkin = detect_checkin(html, path)  # type: ignore[assignment]
        if checkin is None:
            raise ConfigError(
                "Could not detect the snapshot's check-in date from the HTML or "
                "file name. Re-run with --checkin YYYY-MM-DD."
            )

    log.warning(
        "SNAPSHOT MODE: processing saved HTML %s for ONE check-in date (%s). "
        "No Booking.com request is made; rows are labelled snapshot data, "
        "not live observations.",
        path,
        checkin.isoformat(),
    )
    return Snapshot(path=path, html=html, checkin=checkin)


def load_snapshot_dir(directory: Path, *, property_name: str) -> list[Snapshot]:
    """Load a directory of saved pages — one snapshot per check-in date.

    Batch mode for a real multi-date data set: every ``*.html``/``*.htm``
    file must be readable, must classify as non-blocking, must contain the
    configured property name, and must yield a check-in date detectable from
    the page or the file name. Two files claiming the same date is a data
    error, reported with both file names — never silently pick one.
    """
    if not directory.exists():
        raise ConfigError(f"Snapshot directory not found: {directory}")
    if not directory.is_dir():
        raise ConfigError(f"Snapshot input is not a directory: {directory}")

    files = sorted(
        f for f in directory.rglob("*")
        if f.is_file() and f.suffix.lower() in {".html", ".htm"}
    )
    if not files:
        raise ConfigError(
            f"No HTML files (*.html, *.htm) found under {directory}. Save each "
            "check-in date's page into this directory first."
        )

    loaded: dict[date, tuple[Path, str]] = {}
    for path in files:
        html = _read_html(path)
        _classify_and_verify(path, html, property_name=property_name)
        checkin = detect_checkin(html, path)
        if checkin is None:
            raise ConfigError(
                f"Could not detect a check-in date for {path}. Rename the file "
                "to include YYYY-MM-DD or re-save the page with its search URL."
            )
        if checkin in loaded:
            raise ConfigError(
                f"Two snapshot files claim check-in {checkin}: {loaded[checkin][0]} "
                f"and {path}. Keep one file per date."
            )
        loaded[checkin] = (path, html)

    snapshots = [
        Snapshot(path=loaded[day][0], html=loaded[day][1], checkin=day)
        for day in sorted(loaded)
    ]
    log.warning(
        "SNAPSHOT MODE (batch): %d saved HTML file(s) for %d check-in date(s) "
        "(%s .. %s). No Booking.com request is made; rows are labelled "
        "snapshot data, not live observations.",
        len(snapshots),
        len(loaded),
        snapshots[0].checkin.isoformat(),
        snapshots[-1].checkin.isoformat(),
    )
    return snapshots


def _read_html(path: Path) -> str:
    """Read one local HTML file with an honest config error on failure."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"Snapshot HTML could not be read ({path}): {exc}") from exc


def _classify_and_verify(path: Path, html: str, *, property_name: str) -> None:
    """Apply the central stop rule and the identity check to one saved page."""
    uri = path.resolve().as_uri()
    classification = classify_access_state(
        title=page_title(html),
        html=html,
        visible_text=visible_text(html),
    )
    raise_for_blocking_state(classification, url=uri, http_status=None)

    if property_name.strip().casefold() not in html.casefold():
        raise VerificationError(
            f"Snapshot {path} does not contain the configured property name "
            f"{property_name!r}; refusing to scrape an unverified listing.",
            url=uri,
            access_state=AccessState.PROPERTY_MISMATCH,
        )


def snapshot_fetch(snapshot: Snapshot) -> Callable[[str], str]:
    """Fetch callable for a single-snapshot run (kept for the one-file path)."""
    return snapshot_fetch_all([snapshot])


def snapshot_fetch_all(snapshots: list[Snapshot]) -> Callable[[str], str]:
    """Build ``url -> html`` for a batch: the URL's check-in picks the file.

    Classifications are computed once per file here, then re-applied on every
    call (static HTML, but the same central stop rule must hold on every
    fetch path — §4). Identity was already verified when the snapshots were
    loaded. A date with no saved file is a missing-data failure for that
    date, never a silent fallback to another date's page.
    """
    by_date = {snap.checkin: snap for snap in snapshots}
    classified = {
        snap.checkin: classify_access_state(
            title=page_title(snap.html),
            html=snap.html,
            visible_text=visible_text(snap.html),
        )
        for snap in snapshots
    }

    def fetch(url: str) -> str:
        match = _CHECKIN_IN_URL_RE.search(url)
        if match is None:
            raise MissingDataError(
                f"Snapshot fetch received a URL without a check-in date: {url}"
            )
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError as exc:
            raise MissingDataError(
                f"Snapshot fetch URL has an unparsable check-in: {url}"
            ) from exc

        snap = by_date.get(day)
        if snap is None:
            raise MissingDataError(
                f"No saved snapshot for check-in {day}; saved dates: "
                f"{', '.join(d.isoformat() for d in sorted(by_date))}."
            )

        raise_for_blocking_state(classified[day], url=url, http_status=None)
        log.debug("Serving snapshot %s for %s", snap.path, url)
        return snap.html

    return fetch
