# Development decisions & roadmap log

This file records the approved plan and the developer's decisions so the
repository is self-contained (previously these lived only in the
assistant chat). Detailed evidence lives in `docs/m0_findings.md` and
`docs/m7_compliance_review.md`.

## Roadmap (approved 2026-10-31)

| Milestone | Scope | Status |
|-----------|-------|--------|
| M0 | Manual listing verification, live-page inspection | ✅ closed 2026-11-01 |
| M1 | Skeleton, CLI, config validation, date generation | ✅ |
| M2 | CSV contract + row validation services | ✅ |
| M3 | Playwright session, shared rate limiter, typed failures | ✅ |
| M4 | Automated startup listing verification (exit 3) | ✅ |
| M5 | Availability parser + per-date fetch, built on real fixtures | ✅ |
| M6 | Per-date orchestration (retries, isolation, block-stop), CSV output | ✅ |
| M7 | Deliverables & compliance review (spec §16/§17) | ✅ |
| M8 | README with assignment answers + disclosures | ✅ |

## Key decisions

| # | Decision | Date | Notes |
|---|----------|------|-------|
| D1 | **30 check-in dates** = tomorrow … tomorrow+29 | 2026-10-31 | documented interpretation of spec §5.4 |
| D2 | **3 total attempts per date** (not 3 retries) | 2026-10-31 | documented interpretation of spec §4 |
| D3 | **Bonus columns = `error_detail` + `is_capped` only** | 2026-10-31 | other spec §9 bonuses deferred |
| D4 | **YAML** configuration | 2026-10-31 | `config/config.yaml`, no secrets |
| D5 | **Automated startup listing verification** | 2026-10-31 | hard gate before any scraping (exit 3) |
| D6 | **Property substitution**: `heaven_and_harmony_tapovan` enabled, `shiv_vilas` kept disabled | 2026-10-31 | Hotel Shiv Vilas not found on Booking.com; approved deviation (m0_findings §1). CSV filename stays spec-fixed `shivvilas_inventory_*` |
| D7 | **Manual inspection by the developer** in their own browser | 2026-11-01 | human access ≠ bypass; fixtures saved + sanitized by hand |
| D8 | **No re-runs against Booking.com after the AWS WAF block** | 2026-10-31 | retrying-to-bypass would violate spec §4; fixtures drive development |
| D9 | **Rate-limiter semantics: gate-before + mark-after** | 2026-10-31 | strictest reading of "≥3 s between page loads" after a live test exposed a gap |
| D10 | **`is_capped = true` whenever the quantity selector supplies the count** | 2026-11-01 | the numeric maximum lives in `qty_selectable_max` (logged); the boolean flags that every selector count may hide larger inventory (spec §7.2) |
| D11 | **Parser anchors from the saved fixture (`hprt-*` rooms table)** | 2026-11-01 | an earlier live view showed `rp-*` testids — a different render state; recorded, not used (m0_findings §8 row 8) |
| D12 | **`_SAMPLE` filename suffix** for saved-evidence runs | 2026-11-01 | resolves the tension between spec §8.1 (fixed pattern) and §16 (clearly label samples) |
| D13 | **`sold_out` emission disabled until real markup is observed** | 2026-11-01 | AI-generated wording was discarded; honesty rule (m0_findings §8.2) |

## Observed facts the implementation depends on

- Room table = Booking.com "new rooms table": `tr.e2e-hprt-table-row`,
  `a.hprt-roomtype-link[data-room-id]`, `select.hprt-nos-select`,
  `div.bui-price-display__value`, `div[data-excl-charges-raw]`
  (m0_findings §8.5, sanitized fixture).
- Displayed price = **stay total, taxes excluded** (popover evidence:
  "₹2,000 × 1 night − deal = Total ₹1,040, +₹52 taxes and charges").
- Scarcity wording "We have N left at this price" observed on
  search-result **cards** only; never rendered on the room table.
- Currency INR end-to-end (`selected_currency=INR`, ₹ visible).
- The two apartment room types exist only in embedded page JSON for
  the captured date — not rendered, therefore not scraped.
