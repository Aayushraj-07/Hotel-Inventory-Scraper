# Submission Report — Hotel Inventory Scraper

## 1. Executive summary

Phase 1 delivers a CLI that records Booking.com room inventory for one
configured property (2 adults, 1 room, 1 night, INR) across 30 check-in
dates, with one CSV and one log per run. This upgrade adds the hiring
team's three acquisition modes, a central access-state classifier with
per-attempt diagnostics, a per-state retry/fallback table, three provenance
bonus columns, nine synthetic fixtures, a snapshot mode (one file or a
per-date directory batch merged into one CSV), an expanded offline test
suite, and this documentation.

**Status:** all offline work is complete and verified — **303 tests pass**.
The single human-initiated, headless-only live attempt was performed on
2026-11-23 and stopped at listing verification on Booking.com's AWS WAF
challenge (HTTP 202, exit 3). No bypass was attempted, and no live
collection is claimed (see §8).

## 2. Resubmission note — approach, limitations, blocked behavior, why no bypass

This is the short note requested with the resubmission; the evidence for
each claim is in the sections cited.

### Approach

Ordinary Playwright against public pages only, headless, with a shared
≥3 s rate limiter. Every page state flows through one centralized
classifier (`scraper/access_state.py`: 12 states, multi-indicator, HTTP
status authoritative) — the same classifier, parser, validator and CSV
writer run on all three acquisition modes (`live`, offline `fixture`, and
`snapshot` — a single saved file or a per-date directory batch merged into
one CSV). Retries are per state: network/timeout/missing-room-data
failures retry up to 3 attempts per date; structural/extraction failures
do not; blocking states stop immediately. Every row carries
`data_source=live|fixture|snapshot`, so provenance is explicit in the
output itself.

### Limitations

Live retrieval in this environment is blocked by Booking.com's AWS WAF
challenge (HTTP 202): attempts on 2026-10-31, 11-01, 11-02 and the final
attempt on 11-23 each stopped at listing verification in ~1.2 s, exit 3
(evidence in §8). Since no approved API, test environment, or authorized
IP allowlisting was available, **no live inventory data was obtained**.
Real per-date data is still obtainable via batch snapshot mode — a human
saves each date's page from a working browser, and the scraper merges
them into one CSV with no network access of its own. Offline rows are
labelled as such (`data_source`, `_FIXTURE`/`_SNAPSHOT` suffixes) and are
never presented as live observations.

### Blocked behavior (logging, retries, fallbacks)

- **Logging:** every attempt records the access state, indicators, a
  redacted URL, HTTP status/title and the attempt number; the final run's
  verbatim lines are in §8.
- **Retries:** blocking states (`CAPTCHA_PRESENT`, `BOT_PROTECTION`,
  `HTTP_ACCESS_DENIED`, `AWS_WAF_CHALLENGE`) are **never** retried;
  retryable states get at most 3 attempts per date (details in §5).
- **Fallbacks:** a block during listing verification stops the run with
  exit 3 and writes **no CSV**; a block during per-date collection stops
  all further page loads and writes `error` rows for the remaining dates,
  so no date is silently omitted. There is no fallback to a bypass, a
  proxy, or another data source — the block is reported instead.

### Why no bypass

The WAF challenge is a security control Booking.com deliberately
deployed, so circumventing it would mean defeating an access control — a
line this project will not cross in code regardless of who requests it;
ordinary requests to public pages are where it stops. Practically, a
bypass would not hold either: AWS WAF's challenge is server-side with
rotating signatures, so any bypass would be brittle, need constant
upkeep, and escalate to IP bans — the result would be stale or
fabricated data, or a broken tool, never reliable inventory. Finally,
the honest contract is the point of the assignment: blocks are to be
reported, not evaded, and every row declares its provenance via
`data_source`. The legitimate routes instead are Booking.com's official
partner API, an authorized environment (non-headless / allowlisted IP),
or batch snapshot mode — where a human saves each date's page and the
scraper merges them offline, which is what was built and verified.

## 3. Deliverables and repository state

| Deliverable | Location / evidence |
|-------------|---------------------|
| Source code | `main.py`, `scraper/`, `services/` |
| Configuration | `config/config.yaml` (no secrets) |
| Dependencies | `requirements.txt` |
| Written answers | `README.md` §11 (six assignment questions) |
| CSV contract | 8 required + 3 bonus columns, in `services/csv_service.py` |
| Fixtures | `tests/fixtures/` (2 sanitized real pages + 9 synthetic scenarios) |
| Tests | `tests/` — 303 passing offline tests |
| Run logs | `logs/` (curated evidence of the WAF blocks) |
| Decisions | `docs/decisions.md` D1–D17, evidence in `docs/m0_findings.md` |
| This report | `SUBMISSION_REPORT.md` |

`spec.md` stays local only (gitignored) and is not part of the published
repository. No credentials or personal data are present in tracked files.

## 4. Acquisition modes

| Mode | Command | Dates | Label |
|------|---------|-------|-------|
| live | `python main.py --config config/config.yaml --mode live` | 30 | `data_source=live`, plain filename |
| fixture | `python main.py --config config/config.yaml --mode fixture --scenario available` | 30 | `data_source=fixture`, `_FIXTURE` suffix |
| snapshot (file) | `python main.py --config config/config.yaml --mode snapshot --input <file.html> [--checkin YYYY-MM-DD]` | **1** | `data_source=snapshot`, `_SNAPSHOT` suffix |
| snapshot (batch) | `python main.py --config config/config.yaml --mode snapshot --input <directory/>` | **1 per file** | `data_source=snapshot`, `_SNAPSHOT` suffix |

The legacy `--sample-from-fixture` alias still produces the `_SAMPLE` CSV
from saved evidence. Fixture and snapshot paths reuse the live classifier,
parser, validator and writer — a challenge page stops them too. The CLI
has no `--headed` option: runs are headless only.

**Batch snapshots (real per-date data, no scraper-side network access):**
save each check-in date's page from a working human browser into one
directory (`booking_2026-12-01.html`, …), then run `--input <directory>`.
Each file's date is detected from the page or its name, every file is
classified and identity-checked, duplicate or undetectable dates are
rejected with exit 2, and the pages are merged into **one** CSV — the
per-date search URL picks the matching file, so a row can never be served
another date's page. Verified example: 3 saved pages → 3 dates → 6 rows,
exit 0.

Nine synthetic scenarios (`available`, `sold_out`, `no_rooms`,
`waf_challenge`, `captcha`, `bot_protection`, `http_denied`,
`property_mismatch`, `structure_changed`) are documented in
`tests/fixtures/synthetic/README.md`.

## 5. Access states, retries and diagnostics

`scraper/access_state.py` is the single source of truth for 12 states,
multi-indicator classification, URL/text redaction and the per-attempt
`AccessDiagnostic`. HTTP statuses are authoritative; content-based
CAPTCHA/bot findings require two independent sources so one stray text
match cannot stop a healthy run.

- **Retryable:** `NETWORK_ERROR`, `NAVIGATION_TIMEOUT`,
  `ROOM_DATA_NOT_LOADED`, `UNKNOWN_ERROR` — up to 3 total attempts/date.
- **No retry:** `PAGE_STRUCTURE_CHANGED`, `EXTRACTION_ERROR`,
  `PROPERTY_NOT_FOUND`, `PROPERTY_MISMATCH` — error row / verification exit.
- **Stop:** `HTTP_ACCESS_DENIED`, `BOT_PROTECTION`, `CAPTCHA_PRESENT`,
  `AWS_WAF_CHALLENGE` — no further page loads, no bypass, exit 3.

Every attempt is logged with its state, attempt number, redacted URL,
status/title when available, indicators, room-information presence and
extraction result. Blocked runs still emit `error` rows for all remaining
dates so the CSV never silently omits a date.

## 6. Output and provenance

`output/<property_id>_inventory_<YYYYMMDD_HHMM>[_MODE].csv` contains exactly
eight required columns followed by `error_detail`, `is_capped`, and
`data_source` (D16). `data_source` is never inferred: it comes from the
selected mode, so an offline row can never claim to be live.

`sold_out` is now emitted for an observed selector maximum of 0 (D15,
synthetic-validated); it supersedes D13. Selector-derived positive counts
remain capped (`is_capped=true`), and unknown values stay empty.

## 7. Testing and verification results

```
$ .venv/bin/python -m pytest -q
303 passed in 1.71s
```

Offline CLI runs verified during development:

| Command (offline) | Observed result |
|-------------------|-----------------|
| `--mode fixture --scenario available` | 30 dates, 60 rows, exit 0, `data_source=fixture` |
| `--mode fixture --scenario sold_out` | 30 dates, 60 rows, `sold_out` + `ok`, exit 0 |
| `--mode fixture --scenario waf_challenge` | stopped with `AWS_WAF_CHALLENGE`, no extraction |
| `--mode fixture --scenario property_mismatch` | exit 3, `PROPERTY_MISMATCH`, no CSV |
| `--mode fixture --scenario structure_changed` | 30 `error` rows, exit 4, no retry |
| `--mode snapshot --input … --checkin 2026-12-01` | 1 date, 2 rows, exit 0, `data_source=snapshot` |
| `--mode snapshot --input <dir/ of 3 saved pages>` | 3 dates merged into one CSV, 6 rows, exit 0, `data_source=snapshot` |
| `--mode live` (one human-initiated headless run, 2026-11-23) | stopped at listing verification with `AWS_WAF_CHALLENGE`, exit 3, no CSV — see §8 |

## 8. Live access attempt and block evidence

Observed WAF evidence (real, not invented): HTTP **202**, empty `<title>`,
no visible body text, and HTML markers `awsWafCookieDomainList`,
`/__challenge_`, `reportChallengeError`. Prior headless runs on
**2026-10-31, 2026-11-01 and 2026-11-02** each stopped at listing
verification in about 1.3 s with exit 3; per the plan, no further
automated live attempts were made during development. Curated logs:
`logs/run_20261031_2224.log`, `logs/run_20261101_2231.log`,
`logs/run_20261102_0552.log` and the final attempt's
`logs/run_20261123_1402.log`; analysis in `docs/m0_findings.md` §7.

**Final validation attempt (human-initiated, headless-only):** one run of

```bash
python main.py --config config/config.yaml --mode live
```

was executed by the developer on **2026-11-23 at 14:02:53 IST**. It planned
the standard 30 dates (2026-11-24 … 2026-12-23), verified search parameters
(2 adults / 1 room / 1 night / INR), and stopped at listing verification at
14:02:55 (~1.2 s) with `AWS_WAF_CHALLENGE`. Exit code: **3**
(`EXIT_VERIFICATION_FAILED`, the code path taken by a verification-time
block). No room page was fetched, no retry was attempted, no CSV was
written, and no bypass technique was used.

Verbatim key lines from that run:

```text
2026-11-23 14:02:53,853 INFO     hotel_scraper: Run started | config=config/config.yaml | property_id=heaven_and_harmony_tapovan (Around Stays-Heaven And Harmony, Tapovan, Rishikesh, India)
2026-11-23 14:02:53,853 INFO     hotel_scraper: Planned check-in dates: 30 (2026-11-24 .. 2026-12-23); last checkout: 2026-12-24
2026-11-23 14:02:53,853 INFO     hotel_scraper: Verifying public listing: https://www.booking.com/hotel/in/heaven-and-harmony-tapovan.en-gb.html
2026-11-23 14:02:55,033 WARNING  hotel_scraper.access_state: Access state AWS_WAF_CHALLENGE | url=https://www.booking.com/hotel/in/heaven-and-harmony-tapovan.en-gb.html | indicators=[HTTP 202, awsWafCookieDomainList, /__challenge_, reportChallengeError]
2026-11-23 14:02:55,051 ERROR    hotel_scraper: BLOCKED during listing verification (spec §4) [AWS_WAF_CHALLENGE]: AWS WAF challenge page from https://www.booking.com/hotel/in/heaven-and-harmony-tapovan.en-gb.html (HTTP 202, indicators: HTTP 202, awsWafCookieDomainList, /__challenge_, reportChallengeError) — stopping per assignment rules (no bypass attempts).
2026-11-23 14:02:55,051 ERROR    hotel_scraper: Stopped without any bypass attempt. This event must be explained in the README/run report.
```

**Conclusion:** live collection remains unconfirmed in this environment. The
block is reported honestly; all sample/live-labelled output comes from
offline fixture and snapshot modes, which state their provenance in every
row.

## 9. Compliance statement

- Only ordinary Playwright requests to public pages; no login or personal data.
- No CAPTCHA solving, stealth plugins, fingerprint spoofing, proxy rotation,
  cookie/session manipulation, or any other bypass technique.
- Blocks are detected, logged, retried **never**, and reported honestly.
- Shared ≥3 s rate limiter covers all page loads, including retries.
- At most 3 attempts per date; failures become `error` rows, never
  `sold_out` or `no_rooms_listed`.
- Offline data is labelled by filename suffix and `data_source`; nothing
  synthetic is presented as live observation.

## 10. Assumptions, deviations and reproduction

- 30 dates = tomorrow…tomorrow+29 (D1); 3 attempts = 3 total per date (D2).
- Property substitution to the verified Tapovan listing (D6); CSV stem
  follows the scraped property id, not the spec's fixed `shivvilas_*` (D14).
- D15 supersedes D13 (`sold_out` from observed selector zero); D16 supersedes
  D3 (third bonus column).
- A single snapshot file covers exactly one check-in date; batch mode
  covers one date **per saved file**. Neither ever invents dates — the
  CSV's date set comes only from the pages that actually exist.

Reproduce everything offline:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
.venv/bin/python -m pytest -q
python main.py --config config/config.yaml --mode fixture --scenario available
python main.py --config config/config.yaml --mode snapshot \
  --input tests/fixtures/synthetic/available.html --checkin 2026-12-01

# batch snapshot demo (three saved pages, one per date):
mkdir -p /tmp/snapshots
for d in 2026-12-01 2026-12-02 2026-12-03; do
  cp tests/fixtures/synthetic/available.html /tmp/snapshots/booking_$d.html
done
python main.py --config config/config.yaml --mode snapshot --input /tmp/snapshots
```

All run artefacts are written only to `output/` and `logs/`.
