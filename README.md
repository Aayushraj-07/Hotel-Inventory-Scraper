# Hotel Inventory Scraper — Booking.com (Phase 1)

Command-line Python application that records, for one Booking.com property,
the room categories shown as bookable for one-night stays (2 adults, 1 room,
INR) across 30 consecutive check-in dates, writing one CSV per run.

**Non-negotiables:** public data only · no login · no personal data · ≥3 s
between page loads · max 3 attempts per date · CAPTCHA/WAF/bot blocks stop
the activity and are **never bypassed** · nothing is invented — unobserved
values stay empty · every row states how it was acquired.

> **Target disclosure:** the assignment's named property ("Hotel Shiv Vilas,
> Tapovan, Rishikesh") was **not found on Booking.com** during manual M0
> verification. With developer approval the scraper runs against the verified
> listing found instead — **"Around Stays-Heaven And Harmony", Tapovan,
> Rishikesh**. Evidence: `docs/m0_findings.md` §1–§2; decision D6 in
> `docs/decisions.md`.

---

## 1. Overview

The pipeline is: configuration → one property selected → one of three
acquisition modes → per-date fetch/parse → per-attempt access diagnostics →
row validation → one labelled CSV + one log file.

The upgrade adds three things to the original Phase 1 deliverable:

1. **Three acquisition modes** — `live` (ordinary Playwright, public page),
   `fixture` (nine deterministic synthetic scenarios + saved evidence), and
   `snapshot` (locally saved HTML: one file, or a directory batch with one
   file per check-in date).
2. **Centralised access-state classification** (`scraper/access_state.py`) —
   one 12-state vocabulary, one multi-indicator classifier, one redacting
   diagnostic record, one stop rule shared by live and offline paths.
3. **Per-state retries and honest provenance** — network/timeout/missing-data
   failures are retried; structural/permanent failures are not; blocks stop
   the run; and every CSV row carries `data_source=live|fixture|snapshot`.

## 2. Installation

Requirements: **Python 3.10+**. Internet is needed for `pip` and for live
runs only; tests and both offline modes are fully offline.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium     # live runs + one offline browser test
.venv/bin/python -m pytest -q   # 303 tests, no Booking.com access
```

Pinned dependencies: `pyyaml`, `playwright`, `beautifulsoup4`, `pytest`.

## 3. Configuration — `config/config.yaml`

No secrets or personal data in this file.

```yaml
settings:
  adults: 2                     # fixed by the assignment
  rooms: 1                      # fixed by the assignment
  stay_nights: 1                # fixed by the assignment
  currency: INR                 # fixed by the assignment
  days_to_scrape: 30            # tomorrow .. tomorrow+29 (D1)
  page_load_delay_seconds: 3    # minimum between page loads
  max_attempts_per_date: 3      # total attempts per date (D2)
  timezone: Asia/Kolkata
properties:
  - property_id: heaven_and_harmony_tapovan
    property_name: Around Stays-Heaven And Harmony
    location: Tapovan, Rishikesh, India
    booking_url: https://www.booking.com/hotel/in/heaven-and-harmony-tapovan.en-gb.html
    booking_property_id: "17328907"
    enabled: true
```

Startup validation enforces 2 adults / 1 room / 1 night / INR, delay ≥ 3 s,
attempts ≤ 3, exactly one enabled property, and a listing reference before
any browser work. Invalid config exits **2**. Adding a hotel later is a new
record, not new code.

## 4. Running the CLI

```bash
# 1. Live: ONE ordinary headless Playwright run (never --headed)
python main.py --config config/config.yaml --mode live

# 2. Offline synthetic/evidence fixtures (no browser, no network)
python main.py --config config/config.yaml --mode fixture --scenario available
python main.py --config config/config.yaml --mode fixture --scenario sold_out

# 3a. Offline snapshot: one locally saved HTML file -> one check-in date
python main.py --config config/config.yaml --mode snapshot \
  --input tests/fixtures/synthetic/available.html --checkin 2026-12-01

# 3b. Offline snapshot BATCH: a directory of saved pages, one per date
python main.py --config config/config.yaml --mode snapshot --input snapshots/

# Legacy alias, byte-identical to the original sample run:
python main.py --config config/config.yaml --sample-from-fixture
```

**Snapshot date rules:** with a single file, `--checkin YYYY-MM-DD` sets the
date (optional — auto-detected from a `checkin` marker in the HTML, then
from the file name; if neither works it exits 2). With a **directory**,
`--checkin` is rejected: every file's own date is detected, and each date
must have exactly one file (duplicates and undetectable dates exit 2).

**Collecting real per-date data (batch):** open the listing in your own
working browser, set the search dates, wait for the room table, then save
the page into one folder — repeat once per check-in date. Name the files
with the date (`booking_2026-12-01.html`) so detection never has to guess.
Run the command above; you get **one merged CSV** with genuine per-date
rows, each labelled `data_source=snapshot`. The scraper itself makes no
network request.

There is deliberately **no `--headed` flag**. All runs are headless; the CLI
rejects unknown flags.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | success — every date produced `ok` / `sold_out` / `no_rooms_listed` |
| 1 | unexpected error or row-contract violation (no invalid CSV written) |
| 2 | invalid config/arguments, missing snapshot/dir, duplicate or undetectable snapshot dates |
| 3 | listing verification failed, identity mismatch, or an access block stopped the run |
| 4 | CSV written, but ≥ 1 date ended in `error` |

## 5. Acquisition modes

| Mode | Source | Dates | `data_source` | Filename suffix |
|------|--------|-------|---------------|-----------------|
| `live` | public Booking.com page via ordinary Playwright | 30 | `live` | none |
| `fixture` | local test HTML (`evidence` + 9 synthetic scenarios) | 30 | `fixture` | `_FIXTURE` (`_SAMPLE` via legacy flag) |
| `snapshot` | local HTML supplied with `--input`: one file **or** a directory batch | **1 per file** | `snapshot` | `_SNAPSHOT` |

Fixture and snapshot modes use the *same* classifier, parser, validator and
writer as live mode. A challenge fixture therefore **stops** exactly like a
live challenge — no offline path may parse a WAF/CAPTCHA page as inventory.

The nine synthetic scenarios are `available`, `sold_out`, `no_rooms`,
`waf_challenge`, `captcha`, `bot_protection`, `http_denied`,
`property_mismatch`, `structure_changed`. See
`tests/fixtures/synthetic/README.md` and `manifest.json`. They are test data,
never live observations.

## 6. Output — files and CSV contract

One CSV per run in `output/`, named
`<property_id>_inventory_<YYYYMMDD_HHMM>[_FIXTURE|_SNAPSHOT|_SAMPLE].csv`
(timestamp in Asia/Kolkata). The stem follows the property actually scraped —
a recorded deviation from the spec's fixed `shivvilas_*` stem (D14).

Exactly **8 required columns** in spec order, then **3 bonus columns**:

| # | Column | Content |
|---|--------|---------|
| 1 | `scrape_timestamp` | ISO 8601 with India offset (`+05:30`) |
| 2 | `checkin_date` | `YYYY-MM-DD` |
| 3 | `checkout_date` | check-in + `stay_nights` |
| 4 | `room_category` | exact displayed name (empty for date-level records) |
| 5 | `rooms_available` | observed integer, empty = unknown, **never invented** |
| 6 | `availability_source` | evidence vocabulary below |
| 7 | `min_price_inr` | displayed stay total in INR, **taxes excluded** |
| 8 | `status` | `ok` · `sold_out` · `no_rooms_listed` · `error` |
| 9 | `error_detail` | why a value is missing / what failed (bonus) |
| 10 | `is_capped` | `true` when the count came from a selector maximum (bonus) |
| 11 | `data_source` | `live` · `fixture` · `snapshot` (bonus, D16) |

`availability_source` vocabulary: `room_selector`, `scarcity_message`,
`no_numeric_signal`, `no_rooms_listed`, `page_error`. `not_bookable` is
reserved and never emitted. `sold_out` is emitted **only** when a room row's
quantity selector offers a maximum of 0 (D15, synthetic-validated).

## 7. Logging and per-attempt diagnostics

Console + `logs/run_<timestamp>.log`. Every page attempt emits one
structured `AccessDiagnostic` record containing: attempt number, requested
(final URL when available), HTTP status when available, page title,
navigation error, detected state, indicator evidence, whether room
information was present, and whether extraction succeeded.

Diagnostics are redacted on **every** assignment: only allowlisted booking
query parameters survive, userinfo is stripped, and free text is scrubbed
of URLs and `token=…`/`cookie=…`-style pairs. No cookies, headers, session
IDs or personal data are logged. Per-room evidence (`room_type_id`, selector
maximum, taxes, scarcity wording) is logged separately for spot checks.

## 8. Access states, retries and the no-bypass policy

Central classification lives in `scraper/access_state.py` (12 states).
HTTP statuses are authoritative on their own. Text/markup findings need at
least **two independent indicator sources** before a page is classified;
one unreliable text match never triggers a block.

| State(s) | Action |
|----------|--------|
| `NETWORK_ERROR`, `NAVIGATION_TIMEOUT`, `ROOM_DATA_NOT_LOADED`, `UNKNOWN_ERROR` | retry up to `max_attempts_per_date` (≤3) |
| `PAGE_STRUCTURE_CHANGED`, `EXTRACTION_ERROR`, `PROPERTY_NOT_FOUND`, `PROPERTY_MISMATCH` | no retry; honest `error` row (or exit 3 at verification) |
| `HTTP_ACCESS_DENIED`, `BOT_PROTECTION`, `CAPTCHA_PRESENT`, `AWS_WAF_CHALLENGE` | **stop all further page loads immediately** — no retry, no bypass |

A block still produces an `error` row for the affected date and for every
unattempted date, so the CSV always covers all generated dates. The run
exits 3 and the log states plainly that no bypass was attempted.

## 9. Architecture

```
main.py ── config/property/date services
       ── mode dispatch: live | fixture | snapshot
       ── BrowserSession (live): ONE shared ≥3 s rate limiter, block detection
       ── access_state: classify → diagnostic → central stop rule
       ── booking_scraper: per-date URL (observed params) → rendered HTML
       ── availability_parser: pure HTML → observations (fixture-tested)
       ── inventory_service: per-state retries, isolation, statuses, rows
       ── validation_service → csv_service (exact 11-column contract)
```

Selectors and wording come only from sanitized real-page evidence
(`docs/m0_findings.md` §8.5). One offline end-to-end test uses a real
headless browser against a local file; it never contacts Booking.com.

## 10. Tests and fixtures

```bash
.venv/bin/python -m pytest -q     # 303 passed
```

- `tests/test_access_state.py` — classifier, redaction, diagnostics, mapping.
- `tests/test_inventory.py` — per-state retry table, `data_source`, block stop.
- `tests/test_fixtures.py` / `test_snapshot.py` / `test_batch_snapshot.py` /
  `test_cli.py` — mode wiring, single and batch snapshots, CLI rules.
- `tests/test_parser.py` — real fixture ground truth + synthetic `sold_out`.
- `tests/fixtures/` — sanitized real pages plus nine synthetic scenarios.

## 11. The six written answers

**Q1 — How did you find rooms available, and why?**
The per-room quantity dropdown (`select.hprt-nos-select`, options `0…N`);
the recorded count is the maximum selectable N, with
`availability_source=room_selector` and `is_capped=true`. It is the only
numeric signal observed on the real room table (maxima 2, 7, 2, 3).
Scarcity wording was seen on search-result cards only, so it is used only
if it ever appears inside a room row. A selector maximum is a platform cap,
not physical inventory — hence `is_capped`. `sold_out` means an observed
selector maximum of 0 only (D15).

**Q2 — When could `rooms_available` be wrong? How would you spot it?**
Capped selectors (systematic, flagged); stale/asynchronous rendering (the
fetch waits for room rows and refuses unrendered pages); changes between
observation and booking (the CSV is timestamped evidence, not truth);
multiple rate plans per row (would be visible in logged evidence and break
fixture tests). Spot checks: per-category evidence logs, strict row
validation, and day-over-day comparison (prices flicker independently of
inventory, so price changes are not bookings).

**Q3 — What breaks first if Booking.com changes its page?**
The `e2e-*`/`hprt-*` class anchors in `scraper/availability_parser.py`, then
URL query names, then challenge markers. All are constants in the
scraper/parser layer; a change produces precise failing fixture tests, and
the fix is a new sanitized fixture plus updated constants — no business
logic edits.

**Q4 — Could daily CSVs show a room got booked?**
A drop or disappearance of `rooms_available` for the same
`(checkin_date, room_category)` across days is a *possible* signal, not
proof. OTA allocation changes, restrictions, delisting or presentation
changes produce the same pattern. Price movement alone proves nothing.

**Q5 — How would this cover 50 hotels?**
Property records are already config-driven and passed as a `Property`
object — no hotel-specific code exists. Scaling would add per-property
verification/results, error isolation, one **shared** rate limiter for all
navigation (the compliance-critical piece), sequential processing first,
then bounded concurrency under that limiter, one CSV per property, and
monitoring on exit codes. Not implemented in Phase 1.

**Q6 — Assumptions and one more day?**
30 dates = tomorrow…tomorrow+29; 3 attempts = 3 total per date; property
substitution; displayed price = stay total excluding taxes; selector counts
are capped; offline rows are labelled, never live. With one more day:
complete exactly one compliant headless live attempt (Stage 10), then
prioritise real sold-out/scarcity fixtures, a second-date fixture, and CI.

## 12. Limitations, troubleshooting and repository map

- **Automated live access has been blocked.** Booking.com's AWS WAF served
  an HTTP 202 challenge (`awsWafCookieDomainList`) on 2026-10-31, 11-01,
  11-02 and the final 2026-11-23 attempt; each run stopped at verification
  within ~1.3 s (exit 3). No bypass was attempted. Human browser access
  works normally. Live mode is code-complete and offline-tested, but no
  successful live collection is claimed. Evidence: `docs/m0_findings.md` §7,
  curated logs and `SUBMISSION_REPORT.md` §8.
- Counts are capped selector maxima; one currency/occupancy/night length;
  multi-property execution is out of scope.
- The final live validation attempt was performed once, headlessly and
  human-initiated, on **2026-11-23 at 14:02 IST**: it stopped at listing
  verification in ~1.2 s with `AWS_WAF_CHALLENGE` (HTTP 202) and exit 3.
  No room page was fetched and no bypass was attempted. Live collection is
  therefore still unconfirmed here; full verbatim evidence is in
  `SUBMISSION_REPORT.md` §8.

| Symptom | Cause / fix |
|---------|-------------|
| Exit 2 "Configuration error" | invalid YAML/rules, missing fixture/snapshot, or undetectable snapshot date (pass `--checkin`) |
| Exit 3 "BLOCKED…" | WAF/CAPTCHA/bot/HTTP denial detected — expected in this environment. Do not bypass; use `--mode fixture` |
| Exit 3 "verification failed" | page/property identity changed — re-verify the listing manually and update config |
| Exit 4 "failed date(s)" | CSV written; inspect `error_detail` and `logs/run_*.log` |
| Browser test skips | run `playwright install chromium`; all other tests stay offline |

```
main.py                       CLI + three-mode dispatch
config/config.yaml            settings and property records
scraper/access_state.py       12 states, classifier, redaction, diagnostics
scraper/browser.py            Playwright session + shared rate limiter
scraper/hotel_finder.py       startup listing verification
scraper/booking_scraper.py    per-date URL + navigation
scraper/availability_parser.py pure HTML → observations
services/fixture_service.py   nine synthetic scenarios + evidence
services/snapshot_service.py  single-file and batch (per-date) local snapshots
services/inventory_service.py per-state retries, statuses, rows
services/validation_service.py CSV contract validation
services/csv_service.py       writer + mode-specific filenames
tests/                        303 offline tests + sanitized fixtures
docs/decisions.md             decisions D1–D17 (D15/D16 supersede D13/D3)
docs/m0_findings.md           live evidence and block event
output/  logs/                generated CSVs and run logs
```
