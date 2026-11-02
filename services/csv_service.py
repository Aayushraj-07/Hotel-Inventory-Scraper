"""CSV output contract and serialization (spec §8, §9).

    * §8.2 — exactly eight required columns in exactly this order.
    * §9   — optional bonus columns; Phase 1 decision: error_detail and
              is_capped only, appended *after* the required columns.
    * one file per run, named after the property actually scraped:
      <property_id>_inventory_<YYYYMMDD_HHMM>.csv, timestamped in the
      configured India time zone. (Recorded deviation from spec §8.1's
      fixed ``shivvilas_inventory_*`` stem — see output_filename and
      docs/decisions.md D14.)

Flow: collect rows -> validation_service.validate_rows(...) ->
write_csv(...). The writer never validates row *content* (that is the
validation service's job) but it does guarantee the column contract and
UTF-8 encoding.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

# Spec §8.2: do not rename, reorder or omit these columns.
REQUIRED_COLUMNS = [
    "scrape_timestamp",
    "checkin_date",
    "checkout_date",
    "room_category",
    "rooms_available",
    "availability_source",
    "min_price_inr",
    "status",
]

# Spec §9 + approved decision 4: only these two bonus columns in Phase 1.
BONUS_COLUMNS = [
    "error_detail",
    "is_capped",
]

# Final on-disk column order: required columns first, then bonus columns.
OUTPUT_COLUMNS = REQUIRED_COLUMNS + BONUS_COLUMNS

def output_filename(run_time: datetime, property_id: str) -> str:
    """Build the per-run CSV filename for one property.

    Pattern: ``{property_id}_inventory_<YYYYMMDD_HHMM>.csv`` — derived
    from the **config-driven** property id so each property gets its own
    file (spec §8.5 strategy 1; multi-property-ready, no hardcoded hotel
    name in the writer).

    Recorded deviation from spec §8.1 (developer-directed, 2026-11-02):
    the spec fixed the stem as ``shivvilas_inventory_*`` after the
    assignment's named hotel ("Hotel Shiv Vilas") was found not to exist
    on Booking.com and Phase 1 ran against the verified substitute. The
    developer chose to name output files after the property actually
    scraped instead of the spec's placeholder. See docs/decisions.md D14.

    ``run_time`` must be timezone-aware so the timestamp reflects the
    configured time zone rather than the machine's local zone.
    """
    if run_time.tzinfo is None:
        raise ValueError(
            "run_time must be timezone-aware (configured India time zone, spec §8.1)."
        )
    if not property_id or not str(property_id).strip():
        raise ValueError("property_id is required to build the output filename.")
    return f"{str(property_id).strip()}_inventory_{run_time.strftime('%Y%m%d_%H%M')}.csv"


def output_path(
    run_time: datetime, property_id: str, output_dir: str | Path = "output"
) -> Path:
    """Full output path for a run, creating the directory if needed."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / output_filename(run_time, property_id)


def sample_output_path(
    run_time: datetime, property_id: str, output_dir: str | Path = "output"
) -> Path:
    """Path for a SAMPLE CSV built from saved page evidence (spec §16).

    Spec §16 permits "a clearly labelled sample based on saved page
    evidence if live access is blocked". Resolution (documented decision,
    not silent): keep the per-run stem and append ``_SAMPLE`` so the file
    identifies itself as evidence-based sample data rather than live
    observations (§16: "do not present fabricated data as live
    observations").
    """
    path = output_path(run_time, property_id, output_dir)
    return path.with_name(f"{path.stem}_SAMPLE{path.suffix}")


def _cell(value: object) -> str:
    """Serialize one value for the CSV: None -> empty, bool -> true/false."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_csv(rows: list[dict], path: str | Path) -> Path:
    """Write validated rows to ``path`` with the exact column contract.

    * UTF-8 encoding, header row from ``OUTPUT_COLUMNS`` (required first,
      bonus after — spec §8.2/§9).
    * ``None`` and missing bonus values become empty cells.
    * A missing *required* key raises ``ValueError`` — unknown counts are
      represented by a present-but-empty value, never by an absent column.
    * Extra keys raise ``ValueError`` (no surprise columns).
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"row[{index}] must be a mapping.")
        missing = [column for column in REQUIRED_COLUMNS if column not in row]
        if missing:
            raise ValueError(
                f"row[{index}] missing required column(s): {', '.join(missing)}. "
                "Use None for unknown values instead of omitting a column."
            )

    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=OUTPUT_COLUMNS,
            restval="",       # missing bonus columns -> empty cell
            extrasaction="raise",  # unknown columns -> error
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _cell(value) for key, value in row.items()})

    return destination
