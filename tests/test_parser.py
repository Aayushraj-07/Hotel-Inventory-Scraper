"""Parser tests against sanitized REAL fixtures (spec §14.1, §14.2).

Offline by design: no Booking.com access, no browser. Ground truth for
the fixture assertions is the hand-parsed table in
``docs/m0_findings.md`` §8.5 — every expected value there was observed
on the real public page, not invented.
"""

from pathlib import Path

import pytest

from scraper.availability_parser import (
    looks_like_property_page,
    parse_price,
    parse_rooms,
)
from scraper.exceptions import StructureError

FIXTURES = Path(__file__).parent / "fixtures"

ROOMTABLE_HTML = (FIXTURES / "live_2026-11-01_roomtable.html").read_text(
    encoding="utf-8"
)
SEARCH_RESULTS_HTML = (FIXTURES / "search_results_2026-11-01.html").read_text(
    encoding="utf-8"
)

# Hand-parsed ground truth for 2026-11-01 (m0_findings §8.5):
# (room_category, data-room-id, qty max, price INR, taxes INR)
EXPECTED_ROWS = [
    ("Standard Double Room", "1732890704", 2, 1040, 52),
    ("Deluxe Double Room", "1732890701", 7, 1144, 57),
    ("Deluxe Quadruple Room", "1732890703", 2, 2080, 104),
    ("6-Bed Mixed Dormitory Room", "1732890702", 3, 2184, 109),
]


# --- Real fixture: full ground-truth assertions ---------------------------


def test_fixture_parses_exactly_the_hand_parsed_rows() -> None:
    rows = parse_rooms(ROOMTABLE_HTML)
    assert len(rows) == len(EXPECTED_ROWS)
    for row, (name, room_id, qty, price, taxes) in zip(rows, EXPECTED_ROWS):
        assert row["room_category"] == name
        assert row["room_type_id"] == room_id
        assert row["rooms_available"] == qty
        assert row["qty_selectable_max"] == qty
        assert row["min_price_inr"] == price
        assert row["taxes_extra_inr"] == taxes


def test_names_preserve_exact_displayed_wording() -> None:
    rows = parse_rooms(ROOMTABLE_HTML)
    assert [row["room_category"] for row in rows] == [
        "Standard Double Room",
        "Deluxe Double Room",
        "Deluxe Quadruple Room",
        "6-Bed Mixed Dormitory Room",
    ]


def test_selector_counts_are_recorded_as_capped() -> None:
    """spec §7.2: selector maximums are recorded but marked capped."""
    rows = parse_rooms(ROOMTABLE_HTML)
    for row in rows:
        assert row["availability_source"] == "room_selector"
        assert row["is_capped"] is True
        assert isinstance(row["rooms_available"], int)
        assert row["rooms_available"] >= 1


def test_prices_are_integer_inr_stay_totals() -> None:
    rows = parse_rooms(ROOMTABLE_HTML)
    assert [row["min_price_inr"] for row in rows] == [1040, 1144, 2080, 2184]
    assert all(isinstance(row["min_price_inr"], int) for row in rows)


def test_no_sold_out_or_scercity_fabricated_on_fixture() -> None:
    """Honesty: sold_out must never be invented; fixture showed none."""
    rows = parse_rooms(ROOMTABLE_HTML)
    for row in rows:
        assert row["status"] == "ok"
        assert row["scarcity_message"] is None
        assert row["error_detail"] is None


# --- Page classification (no_rooms_listed vs error, spec §8.4) ------------


def test_property_page_with_rows_is_recognized() -> None:
    assert looks_like_property_page(ROOMTABLE_HTML) is True


def test_search_results_page_is_not_a_property_page() -> None:
    """Search results have no room rows and must not look like one."""
    assert parse_rooms(SEARCH_RESULTS_HTML) == []
    assert looks_like_property_page(SEARCH_RESULTS_HTML) is False


def test_property_page_without_rows_is_recognized() -> None:
    """Loaded property chrome + zero rows -> caller may emit no_rooms_listed."""
    html = (
        '<html><body>'
        '<div data-testid="searchbox-dates-container"></div>'
        '<div data-testid="breadcrumb-item"></div>'
        "</body></html>"
    )
    assert parse_rooms(html) == []
    assert looks_like_property_page(html) is True


def test_unrelated_html_is_not_a_property_page() -> None:
    assert looks_like_property_page("<html><body>maintenance</body></html>") is False


# --- Price parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("₹\xa01,040", 1040),       # observed NBSP format
        ("₹ 2,184", 2184),
        ("₹4,000", 4000),
        ("999", 999),
        ("Price unavailable", None),
        ("", None),
        ("₹", None),
    ],
)
def test_parse_price(text: str, expected: int | None) -> None:
    assert parse_price(text) == expected


# --- Synthetic snippets: only OBSERVED element/attribute names ------------
# These build minimal rows from the exact observed tags/attributes
# (m0_findings §8.5) to exercise code paths the fixture does not cover.


ROW_TEMPLATE = """
<tr class="js-rt-block-row e2e-hprt-table-row" data-block-id="1_2_0_0">
  <td>
    <a class="hprt-roomtype-link" data-room-id="{room_id}">
      <span class="hprt-roomtype-icon-link">{name}</span>
    </a>
    {selector}
    <div class="bui-price-display__value">{price}</div>
    <div data-excl-charges-raw="{taxes_raw}">{taxes_text}</div>
    {extra}
  </td>
</tr>
"""


def page_with(rows_html: str) -> str:
    return f"<html><body><table>{rows_html}</table></body></html>"


def selector_with(max_value: int) -> str:
    options = "".join(
        f'<option value="{value}"></option>' for value in range(max_value + 1)
    )
    return f'<select class="hprt-nos-select">{options}</select>'


def test_selector_max_is_taken_from_highest_option() -> None:
    html = page_with(
        ROW_TEMPLATE.format(
            room_id="1732890704",
            name="Standard Double Room",
            selector=selector_with(5),
            price="₹ 1,040",
            taxes_raw="52",
            taxes_text="+₹ 52 taxes and charges",
            extra="",
        )
    )
    row = parse_rooms(html)[0]
    assert row["rooms_available"] == 5
    assert row["qty_selectable_max"] == 5
    assert row["availability_source"] == "room_selector"
    assert row["is_capped"] is True


def test_row_without_any_numeric_signal_is_flagged_not_invented() -> None:
    """Listed row, no price/selector/scarcity: honest unknown, NOT sold_out."""
    html = page_with(
        ROW_TEMPLATE.format(
            room_id="1",
            name="Mystery Room",
            selector="",
            price="",
            taxes_raw="",
            taxes_text="",
            extra="",
        )
    )
    row = parse_rooms(html)[0]
    assert row["status"] == "ok"  # sold_out requires observed evidence
    assert row["rooms_available"] is None  # never fabricated
    assert row["availability_source"] == "no_numeric_signal"
    assert row["min_price_inr"] is None
    assert row["error_detail"]  # caveat recorded (spec §9)


def test_price_without_selector_is_recorded_with_no_numeric_signal() -> None:
    html = page_with(
        ROW_TEMPLATE.format(
            room_id="1",
            name="Price Only Room",
            selector="",
            price="₹ 777",
            taxes_raw="40",
            taxes_text="+₹ 40 taxes and charges",
            extra="",
        )
    )
    row = parse_rooms(html)[0]
    assert row["status"] == "ok"
    assert row["min_price_inr"] == 777
    assert row["rooms_available"] is None
    assert row["availability_source"] == "no_numeric_signal"


def test_scarcity_wording_inside_a_row_is_used_as_evidence() -> None:
    # The wording is real Booking.com copy observed on search cards
    # (m0_findings §8 row 5); the in-row placement is a code-path test
    # only — the fixture never showed scarcity on the room table.
    html = page_with(
        ROW_TEMPLATE.format(
            room_id="9",
            name="Rare Room",
            selector=selector_with(1),
            price="₹ 999",
            taxes_raw="9",
            taxes_text="+₹ 9 taxes and charges",
            extra="<span>We have 1 left at this price</span>",
        )
    )
    row = parse_rooms(html)[0]
    assert row["rooms_available"] == 1
    assert row["availability_source"] == "scarcity_message"
    assert row["scarcity_message"] == "We have 1 left at this price"


def test_room_row_without_a_name_raises_structure_error() -> None:
    """Losing a displayed category silently would under-report inventory."""
    html = page_with('<tr class="e2e-hprt-table-row"><td>no name</td></tr>')
    with pytest.raises(StructureError, match="no readable room name"):
        parse_rooms(html)


def test_page_without_room_rows_returns_empty_list() -> None:
    assert parse_rooms("<html><body><p>nothing here</p></body></html>") == []
