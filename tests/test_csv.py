"""Unit tests for the CSV contract: filename, exact header order, encoding
and serialization (spec §8.1/§8.2/§8.7, §14.1 "CSV header and column order
are exact").

All tests run offline; no Booking.com access required.
"""

import csv
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services.csv_service import (
    OUTPUT_COLUMNS,
    REQUIRED_COLUMNS,
    output_filename,
    output_path,
    sample_output_path,
    write_csv,
)
from tests.helpers import make_error_row, make_no_rooms_row, make_row, rows_for

# The eight columns exactly as listed in spec §8.2 — hardcoded here so any
# accidental reorder in csv_service fails this test.
SPEC_REQUIRED_COLUMNS = [
    "scrape_timestamp",
    "checkin_date",
    "checkout_date",
    "room_category",
    "rooms_available",
    "availability_source",
    "min_price_inr",
    "status",
]

RUN_TIME = datetime(2026, 10, 31, 17, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
PROPERTY_ID = "heaven_and_harmony_tapovan"


def read_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


# --- Filename (per-property; recorded deviation from spec §8.1) -----------------


def test_output_filename_uses_property_id() -> None:
    assert (
        output_filename(RUN_TIME, PROPERTY_ID)
        == "heaven_and_harmony_tapovan_inventory_20261031_1715.csv"
    )


def test_output_filename_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        output_filename(datetime(2026, 10, 31, 17, 15), PROPERTY_ID)


def test_output_filename_requires_property_id() -> None:
    with pytest.raises(ValueError, match="property_id"):
        output_filename(RUN_TIME, "")


def test_output_path_creates_directory_and_uses_pattern(tmp_path: Path) -> None:
    target_dir = tmp_path / "new_output"
    path = output_path(RUN_TIME, PROPERTY_ID, target_dir)
    assert path.parent.is_dir()
    assert path.name == "heaven_and_harmony_tapovan_inventory_20261031_1715.csv"


def test_sample_output_path_is_clearly_labelled(tmp_path: Path) -> None:
    """spec §16: a sample from saved evidence must label itself as such."""
    path = sample_output_path(RUN_TIME, PROPERTY_ID, tmp_path)
    assert path.name == "heaven_and_harmony_tapovan_inventory_20261031_1715_SAMPLE.csv"
    assert path.name.startswith(f"{PROPERTY_ID}_inventory_")


# --- Header and column order (spec §8.2) ----------------------------------------


def test_required_columns_match_spec_exactly() -> None:
    assert REQUIRED_COLUMNS == SPEC_REQUIRED_COLUMNS


def test_bonus_columns_follow_required_columns() -> None:
    assert OUTPUT_COLUMNS[: len(REQUIRED_COLUMNS)] == REQUIRED_COLUMNS
    assert OUTPUT_COLUMNS[len(REQUIRED_COLUMNS):] == ["error_detail", "is_capped"]


def test_written_header_is_exact(tmp_path: Path) -> None:
    path = write_csv(rows_for([date(2026, 11, 1)]), tmp_path / "out.csv")
    header = read_rows(path)[0]
    assert header == OUTPUT_COLUMNS
    assert header[:8] == SPEC_REQUIRED_COLUMNS


def test_row_cell_count_matches_header(tmp_path: Path) -> None:
    rows = [
        make_row(checkin_date="2026-11-01", checkout_date="2026-11-02"),
        make_no_rooms_row(checkin_date="2026-11-02", checkout_date="2026-11-03"),
        make_error_row(checkin_date="2026-11-03", checkout_date="2026-11-04"),
    ]
    path = write_csv(rows, tmp_path / "mixed.csv")
    for line in read_rows(path):
        assert len(line) == len(OUTPUT_COLUMNS)


# --- Encoding and value serialization -------------------------------------------


def test_utf8_room_names_roundtrip(tmp_path: Path) -> None:
    name = "Délicieuse Chambre — कक्ष ₹4,500"
    rows = rows_for([date(2026, 11, 1)], room_category=name)
    path = write_csv(rows, tmp_path / "utf8.csv")
    parsed = read_rows(path)
    assert parsed[1][3] == name  # column index of room_category


def test_none_values_become_empty_cells(tmp_path: Path) -> None:
    rows = [
        make_no_rooms_row(checkin_date="2026-11-01", checkout_date="2026-11-02")
    ]
    path = write_csv(rows, tmp_path / "empty.csv")
    record = dict(zip(OUTPUT_COLUMNS, read_rows(path)[1]))
    assert record["room_category"] == ""
    assert record["rooms_available"] == ""
    assert record["min_price_inr"] == ""
    assert record["status"] == "no_rooms_listed"


def test_booleans_serialize_as_lowercase(tmp_path: Path) -> None:
    rows = [
        make_row(checkin_date="2026-11-01", checkout_date="2026-11-02",
                 is_capped=True, error_detail=None),
        make_row(checkin_date="2026-11-02", checkout_date="2026-11-03",
                 is_capped=False),
    ]
    path = write_csv(rows, tmp_path / "bool.csv")
    parsed = read_rows(path)
    capped_index = OUTPUT_COLUMNS.index("is_capped")
    assert parsed[1][capped_index] == "true"
    assert parsed[2][capped_index] == "false"


def test_commas_and_quotes_are_quoted_and_roundtrip(tmp_path: Path) -> None:
    detail = 'Timeout, after "3" attempts; page did not load'
    rows = [
        make_error_row(checkin_date="2026-11-01", checkout_date="2026-11-02",
                       error_detail=detail)
    ]
    path = write_csv(rows, tmp_path / "quoted.csv")
    record = dict(zip(OUTPUT_COLUMNS, read_rows(path)[1]))
    assert record["error_detail"] == detail


def test_integers_and_dates_render_as_plain_text(tmp_path: Path) -> None:
    rows = rows_for([date(2026, 11, 1)], rooms_available=7, min_price_inr=15600)
    path = write_csv(rows, tmp_path / "plain.csv")
    record = dict(zip(OUTPUT_COLUMNS, read_rows(path)[1]))
    assert record["rooms_available"] == "7"
    assert record["min_price_inr"] == "15600"
    assert record["checkin_date"] == "2026-11-01"
    assert record["checkout_date"] == "2026-11-02"


# --- Contract enforcement ---------------------------------------------------------


def test_missing_required_key_raises(tmp_path: Path) -> None:
    rows = rows_for([date(2026, 11, 1)])
    del rows[0]["status"]
    with pytest.raises(ValueError, match="missing required column"):
        write_csv(rows, tmp_path / "bad.csv")


def test_extra_key_raises(tmp_path: Path) -> None:
    rows = rows_for([date(2026, 11, 1)])
    rows[0]["surprise_column"] = "nope"
    with pytest.raises(ValueError):
        write_csv(rows, tmp_path / "extra.csv")


def test_missing_bonus_key_writes_empty_cell(tmp_path: Path) -> None:
    row = make_row(checkin_date="2026-11-01", checkout_date="2026-11-02")
    del row["error_detail"]  # bonus columns are optional per row
    del row["is_capped"]
    path = write_csv([row], tmp_path / "nobonus.csv")
    record = dict(zip(OUTPUT_COLUMNS, read_rows(path)[1]))
    assert record["error_detail"] == ""
    assert record["is_capped"] == ""


def test_write_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = write_csv(rows_for([date(2026, 11, 1)]), tmp_path / "a" / "b" / "out.csv")
    assert path.is_file()


def test_output_file_is_empty_but_valid_with_no_rows(tmp_path: Path) -> None:
    """validate_rows rejects empty input first; writer still emits a header."""
    path = write_csv([], tmp_path / "header_only.csv")
    assert read_rows(path) == [OUTPUT_COLUMNS]
