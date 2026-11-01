# Hotel Inventory Scraper — Booking.com (Phase 1)

Command-line Python application that records, for one Booking.com
property, the room categories shown as bookable for one-night stays
(2 adults, 1 room, INR) across 30 consecutive check-in dates, writing
one CSV per run.

Built to the assignment spec (`spec.md`) with these non-negotiables:
**public data only · no login · no personal data · ≥3 s between page
loads · max 3 attempts per date · CAPTCHA/bot blocks stop the run and
are never bypassed · nothing is invented — unobserved values stay empty.**

> **Phase 1 target disclosure:** the assignment's target property
> ("Hotel Shiv Vilas, Tapovan, Rishikesh") was **not found on
> Booking.com** during manual verification. With developer approval,
> Phase 1 runs against the verified listing found instead —
> **"Around Stays-Heaven And Harmony", Tapovan, Rishikesh**. The record
> stays in the config (disabled) for a later re-check. Full evidence:
> `docs/m0_findings.md` §1–§2, decision log: `docs/decisions.md` (D6).

---

## 1. Installation

Requirements: **Python 3.10+**, internet only for `pip` and for live
runs (all tests and the sample mode are fully offline).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium     # browser for live runs + one offline e2e test
```

Dependencies (bounded in `requirements.txt`): `pyyaml`, `playwright`,
`beautifulsoup4` (+ `pytest` for tests).

## 2. Configuration — `config/config.yaml`

No secrets. Global settings + a list of property records (spec §5):

```yaml
settings:
  adults: 2                     # fixed by the assignment
  rooms: 1                      # fixed by the assignment
  stay_nights: 1                # fixed by the assignment
  currency: INR                 # fixed by the assignment
  days_to_scrape: 30            # tomorrow .. tomorrow+29 (spec §5.4)
  page_load_delay_seconds: 3    # assignment minimum
  max_attempts_per_date: 3      # total attempts per date
  timezone: Asia/Kolkata
properties:
  - property_id: heaven_and_harmony_tapovan
    property_name: Around Stays-Heaven And Harmony
    location: Tapovan, Rishikesh, India
    booking_url: https://www.booking.com/hotel/in/heaven-and-harmony-tapovan.en-gb.html
    booking_property_id: "17328907"
    enabled: true
```

Startup validation **enforces** the assignment rules (2 adults / 1 room
/ 1 night / INR, delay ≥ 3 s, attempts ≤ 3) and rejects duplicate
property IDs or anything other than exactly one enabled property
(Phase 1). Errors fail fast with exit code 2. Adding a future hotel =
adding a record; no code changes (spec §5.3).

## 3. Running

```bash
# Live run (browser; requires unblocked Booking.com access):
python main.py --config config/config.yaml

# Offline sample run from sanitized saved page evidence (no network):
python main.py --config config/config.yaml --sample-from-fixture

# Tests (offline; no Booking.com access ever needed):
.venv/bin/python -m pytest -q        # 171 passing
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | success — every date produced `ok` / `no_rooms_listed` rows |
| 1 | unexpected error (incl. a row-contract violation caught before any CSV is written) |
| 2 | invalid/missing configuration or missing listing reference |
| 3 | listing verification failed **or** an access block was encountered — run stopped, no bypass attempted |
| 4 | run completed and the CSV was written, but ≥ 1 date ended in `error` (never report full success when dates failed, spec §13) |

## 4. Output

One CSV per run in `output/`:

- live runs: `shivvilas_inventory_<YYYYMMDD_HHMM>.csv` (spec §8.1)
- sample runs: `shivvilas_inventory_<YYYYMMDD_HHMM>_SAMPLE.csv` —
  the `_SAMPLE` suffix is a deliberate, documented label (spec §16:
  saved-evidence samples must not read as live observations; D12)

Columns — the eight required ones **in spec order**, then the two
approved bonus columns:

| # | Column | Content |
|---|--------|---------|
| 1 | `scrape_timestamp` | ISO 8601 with India offset (`+05:30`) |
| 2 | `checkin_date` | `YYYY-MM-DD` |
| 3 | `checkout_date` | check-in + `stay_nights` |
| 4 | `room_category` | exact displayed name (empty for date-level records) |
| 5 | `rooms_available` | observed integer, or empty = unknown (**never invented**) |
| 6 | `availability_source` | evidence source (vocabulary below) |
| 7 | `min_price_inr` | displayed stay total in INR, **taxes excluded** (empty if absent) |
| 8 | `status` | `ok` · `sold_out` · `no_rooms_listed` · `error` |
| 9 | `error_detail` | why a value is missing / what failed (bonus) |
| 10 | `is_capped` | `true` when the count came from a quantity selector (bonus) |

**Vocabulary — `availability_source`:**
`room_selector` (quantity dropdown maximum — the only signal observed
on the room table so far) · `scarcity_message` (exact in-row scarcity
wording with a number, if ever rendered) · `not_bookable` (reserved;
no such markup observed yet) · `no_numeric_signal` (row listed, no
defensible number) · `no_rooms_listed` (page loaded, zero categories) ·
`page_error` (retries exhausted or blocked).

## 5. Logging

Console + `logs/run_<timestamp>.log`. Every observation is logged with
its evidence (`room_type_id`, selector maximum, taxes, scarcity text),
plus per-date attempts, failure categories, blocks and the run summary.
No secrets, cookies, session IDs or personal data are ever logged.

## 6. How it works (short version)

```
main.py ── config/property/date services
       ── BrowserSession (Playwright; ONE shared ≥3 s rate limiter; block detection)
       ── booking_scraper: per-date URL (observed query params) → navigate →
                          wait for rendered room rows → HTML
       ── availability_parser (pure; works on fixtures and live pages alike)
       ── inventory_service: per-date retries, isolation, block-stop, statuses
       ── validation_service → csv_service (exact column contract)
```

Every HTML selector is traceable to sanitized real-page fixtures in
`tests/fixtures/` (see `docs/m0_findings.md` §8.5). The offline test
suite — including a real headless-browser test that loads the fixture
from disk — fails if any of those anchors change meaning.

## 7. The six assignment questions

### Q1. How did you find the number of rooms available, and why that method?

**Method actually used:** the per-room **quantity dropdown** on the room
table (`<select class="hprt-nos-select">`, options `0…N`); the recorded
count is the maximum selectable option N, with
`availability_source=room_selector` and `is_capped=true`.

**Why:** it is the only numeric signal actually observed on this
property's room table (verified on the saved 2026-11-01 page:
maxima 2, 7, 2, 3 per category). Booking.com's scarcity wording
("We have N left at this price") was observed on search-result *cards*
only — never on the room table — so the parser keys on that exact
wording only if it ever appears inside a room row, and otherwise does
not use it. No count is ever estimated: when no signal exists the cell
stays empty with an explanatory `availability_source`/`error_detail`.

**Reliability / caps:** a dropdown maximum is a *cap*, not an inventory
count — the property may hold more rooms. That is why every
selector-derived count carries `is_capped=true`, so downstream consumers
can treat "7" as "≥ 7 offered, exact number unknown".

### Q2. In which cases could `rooms_available` be wrong? How would you spot it?

- **Selector cap (systematic):** value is a platform-side maximum, not
  physical inventory — always flagged `is_capped=true`.
- **Stale/asynchronous content:** the table can render from recently
  cached availability. Mitigations: we explicitly wait for the room rows
  (never assume navigation = loaded), and a page that delivers no room
  content is recorded as `error`/`missing_data`, not as availability.
- **Between observation and booking:** inventory can change seconds
  after we record it; the number is a timestamped observation, not truth.
- **Room row vs rate plans/blocks:** counts are read per room-category
  row. The captured page had exactly one selector per row; if Booking.com
  ever renders several (per rate plan), the first is used — visible in
  the logged evidence (`room_type_id`, block-level detail) and in fixture
  regression failures.
- **How to spot problems:** every run log records the raw evidence per
  category; validation rejects negatives/duplicates/contract drift;
  fixture tests catch structural changes; day-over-day CSV comparison
  separates count changes from promo/price flicker (the "Late Escape
  Deal" discount was observed appearing/disappearing without any
  inventory change).

### Q3. What would break first if Booking.com changed its page, and how is fixing it made easy?

Most fragile: the room-table **class/attribute anchors**
(`tr.e2e-hprt-table-row`, `a.hprt-roomtype-link`, `select.hprt-nos-select`,
`div.bui-price-display__value`) — Booking.com ships hashed/`e2e-`
class names that may be renamed. Second: the URL query-parameter names,
third: the block-detection markers. All parser-side anchors live as
constants at the top of `scraper/availability_parser.py`; navigation
anchors live in `scraper/booking_scraper.py`. Because the parser is a
pure function tested against a sanitized fixture, an interface change
shows up as **failing tests with precise diffs**, and the fix is: save a
new sanitized fixture, adjust the constants, update the evidence table
in `docs/m0_findings.md` §8.5 — no business logic touches HTML details.

### Q4. If run daily for a month, how could the CSVs show that a room got booked?

Compare rows for the same `(checkin_date, room_category)` across daily
runs: `rooms_available` dropping (e.g. 7 → 5 → 2) or a category
disappearing while its siblings persist is a **possible inventory
reduction signal — not proof of a booking**. Plausible alternative
causes: the hotel reduced OTA allocation, set a restriction, removed a
room type from sale, a promotion changed visibility, or Booking.com
changed presentation. Prices move independently of inventory (observed
promo flicker), so price changes must not be read as bookings. The
`scrape_timestamp` column makes each observation comparable and
auditable.

### Q5. How would the design cover 50 hotels?

Already in place: property records are configuration (`properties:`
list) and every scraper/parser call receives the `Property` — no
hotel-specific code exists anywhere. Scaling up would add: startup
verification per enabled property (exists, applied per property);
error isolation per property×date (exists per date — same pattern);
**one global rate limiter** shared by all navigation (exists — this is
the critical piece for compliance at scale); initially strictly
sequential processing (Phase 1 behaviour), then controlled concurrency
bounded by that shared limiter; one CSV per property per run (spec §8.5
strategy 1 — `property_id` in filenames/logs, columns unchanged); and
monitoring on the exit codes (0 ok / 3 blocked / 4 partial). Phase 1
deliberately does **not** implement multi-property execution (spec §3.2).

### Q6. What assumptions did you make, and what would you do with one more day?

**Assumptions (all documented, none silent):**
1. "30 dates" = tomorrow…tomorrow+29 (spec §5.4 ambiguity).
2. "3 retries" = 3 total attempts per date (spec §4 ambiguity).
3. Property substitution for the unfound target (disclosed above).
4. Displayed price = stay total, taxes excluded (verified via the price
   popover on the captured page; equals per-night for 1-night stays).
5. `is_capped=true` whenever the selector supplies the count (D10).
6. The sample CSV repeats one saved page for every date — it
   demonstrates the pipeline, not per-date reality (hence `_SAMPLE`).
7. `sold_out` is unimplemented **on purpose**: no not-bookable markup
   has ever been observed, and inventing selectors is forbidden.

**With one more day, in priority order:**
1. If the AWS WAF challenge clears: one compliant live smoke test
   (spec §14.3), then the full 30-date run — the only way to replace
   assumption 6 with real per-date data.
2. Observe real sold-out / scarcity-on-row / multi-night cases in a
   human browser, save sanitized fixtures, and add documented parser
   branches for them.
3. Add a second-date fixture (e.g. a peak-season date) to widen parser
   regression coverage beyond 2026-11-01.
4. Small: CI running `pytest` on push; `--dates N` CLI override.

## 8. Limitations (current state)

- **Automated live access is blocked.** On 2026-10-31 Booking.com
  served an AWS WAF bot-protection challenge (HTTP 202, empty title)
  to the headless browser. Per the rules the run **stopped** (exit 3),
  nothing was bypassed, and no automated retry-against-the-block has
  been attempted since. Evidence: `docs/m0_findings.md` §7,
  `logs/run_20261031_2224.log`. Human browser access works normally.
  Live mode is code-complete and offline-tested, but **not** verified
  against the live site since the block — this README will not claim
  otherwise.
- `sold_out` rows cannot be produced until real not-bookable markup is
  observed (honesty rule).
- Counts are capped selector maxima, not physical inventory (Q1/Q2).
- One currency (INR), one occupancy (2 adults/1 room), one-night stays —
  fixed by the assignment; multi-property/OTA support is out of scope.

## 9. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Exit 2 + "Configuration error" | missing/invalid YAML keys, delay < 3, attempts > 3, adults/rooms/stay/currency not 2/1/1/INR, zero or multiple enabled properties, missing `booking_url`/`booking_property_id` |
| Exit 3 + "BLOCKED…" | AWS WAF/CAPTCHA block detected. Expected while Booking.com blocks this environment. Do not bypass; use `--sample-from-fixture` and the fixtures for development (spec §4) |
| Exit 3 + "verification failed" | the page title/visible text no longer contains the configured property name — re-verify the listing manually (M0) and update the config |
| Exit 4 + "failed date(s)" | some dates errored after retries; CSV still written; `error_detail` columns and `logs/run_*.log` say why |
| `--sample-from-fixture`: "Sample fixture missing" | restore `tests/fixtures/live_2026-11-01_roomtable.html` from the repo |
| Browser test skips during `pytest` | Chromium missing — `playwright install chromium`; everything else still runs offline |

## 10. Repository map

```
main.py                    CLI: config → verify → collect → validate → CSV
config/config.yaml         settings + property records (no secrets)
scraper/browser.py         Playwright session, shared rate limiter, block detection
scraper/hotel_finder.py    startup listing verification
scraper/booking_scraper.py per-date URL + navigation + render wait
scraper/availability_parser.py  pure HTML → observations (fixture-tested)
services/…                 property, dates, inventory orchestration, validation, CSV
tests/                     171 offline tests + sanitized fixtures
docs/m0_findings.md        verification evidence, block event, fixture ground truth
docs/m7_compliance_review.md  spec §16/§17 checklist with evidence
docs/decisions.md          roadmap + approved decisions (D1–D13)
output/                    one CSV per run (+ curated _SAMPLE deliverable)
logs/                      one log per run (+ curated examples)
```
