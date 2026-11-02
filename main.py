#!/usr/bin/env python3
"""Hotel Inventory Scraper — Phase 1 CLI entry point.

Flow (spec §13):
    * CLI parsing (--config, --sample-from-fixture)
    * configuration loading and validation (fail fast)
    * Phase 1 property selection (exactly one enabled property)
    * listing verification (live mode) or fixture mode (spec §16 sample)
    * check-in / check-out date generation (spec §5.4)
    * per-date collection with retries and block-stop (M6, spec §6/§10)
    * row validation + one CSV per run (spec §8)
    * logging to console + logs/ file, summary + exit code
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from services.csv_service import (
    OUTPUT_COLUMNS,
    REQUIRED_COLUMNS,
    output_path,
    sample_output_path,
    write_csv,
)
from services.date_service import checkout_for, generate_checkin_dates
from services.inventory_service import collect_inventory
from services.property_service import (
    ConfigError,
    load_config,
    require_listing_reference,
    select_enabled_property,
    validate_config,
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

# Saved, sanitized page evidence used by --sample-from-fixture (spec §16).
SAMPLE_FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "live_2026-11-01_roomtable.html"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Hotel Inventory Scraper (Phase 1: Hotel Shiv Vilas, Booking.com)"
        ),
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to the YAML configuration file (default: config/config.yaml)",
    )
    parser.add_argument(
        "--sample-from-fixture",
        action="store_true",
        help=(
            "Run fully OFFLINE against the sanitized saved page evidence "
            "(tests/fixtures): no browser, no network, no Booking.com access. "
            "Produces a clearly labelled *_SAMPLE.csv (spec §16) — useful while "
            "automated live access is blocked (docs/m0_findings.md §7)."
        ),
    )
    return parser


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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

        # --- Date generation (spec §5.4: exactly 30 dates) ---------------
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

        # --- Collection (spec §6, §10) ------------------------------------
        if args.sample_from_fixture:
            # Offline sample mode (spec §16): saved page evidence, no network.
            if not SAMPLE_FIXTURE.is_file():
                log.error("Sample fixture missing: %s", SAMPLE_FIXTURE)
                return EXIT_CONFIG_ERROR
            log.warning(
                "SAMPLE MODE: offline run against saved page evidence %s — no "
                "listing verification, no browser, no Booking.com access. Every "
                "date repeats that one saved page: rows are clearly labelled "
                "sample data, NOT live observations (spec §16).",
                SAMPLE_FIXTURE,
            )
            fixture_html = SAMPLE_FIXTURE.read_text(encoding="utf-8")
            result = collect_inventory(
                property_record=property_record,
                settings=settings,
                checkin_dates=checkin_dates,
                fetch_html=lambda _url: fixture_html,
                now=observation_now,
            )
            csv_path = sample_output_path(run_time, property_record.property_id)
        else:
            # Live mode: verify the listing first, then collect (spec §13).
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
                    now=observation_now,
                )
            csv_path = output_path(run_time, property_record.property_id)

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
        log.error("BLOCKED during listing verification (spec §4): %s", exc)
        log.error(
            "Stopped without any bypass attempt. This event must be explained "
            "in the README/run report."
        )
        return EXIT_VERIFICATION_FAILED
    except VerificationError as exc:
        log.error("Listing verification failed: %s", exc)
        return EXIT_VERIFICATION_FAILED
    except ScraperError as exc:
        log.error(
            "Listing verification could not complete [%s]: %s", exc.category, exc
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
