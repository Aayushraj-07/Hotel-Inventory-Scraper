"""Snapshot-mode tests (assignment §3.C, §8): one date, honest labelling.

All offline: local HTML written to tmp_path, no browser, no network.
"""

from datetime import date
from pathlib import Path

import pytest

from scraper.access_state import AccessState
from scraper.exceptions import BlockedError, MissingDataError, VerificationError
from services.property_service import ConfigError
from services.snapshot_service import (
    Snapshot,
    detect_checkin,
    load_snapshot,
    snapshot_fetch,
)

PROPERTY_NAME = "Around Stays-Heaven And Harmony"

PAGE_WITH_DATE = f"""<!DOCTYPE html>
<html lang="en"><head><title>{PROPERTY_NAME}, Tapovan</title>
<link rel="canonical" href="https://www.booking.com/hotel/in/x.html?checkin=2026-12-01&amp;checkout=2026-12-02">
</head><body>
<div data-testid="searchbox-dates-container"></div>
<div data-testid="breadcrumb-item">{PROPERTY_NAME}</div>
</body></html>
"""

PAGE_WITHOUT_DATE = f"""<!DOCTYPE html>
<html lang="en"><head><title>{PROPERTY_NAME}, Tapovan</title></head><body>
<div data-testid="searchbox-dates-container"></div>
</body></html>
"""

WAF_PAGE = """<!DOCTYPE html><html><head><title></title></head><body>
<script>var awsWafCookieDomainList = ["booking.com"]; var p = "/__challenge_/x";
function reportChallengeError(e) {}</script></body></html>
"""


def write(tmp_path: Path, html: str, name: str = "saved.html") -> Path:
    path = tmp_path / name
    path.write_text(html, encoding="utf-8")
    return path


# --- Date detection ----------------------------------------------------------


def test_detect_checkin_from_page_marker(tmp_path: Path) -> None:
    path = write(tmp_path, PAGE_WITH_DATE)
    assert detect_checkin(PAGE_WITH_DATE, path) == date(2026, 12, 1)


def test_detect_checkin_from_file_name(tmp_path: Path) -> None:
    path = write(tmp_path, PAGE_WITHOUT_DATE, name="booking_2026-12-05.html")
    assert detect_checkin(PAGE_WITHOUT_DATE, path) == date(2026, 12, 5)


def test_detect_checkin_returns_none_when_there_is_no_marker(tmp_path: Path) -> None:
    path = write(tmp_path, PAGE_WITHOUT_DATE)
    assert detect_checkin(PAGE_WITHOUT_DATE, path) is None


# --- Loading / verification --------------------------------------------------


def test_load_snapshot_with_explicit_checkin(tmp_path: Path) -> None:
    path = write(tmp_path, PAGE_WITHOUT_DATE)
    snapshot = load_snapshot(path, requested_checkin="2026-12-09", property_name=PROPERTY_NAME)

    assert snapshot.checkin == date(2026, 12, 9)
    assert snapshot.html == PAGE_WITHOUT_DATE
    assert snapshot.url.startswith("file://")


def test_load_snapshot_autodetects_checkin(tmp_path: Path) -> None:
    path = write(tmp_path, PAGE_WITH_DATE)
    snapshot = load_snapshot(path, requested_checkin=None, property_name=PROPERTY_NAME)
    assert snapshot.checkin == date(2026, 12, 1)


def test_missing_date_is_a_config_error(tmp_path: Path) -> None:
    path = write(tmp_path, PAGE_WITHOUT_DATE)
    with pytest.raises(ConfigError, match="--checkin"):
        load_snapshot(path, requested_checkin=None, property_name=PROPERTY_NAME)


def test_invalid_date_is_a_config_error(tmp_path: Path) -> None:
    path = write(tmp_path, PAGE_WITHOUT_DATE)
    with pytest.raises(ConfigError, match="ISO date"):
        load_snapshot(path, requested_checkin="tomorrow", property_name=PROPERTY_NAME)


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_snapshot(tmp_path / "nope.html", requested_checkin=None, property_name=PROPERTY_NAME)


def test_wrong_property_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, PAGE_WITHOUT_DATE)
    with pytest.raises(VerificationError) as exc:
        load_snapshot(path, requested_checkin="2026-12-01", property_name="Other Hotel")
    assert exc.value.access_state is AccessState.PROPERTY_MISMATCH


def test_challenge_snapshot_stops_before_parsing(tmp_path: Path) -> None:
    path = write(tmp_path, WAF_PAGE, name="waf_2026-12-01.html")
    with pytest.raises(BlockedError) as exc:
        load_snapshot(path, requested_checkin=None, property_name=PROPERTY_NAME)
    assert exc.value.access_state is AccessState.AWS_WAF_CHALLENGE


# --- Fetch callable ----------------------------------------------------------


def test_snapshot_fetch_returns_the_saved_html(tmp_path: Path) -> None:
    path = write(tmp_path, PAGE_WITH_DATE)
    snapshot = load_snapshot(path, requested_checkin=None, property_name=PROPERTY_NAME)
    url = "https://www.booking.com/hotel/in/x.html?checkin=2026-12-01&group_adults=2"
    assert snapshot_fetch(snapshot)(url) == PAGE_WITH_DATE


def test_snapshot_fetch_rechecks_blocking_state(tmp_path: Path) -> None:
    """A stored Snapshot must still fail closed if its HTML is a challenge."""
    path = write(tmp_path, WAF_PAGE)
    snapshot = Snapshot(path=path, html=WAF_PAGE, checkin=date(2026, 12, 1))
    with pytest.raises(BlockedError):
        snapshot_fetch(snapshot)("https://example.invalid/?checkin=2026-12-01")


def test_snapshot_fetch_refuses_a_different_date(tmp_path: Path) -> None:
    """Never serve one date's saved page for another date's row."""
    path = write(tmp_path, PAGE_WITH_DATE)
    snapshot = load_snapshot(path, requested_checkin=None, property_name=PROPERTY_NAME)
    with pytest.raises(MissingDataError, match="No saved snapshot for check-in"):
        snapshot_fetch(snapshot)("https://example.invalid/?checkin=2026-12-31")
