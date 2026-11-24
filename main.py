#!/usr/bin/env python3
"""Hotel Inventory Scraper — Phase 1 CLI entry point.

Flow (spec §13):
    * CLI parsing (--config, --mode live|fixture|snapshot, --input,
      --scenario; legacy --sample-from-fixture alias) + cross-argument
      validation (assignment §8)
    * configuration loading and validation (fail fast)
    * Phase 1 property selection (exactly one enabled property)
    * acquisition by mode: live listing verification (spec §13), offline
      fixture run (spec §16), or locally saved HTML snapshot(s) — one
      file for one check-in date, or a directory merged batch
    * check-in / check-out date generation (spec §5.4)
    * per-date collection with retries and block-stop (M6, spec §6/§10)
    * row validation + one CSV per run, labelled by mode (spec §8)
    * logging to console + logs/ file, summary + exit code
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from services.csv_service import (
    OUTPUT_COLUMNS,
    REQUIRED_COLUMNS,
    fixture_output_path,
    output_path,
    sample_output_path,
    snapshot_output_path,
    write_csv,
)
from services.date_service import checkout_for, generate_checkin_dates
from services.fixture_service import (
    classify_fixture,
    fixture_fetch,
    load_fixture,
    verify_fixture_identity,
)
from services.inventory_service import collect_inventory
from services.property_service import (
    ConfigError,
    load_config,
    require_listing_reference,
    select_enabled_property,
    validate_config,
)
from services.snapshot_service import (
    Snapshot,
    load_snapshot,
    load_snapshot_dir,
    snapshot_fetch_all,
)
from services.validation_service import ValidationError, validate_rows
from scraper.browser import BrowserSession
from scraper.booking_scraper import fetch_property_html
from scraper.exceptions import BlockedError, ScraperError, VerificationError
from scraper.hotel_finder import verify_listing

# Exit codes (documented for the README in M8):
#   0 = success: every date produced observations (ok / no_rooms_listed)
#   1 = unexpected error (bug or unhandled condition)
#   2 = invalid/missing configuration or missing listing reference
#   3 = listing verification failed (wrong/changed page, HTTP error,
#       CAPTCHA/bot block during verification or scraping — never bypassed)
#   4 = run completed and CSV written, but >= 1 date ended in `error`
#       (spec §13: do not report full success when dates failed)
EXIT_OK = 0
EXIT_UNEXPECTED_ERROR = 1
EXIT_CONFIG_ERROR = 2
EXIT_VERIFICATION_FAILED = 3
EXIT_DATES_FAILED = 4

log = logging.getLogger("hotel_scraper")

# Fixture scenario registry (assignment §3.B) lives in fixture_service so
# the CLI, tests and fixture README always agree on the same nine synthetic
# scenarios plus the saved real-page evidence. All are test data, never
# live results.
DEFAULT_SCENARIO = "evidence"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Hotel Inventory Scraper (Phase 1: Booking.com, one configured property)"
        ),
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to the YAML configuration file (default: config/config.yaml)",
    )
    parser.add_argument(
        "--mode",
        choices=("live", "fixture", "snapshot"),
        default="live",
        help=(
            "Data acquisition mode: 'live' (ordinary Playwright access to "
            "Booking.com), 'fixture' (deterministic offline scenarios from "
            "tests/fixtures), 'snapshot' (locally saved HTML file(s)). "
            "Default: live."
        ),
    )
    parser.add_argument(
        "--input",
        default=None,
        metavar="PATH",
        help=(
            "Local saved HTML to process in snapshot mode: a single .html "
            "file (one check-in date, use --checkin if not detectable) or a "
            "DIRECTORY of .html files, one per check-in date (batch merge). "
            "Required with --mode snapshot; ignored otherwise."
        ),
    )
    parser.add_argument(
        "--scenario",
        default=None,
        metavar="NAME",
        help=(
            "Fixture scenario name (fixture mode; default: 'evidence'). "
            "Nine synthetic scenarios (available, sold_out, no_rooms, "
            "waf_challenge, captcha, bot_protection, http_denied, "
            "property_mismatch, structure_changed) plus the saved evidence "
            "page are available; see tests/fixtures/synthetic/README.md."
        ),
    )
    parser.add_argument(
        "--checkin",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "Check-in date the saved snapshot represents (snapshot mode). "
            "Auto-detected from the HTML when omitted."
        ),
    )
    parser.add_argument(
        "--sample-from-fixture",
        action="store_true",
        help=(
            "Legacy alias for '--mode fixture --scenario evidence': runs "
            "OFFLINE against the sanitized saved page evidence "
            "(tests/fixtures/live_2026-11-01_roomtable.html). Produces the "
            "clearly labelled *_SAMPLE.csv (spec §16)."
        ),
    )
    return parser


def validate_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> argparse.Namespace:
    """Cross-argument validation with helpful errors (assignment §8).

    argparse itself rejects unknown modes; this covers the combinations
    that only make sense together. ``parser.error`` exits with code 2,
    matching EXIT_CONFIG_ERROR.
    """
    if args.sample_from_fixture:
        # Legacy alias wins over --mode; keep behaviour byte-identical to
        # the pre-mode CLI (same fixture, same _SAMPLE label).
        if args.mode not in ("live", "fixture"):
            parser.error(
                "--sample-from-fixture cannot be combined with --mode snapshot."
            )
        args.mode = "fixture"
        args.scenario = "evidence"
    if args.mode == "snapshot":
        if args.sample_from_fixture:
            parser.error("--sample-from-fixture cannot be used with snapshot mode.")
        if not args.input:
            parser.error("--mode snapshot requires --input <html-file-or-directory>.")
        if args.scenario:
            parser.error("--scenario applies to fixture mode only, not snapshot.")
        if args.checkin is not None:
            if Path(args.input).is_dir():
                parser.error(
                    "--checkin applies to a single snapshot file only; with a "
                    "directory each file's own check-in date is used."
                )
            try:
                date.fromisoformat(args.checkin)
            except ValueError:
                parser.error(
                    f"--checkin must be an ISO date (YYYY-MM-DD), got {args.checkin!r}."
                )
    else:
        if args.input:
            parser.error(f"--input applies to snapshot mode only (got --mode {args.mode}).")
        if args.checkin:
            parser.error(f"--checkin applies to snapshot mode only (got --mode {args.mode}).")
    return args


def _log_formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    )


def configure_console_logging() -> None:
    """Attach a console handler once, so config errors are always visible."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_log_formatter())
    root.addHandler(handler)


def configure_file_logging(run_time: datetime) -> Path:
    """Attach a file handler under logs/ using the configured time zone."""
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"run_{run_time.strftime('%Y%m%d_%H%M')}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(_log_formatter())
    logging.getLogger().addHandler(handler)
    return log_path


# --- Acquisition modes (assignment §3) -------------------------------------


def _run_live(
    *,
    property_record,
    settings,
    checkin_dates,
    run_time: datetime,
    now,
):
    """Ordinary Playwright access: verify the listing, then collect (spec §13)."""
    log.info("Verifying public listing: %s", property_record.booking_url)
    with BrowserSession(settings.page_load_delay_seconds) as session:
        verification = verify_listing(property_record, session)
        log.info(
            "Listing verified | property_id=%s | matched via %s | title=%r",
            verification.property_id,
            verification.matched_via,
            verification.page_title,
        )
        if verification.canonical_url:
            log.info("Canonical URL: %s", verification.canonical_url)

        result = collect_inventory(
            property_record=property_record,
            settings=settings,
            checkin_dates=checkin_dates,
            fetch_html=lambda url: fetch_property_html(session, url),
            now=now,
        )
    return result, output_path(run_time, property_record.property_id)


def _run_fixture(
    *,
    args,
    property_record,
    settings,
    checkin_dates,
    run_time: datetime,
    now,
):
    """Deterministic offline run against a registered fixture (assignment §3.B).

    No browser, no network, no Booking.com access. Every date repeats the
    one saved page: rows are fixture/test data, NEVER live observations —
    labelled by the output suffix (_SAMPLE for the legacy flag, _FIXTURE
    for --mode fixture) and by ``data_source=fixture``. Challenge fixtures
    go through the central classifier and stop just like a live block.
    """
    scenario = args.scenario or DEFAULT_SCENARIO
    try:
        fixture = load_fixture(scenario)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise ConfigError(str(exc)) from exc

    verify_fixture_identity(fixture, property_name=property_record.property_name)
    state = classify_fixture(fixture, url=fixture.path.resolve().as_uri())
    if state is not None:
        log.info(
            "Fixture %s classified as %s (%s) — no extraction attempted",
            scenario,
            state.value,
            fixture.description,
        )

    if args.sample_from_fixture:
        # Legacy flag: byte-identical behaviour and label to the pre-mode CLI.
        log.warning(
            "SAMPLE MODE: offline run against saved page evidence %s — no "
            "listing verification, no browser, no Booking.com access. Every "
            "date repeats that one saved page: rows are clearly labelled "
            "sample data, NOT live observations (spec §16).",
            fixture.path,
        )
        label_path = sample_output_path
    else:
        log.warning(
            "FIXTURE MODE (scenario=%s): offline run against test fixture %s — "
            "synthetic/evidence test data, NOT live Booking.com inventory. "
            "Same parse/validate/CSV pipeline as live mode.",
            scenario,
            fixture.path,
        )
        label_path = fixture_output_path

    result = collect_inventory(
        property_record=property_record,
        settings=settings,
        checkin_dates=checkin_dates,
        fetch_html=fixture_fetch(fixture),
        now=now,
        data_source="fixture",
    )
    return result, label_path(run_time, property_record.property_id)


def _run_snapshot(
    *,
    snapshots: list[Snapshot],
    property_record,
    settings,
    checkin_dates,
    run_time: datetime,
    now,
):
    """Process locally saved HTML (one date, or a batch of them — §3.C)."""
    if len(snapshots) == 1:
        log.info(
            "Snapshot input: 1 saved page -> 1 check-in date (%s)",
            snapshots[0].checkin.isoformat(),
        )
    else:
        log.info(
            "Snapshot batch: %d saved pages -> %d check-in dates (%s .. %s)",
            len(snapshots),
            len(checkin_dates),
            snapshots[0].checkin.isoformat(),
            snapshots[-1].checkin.isoformat(),
        )
    result = collect_inventory(
        property_record=property_record,
        settings=settings,
        checkin_dates=checkin_dates,
        fetch_html=snapshot_fetch_all(snapshots),
        now=now,
        data_source="snapshot",
    )
    return result, snapshot_output_path(run_time, property_record.property_id)


def _run_acquisition(
    *, args, property_record, settings, checkin_dates, run_time: datetime, now,
    snapshots: list[Snapshot] | None = None,
):
    """Dispatch to exactly one acquisition mode (assignment §3)."""
    if args.mode == "fixture":
        return _run_fixture(
            args=args,
            property_record=property_record,
            settings=settings,
            checkin_dates=checkin_dates,
            run_time=run_time,
            now=now,
        )
    if args.mode == "snapshot":
        if not snapshots:  # defensive: main loads them before date generation
            raise ConfigError("snapshot mode requires loaded snapshot file(s).")
        return _run_snapshot(
            snapshots=snapshots,
            property_record=property_record,
            settings=settings,
            checkin_dates=checkin_dates,
            run_time=run_time,
            now=now,
        )
    return _run_live(
        property_record=property_record,
        settings=settings,
        checkin_dates=checkin_dates,
        run_time=run_time,
        now=now,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args = validate_args(parser, args)  # cross-mode rules; errors exit 2
    configure_console_logging()

    # --- Configuration loading + validation (fail fast, spec §13) -------
    try:
        raw_config = load_config(args.config)
        settings, properties = validate_config(raw_config)
        property_record = select_enabled_property(properties)
        # Automated startup check (decision 7): a listing reference is
        # mandatory before any browser work (spec §5.2, §13 step 4).
        require_listing_reference(property_record)
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return EXIT_CONFIG_ERROR

    tz = ZoneInfo(settings.timezone)
    run_time = datetime.now(timezone.utc).astimezone(tz)
    log_path = configure_file_logging(run_time)

    try:
        log.info(
            "Run started | config=%s | property_id=%s (%s, %s)",
            args.config,
            property_record.property_id,
            property_record.property_name,
            property_record.location,
        )

        # --- Date generation (spec §5.4: 30 dates; snapshot: file/dir) ----
        snapshots: list[Snapshot] = []
        if args.mode == "snapshot":
            input_path = Path(args.input)
            if input_path.is_dir():
                if args.checkin:
                    raise ConfigError(
                        "--checkin is for a single snapshot file; in a directory "
                        "each file's own check-in date is used."
                    )
                snapshots = load_snapshot_dir(
                    input_path, property_name=property_record.property_name
                )
            else:
                snapshots = [
                    load_snapshot(
                        input_path,
                        requested_checkin=args.checkin,
                        property_name=property_record.property_name,
                    )
                ]
            checkin_dates = [snap.checkin for snap in snapshots]
        else:
            checkin_dates = generate_checkin_dates(
                settings.days_to_scrape,
                settings.timezone,
                settings.stay_nights,
            )
        first, last = checkin_dates[0], checkin_dates[-1]

        log.info(
            "Search parameters: adults=%d rooms=%d stay_nights=%d currency=%s",
            settings.adults,
            settings.rooms,
            settings.stay_nights,
            settings.currency,
        )
        log.info(
            "Planned check-in dates: %d (%s .. %s); last checkout: %s",
            len(checkin_dates),
            first.isoformat(),
            last.isoformat(),
            checkout_for(last, settings.stay_nights).isoformat(),
        )
        log.info(
            "CSV contract: %d required columns + bonus columns %s",
            len(REQUIRED_COLUMNS),
            OUTPUT_COLUMNS[len(REQUIRED_COLUMNS):],
        )

        observation_now = lambda: datetime.now(timezone.utc).astimezone(tz)

        # --- Acquisition (assignment §3): exactly one mode per run --------
        result, csv_path = _run_acquisition(
            args=args,
            property_record=property_record,
            settings=settings,
            checkin_dates=checkin_dates,
            run_time=run_time,
            now=observation_now,
            snapshots=snapshots,
        )

        # --- Validation + CSV (spec §8, §13 steps 7-9) --------------------
        validate_rows(result.rows, checkin_dates, settings.stay_nights)
        write_csv(result.rows, csv_path)
        log.info("CSV written: %s (%d rows)", csv_path, len(result.rows))
        log.info(
            "Run summary | dates_ok=%d | dates_no_rooms=%d | dates_failed=%d "
            "| blocked=%s",
            result.dates_ok,
            result.dates_no_rooms,
            result.dates_failed,
            result.blocked,
        )

        if result.blocked:
            log.error(
                "Run stopped by an access block (spec §4): %s", result.blocked_detail
            )
            log.error(
                "No bypass was attempted; this event must be explained in the "
                "README/run report."
            )
            log.info("Run finished | log=%s", log_path)
            return EXIT_VERIFICATION_FAILED
        if result.dates_failed:
            log.warning(
                "Completed with %d failed date(s) — this is NOT a fully "
                "successful run (spec §13).",
                result.dates_failed,
            )
            log.info("Run finished | log=%s", log_path)
            return EXIT_DATES_FAILED

        log.info("Run finished | log=%s", log_path)
        return EXIT_OK
    except BlockedError as exc:
        log.error(
            "BLOCKED during listing verification (spec §4) [%s]: %s",
            exc.access_state.value if exc.access_state else "BLOCKED",
            exc,
        )
        log.error(
            "Stopped without any bypass attempt. This event must be explained "
            "in the README/run report."
        )
        return EXIT_VERIFICATION_FAILED
    except VerificationError as exc:
        log.error(
            "Listing verification failed [%s]: %s",
            exc.access_state.value if exc.access_state else "UNKNOWN_ERROR",
            exc,
        )
        return EXIT_VERIFICATION_FAILED
    except ScraperError as exc:
        log.error(
            "Listing verification could not complete [%s/%s]: %s",
            exc.category,
            exc.access_state.value if exc.access_state else "UNKNOWN_ERROR",
            exc,
        )
        return EXIT_VERIFICATION_FAILED
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return EXIT_CONFIG_ERROR
    except ValidationError as exc:
        # Rows violated the CSV contract — an application bug, not a
        # site condition; never write an invalid file (spec §8.7).
        log.error("Row validation failed before CSV write: %s", exc)
        return EXIT_UNEXPECTED_ERROR
    except Exception:  # noqa: BLE001 — top-level guard, logged with traceback
        log.exception("Unexpected error during run")
        return EXIT_UNEXPECTED_ERROR


if __name__ == "__main__":
    sys.exit(main())
