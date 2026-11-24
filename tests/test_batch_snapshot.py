"""Batch snapshot tests (assignment §3.C): one saved page per check-in date.

Offline by design: files are written to tmp_path, nothing touches a network.
"""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from main import build_parser, validate_args
from scraper.access_state import AccessState
from scraper.exceptions import BlockedError, MissingDataError, VerificationError
from services.inventory_service import collect_inventory
from services.property_service import ConfigError, Property, Settings
from services.snapshot_service import (
    load_snapshot_dir,
    snapshot_fetch_all,
)
from services.validation_service import validate_rows

PROPERTY_NAME = "Around Stays-Heaven And Harmony"

PROPERTY = Property(
    property_id="batch_test",
    property_name=PROPERTY_NAME,
    location="Tapovan, India",
    booking_url="https://www.booking.com/hotel/in/batch-test.en-gb.html",
    booking_property_id="1",
    enabled=True,
)

SETTINGS = Settings(
    adults=2,
    rooms=1,
    stay_nights=1,
    currency="INR",
    days_to_scrape=30,
    page_load_delay_seconds=3.0,
    max_attempts_per_date=3,
    timezone="Asia/Kolkata",
)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def page(checkin: str, *, room_id: str, price: str) -> str:
    """Synthetic saved page whose search URL carries the check-in date."""
    return f"""<!DOCTYPE html>
<html lang="en"><head><title>{PROPERTY_NAME}, Tapovan</title>
<link rel="canonical" href="https://www.booking.com/hotel/in/x.html?checkin={checkin}">
</head><body>
<div data-testid="searchbox-dates-container"></div>
<div data-testid="breadcrumb-item">{PROPERTY_NAME}</div>
<table><tr class="e2e-hprt-table-row"><td>
  <a class="hprt-roomtype-link" data-room-id="{room_id}"><span class="hprt-roomtype-icon-link">Saved Room {checkin}</span></a>
  <div class="bui-price-display__value">{price}</div>
  <select class="hprt-nos-select"><option value="0">0</option><option value="1" selected>1</option></select>
</td></tr></table>
</body></html>
"""


def save(directory: Path, name: str, html: str) -> Path:
    path = directory / name
    path.write_text(html, encoding="utf-8")
    return path


@pytest.fixture()
def batch_dir(tmp_path: Path) -> Path:
    save(tmp_path, "booking_2026-12-03.html", page("2026-12-03", room_id="3001", price="₹ 1,300"))
    save(tmp_path, "booking_2026-12-01.html", page("2026-12-01", room_id="1001", price="₹ 1,040"))
    save(tmp_path, "booking_2026-12-02.html", page("2026-12-02", room_id="2001", price="₹ 1,144"))
    return tmp_path


# --- Loading a directory ------------------------------------------------------


def test_batch_loads_every_file_sorted_by_date(batch_dir: Path) -> None:
    snapshots = load_snapshot_dir(batch_dir, property_name=PROPERTY_NAME)

    assert [snap.checkin for snap in snapshots] == [
        date(2026, 12, 1),
        date(2026, 12, 2),
        date(2026, 12, 3),
    ]
    assert [snap.path.name for snap in snapshots] == [
        "booking_2026-12-01.html",
        "booking_2026-12-02.html",
        "booking_2026-12-03.html",
    ]
    assert "Saved Room 2026-12-01" in snapshots[0].html  # kept with its date


def test_non_html_files_are_ignored(batch_dir: Path) -> None:
    (batch_dir / "notes.txt").write_text("ignore me", encoding="utf-8")
    (batch_dir / ".DS_Store").write_bytes(b"junk")
    snapshots = load_snapshot_dir(batch_dir, property_name=PROPERTY_NAME)
    assert len(snapshots) == 3


def test_files_in_subdirectories_are_included(batch_dir: Path) -> None:
    nested = batch_dir / "dec"
    nested.mkdir()
    save(nested, "booking_2026-12-04.html", page("2026-12-04", room_id="4001", price="₹ 999"))
    snapshots = load_snapshot_dir(batch_dir, property_name=PROPERTY_NAME)
    assert [snap.checkin for snap in snapshots][-1] == date(2026, 12, 4)


def test_date_falls_back_to_the_file_name(tmp_path: Path) -> None:
    """No marker in the page is fine if the file name carries YYYY-MM-DD."""
    save(
        tmp_path,
        "booking_2026-12-07.html",
        f"<html><head><title>{PROPERTY_NAME}</title></head><body>no marker</body></html>",
    )
    snapshots = load_snapshot_dir(tmp_path, property_name=PROPERTY_NAME)
    assert [snap.checkin for snap in snapshots] == [date(2026, 12, 7)]


def test_undetectable_date_is_rejected_naming_the_file(tmp_path: Path) -> None:
    save(tmp_path, "a_2026-12-01.html", page("2026-12-01", room_id="1", price="₹ 1"))
    save(
        tmp_path,
        "checkout-page.html",
        f"<html><head><title>{PROPERTY_NAME}</title></head>"
        f"<body>no marker here</body></html>",
    )
    with pytest.raises(ConfigError, match="checkout-page.html"):
        load_snapshot_dir(tmp_path, property_name=PROPERTY_NAME)


def test_missing_directory_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_snapshot_dir(tmp_path / "nope", property_name=PROPERTY_NAME)


def test_directory_without_html_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="No HTML files"):
        load_snapshot_dir(tmp_path, property_name=PROPERTY_NAME)


def test_file_instead_of_directory_is_rejected(batch_dir: Path) -> None:
    single = batch_dir / "booking_2026-12-01.html"
    with pytest.raises(ConfigError, match="not a directory"):
        load_snapshot_dir(single, property_name=PROPERTY_NAME)


def test_duplicate_dates_are_rejected_with_both_paths(tmp_path: Path) -> None:
    save(tmp_path, "a_2026-12-01.html", page("2026-12-01", room_id="1", price="₹ 1"))
    save(tmp_path, "b_2026-12-01.html", page("2026-12-01", room_id="2", price="₹ 2"))

    with pytest.raises(ConfigError) as exc:
        load_snapshot_dir(tmp_path, property_name=PROPERTY_NAME)
    message = str(exc.value)
    assert "2026-12-01" in message
    assert "a_2026-12-01.html" in message and "b_2026-12-01.html" in message


def test_wrong_property_in_any_file_rejects_the_batch(tmp_path: Path) -> None:
    save(tmp_path, "a_2026-12-01.html", page("2026-12-01", room_id="1", price="₹ 1"))
    save(tmp_path, "b_2026-12-02.html", "<html><head><title>Other Hotel</title></head><body></body></html>")

    with pytest.raises(VerificationError) as exc:
        load_snapshot_dir(tmp_path, property_name=PROPERTY_NAME)
    assert exc.value.access_state is AccessState.PROPERTY_MISMATCH


def test_challenge_page_in_batch_stops_loading(tmp_path: Path) -> None:
    save(tmp_path, "a_2026-12-01.html", page("2026-12-01", room_id="1", price="₹ 1"))
    save(
        tmp_path,
        "b_2026-12-02.html",
        '<html><head><title></title></head><body><script>'
        'var awsWafCookieDomainList = ["booking.com"]; var p = "/__challenge_/x";'
        'function reportChallengeError(e) {}</script></body></html>',
    )
    with pytest.raises(BlockedError) as exc:
        load_snapshot_dir(tmp_path, property_name=PROPERTY_NAME)
    assert exc.value.access_state is AccessState.AWS_WAF_CHALLENGE


# --- Fetching by per-date URL -------------------------------------------------


def test_fetch_serves_the_page_matching_the_url_date(batch_dir: Path) -> None:
    snapshots = load_snapshot_dir(batch_dir, property_name=PROPERTY_NAME)
    fetch = snapshot_fetch_all(snapshots)

    assert "Saved Room 2026-12-02" in fetch(
        "https://www.booking.com/hotel/in/x.html?checkin=2026-12-02&group_adults=2"
    )
    assert "Saved Room 2026-12-03" in fetch(
        "https://www.booking.com/hotel/in/x.html?checkin=2026-12-03&group_adults=2"
    )


def test_fetch_without_a_date_fails_loudly(batch_dir: Path) -> None:
    fetch = snapshot_fetch_all(load_snapshot_dir(batch_dir, property_name=PROPERTY_NAME))
    with pytest.raises(MissingDataError, match="without a check-in date"):
        fetch("https://www.booking.com/hotel/in/x.html")


def test_fetch_never_falls_back_to_another_date(batch_dir: Path) -> None:
    fetch = snapshot_fetch_all(load_snapshot_dir(batch_dir, property_name=PROPERTY_NAME))
    with pytest.raises(MissingDataError, match="No saved snapshot for check-in 2026-12-31"):
        fetch("https://www.booking.com/hotel/in/x.html?checkin=2026-12-31")


# --- End-to-end merge ---------------------------------------------------------


def test_batch_merges_into_one_validated_csv_shape(batch_dir: Path) -> None:
    snapshots = load_snapshot_dir(batch_dir, property_name=PROPERTY_NAME)
    dates = [snap.checkin for snap in snapshots]
    urls: list[str] = []

    def fetch(url: str) -> str:
        urls.append(url)
        return snapshot_fetch_all(snapshots)(url)

    result = collect_inventory(
        property_record=PROPERTY,
        settings=SETTINGS,
        checkin_dates=dates,
        fetch_html=fetch,
        now=lambda: NOW,
        data_source="snapshot",
    )

    assert result.blocked is False
    assert (result.dates_ok, result.dates_failed) == (3, 0)
    assert len(result.rows) == 3  # one room row per date
    assert [row["checkin_date"] for row in result.rows] == [
        "2026-12-01",
        "2026-12-02",
        "2026-12-03",
    ]
    assert {row["data_source"] for row in result.rows} == {"snapshot"}
    assert {row["room_category"] for row in result.rows} == {
        "Saved Room 2026-12-01",
        "Saved Room 2026-12-02",
        "Saved Room 2026-12-03",
    }
    assert all("checkin=" in url for url in urls)
    validate_rows(result.rows, dates, stay_nights=1)


# --- CLI rules ----------------------------------------------------------------


def test_checkin_is_rejected_with_a_directory(batch_dir: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["--mode", "snapshot", "--input", str(batch_dir), "--checkin", "2026-12-01"]
    )
    with pytest.raises(SystemExit) as exc:
        validate_args(parser, args)
    assert exc.value.code == 2


def test_checkin_still_accepted_with_a_single_file(batch_dir: Path) -> None:
    parser = build_parser()
    single = batch_dir / "booking_2026-12-01.html"
    args = validate_args(
        parser,
        parser.parse_args(
            ["--mode", "snapshot", "--input", str(single), "--checkin", "2026-12-01"]
        ),
    )
    assert args.mode == "snapshot"
    assert args.checkin == "2026-12-01"
