# M7 — Deliverables & compliance review (2026-11-01)

**Scope:** verify the repository against spec §16 (deliverables) and
§17 (acceptance criteria) before the final README milestone (M8).
All statuses below cite evidence in this repository; nothing is marked
complete on assertion alone.

**Verification commands:**

``` bash
.venv/bin/python -m pytest -q                       # 171 passed
.venv/bin/python main.py --config config/config.yaml --sample-from-fixture
                                                     # EXIT=0, 120 rows
```

---

## 1. Deliverables (spec §16)

| # | Deliverable | Status | Evidence |
|---|-------------|--------|----------|
| 1 | Python source | ✅ | Tree matches spec §12: `main.py`, `scraper/{browser,hotel_finder,booking_scraper,availability_parser}.py`, `services/{property,date,inventory,validation,csv}_service.py` |
| 2 | Configuration | ✅ | `config/config.yaml` — no secrets; deviation notes inline |
| 3 | `requirements.txt` | ✅ | bounded: pyyaml, playwright, beautifulsoup4 (+ pytest) |
| 4 | `README.md` | ⏳ **M8** | pending |
| 5 | `spec.md` | ✅ | present |
| 6 | Unit tests + sanitized fixtures | ✅ | 171 tests (§3 below); `tests/fixtures/*.html` sanitized — sensitive scan empty (m0_findings §8.5) |
| 7 | Sample CSV (real run or labelled saved-evidence sample) | ✅ | `output/shivvilas_inventory_20261101_1631_SAMPLE.csv` — 120 rows, `_SAMPLE` label per §16 vs §8.1 resolution (csv_service docstring) |
| 8 | Run log | ✅ | `logs/run_20261101_1631.log` (full evidence trail); block-event logs `run_20261031_*.log` |

## 2. Acceptance criteria (spec §17)

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Fresh environment installs via README | ⏳ | README is M8; `requirements.txt` is complete + bounded |
| 2 | Runs with `python main.py --config config/config.yaml` | ✅* | Sample mode verified end-to-end (exit 0, CSV + log written); live mode coded, see §4 |
| 3 | Correct listing verified | ⚠️ | Substitute property documented + approved (m0_findings §1–§2); README disclosure pending |
| 4 | Configured number of dates, dynamic | ✅ | 30 dates from config; `test_dates.py` (11 tests) |
| 5 | Consistent search parameters | ✅ | observed query params built per date; `test_booking_scraper.py::test_url_carries_observed_search_parameters` |
| 6 | Every date attempted individually | ✅ | per-date loop; blocked/unattempted dates still get coverage rows (spec §8.7); `test_inventory.py` |
| 7 | All displayed room categories captured | ✅ | fixture ground truth = 4 rows, parser reproduces exactly; `test_parser.py::test_fixture_parses_exactly_the_hand_parsed_rows` |
| 8 | Evidence-based counts, honest limitations | ✅ | `room_selector` + `is_capped=true`; unknowns stay empty; caveat in `error_detail`; sold_out never fabricated (m0_findings §8.2) |
| 9 | sold_out / no_rooms_listed / error distinguished | ✅ | locked vocabularies + contract tests (`test_validation.py`, `test_inventory.py`); sold_out parser branch intentionally absent until observed evidence exists |
| 10 | No date failure crashes the run | ✅ | `test_inventory.py::test_exhausted_retries_write_error_row_and_next_date_still_runs` |
| 11 | Retries + page-load delays comply | ✅ | ≤3 attempts enforced in config validation; shared RateLimiter gate-before/mark-after (`test_browser.py`, 26 tests); retries flow through the same limiter |
| 12 | CSV columns and order exact | ✅ | spec §8.2 order hardcoded in a test; header equality asserted (`test_csv.py`) |
| 13 | Validation and tests pass | ✅ | **171 passed** |
| 14 | README answers all six questions | ⏳ | **M8** |
| 15 | No login / personal data / protection bypass | ✅ | sanitized fixtures (scan empty, scripts stripped, sid/JWT/IP redacted); WAF block detected → stopped → documented (m0_findings §7); no bypass attempted |
| 16 | Config-driven property; no hardcoded extraction | ✅ | parser/scraper contain zero hotel-specific logic; property comes from config (`Property` record) |
| 17 | Multi-property-ready design; Phase 1 still single-property | ✅ | property records are a validated list; scraper/parser take a `Property` argument; exactly-one-enabled enforced; one shared limiter (spec §5.3) |

\* live mode cannot be re-verified while the AWS WAF challenge blocks
automated access (m0_findings §7); its code paths are covered offline.

## 3. Test suite → spec §14.1 minimums

| Spec §14.1 minimum | Covered by |
|---|---|
| Config rejects duplicate IDs / invalid enabled records | `test_config.py` (28) |
| Phase 1 selects only the enabled record | `test_config.py` |
| Disabled future property doesn't change execution | `test_config.py` |
| Date count = configured; first = tomorrow; checkout = +1 | `test_dates.py` (11) |
| Category names without wording loss | `test_parser.py::test_names_preserve_exact_displayed_wording` |
| Selector counts parsed from fixtures | `test_parser.py` (real fixture + snippets) |
| Scarcity-message counts parsed | `test_parser.py::test_scarcity_wording_inside_a_row_is_used_as_evidence` |
| Sold-out classified correctly | `test_validation.py` sold_out contract tests; parser branch absent **by design** — no observed markup exists (m0_findings §8.2) |
| No-room dates represented | `test_inventory.py::test_loaded_page_without_rooms_becomes_date_level_record` |
| Unknown counts not fabricated | `test_parser.py::test_row_without_any_numeric_signal_is_flagged_not_invented` |
| Negative counts rejected; duplicates detected | `test_validation.py` (38) |
| CSV header/order exact | `test_csv.py` (18) |
| One date's error doesn't block later dates | `test_inventory.py` |
| Property identity retained in observations/logs | inventory `observed |` log lines + run log |
| Shared rate limiter enforces minimum delay | `test_browser.py` |

Per-file counts: parser 22 · browser 26 · config 28 · csv 18 · dates 11 ·
hotel_finder 13 · inventory 11 · validation 38 · booking_scraper 4 = **171**.

## 4. Known gaps carried into M8

1. **README.md** — six assignment answers + disclosures: property
   substitution (§3 above), WAF block event, `_SAMPLE` labelling,
   `availability_source` vocabulary, `is_capped` semantics, exit codes,
   fixture sanitization procedure.
2. **Live run unverified** since the 2026-10-31 WAF block; live mode is
   code-complete and offline-tested only. Never claim otherwise (§19).
3. **Git repository not initialized** — developer decision pending.
4. **Roadmap/decision log not in the repo** — recommend a short
   `docs/decisions.md` (or README section) at M8 recording the approved
   milestones and user decisions (30 dates, 3 attempts, bonus columns,
   fixture substitution, is_capped semantics).
