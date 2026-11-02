# M0 Findings — Listing verification (manual inspection)

**Status:** **CLOSED** on 2026-11-01 (initially PARTIAL on 2026-10-31;
remaining open items resolved from developer-saved sanitized fixtures,
see §8.5)
**Outcome:** Assignment target property not found; documented substitution
approved by the developer.
**Compliance:** public data only · no login · no personal data · no CAPTCHA
encountered or bypassed · no credentials or session data recorded here.

---

## 1. Search for the assignment target (spec §3.1)

| Item | Result |
|------|--------|
| Target property | Hotel Shiv Vilas, Tapovan, Rishikesh |
| Found on Booking.com? | **NO** (manual search, 2026-10-31) |
| Developer decision | Proceed with the verified Tapovan listing found during M0; re-check Hotel Shiv Vilas later. |

### Recorded deviation

This is a deliberate deviation from:

- spec §3.1 (verify the correct Hotel Shiv Vilas listing),
- spec §5.2 (exactly one enabled property = Hotel Shiv Vilas),
- spec §17.3 (acceptance criterion: correct Shiv Vilas listing verified).

It is approved by the developer and recorded here rather than applied
silently (spec rules 2 and 8). It must also be disclosed in the README
(assumptions/limitations) at milestone M8.

---

## 2. Verified findings — the substitute listing

| # | Item | Value | Source |
|---|------|-------|--------|
| 1 | Property name | **Around Stays-Heaven And Harmony** | listing |
| 2 | Full listing URL | `https://www.booking.com/hotel/in/heaven-and-harmony-tapovan.en-gb.html` | address bar |
| 3 | Property identifier | `dest_id=17328907` with `dest_type=hotel` → stored as `booking_property_id: "17328907"` | URL query (confirm against live page before M5) |
| 4 | Address | 1, Badrinath Marg, Tapovan, Rishikesh, Uttarakhand, India, 249192 | listing |
| 5 | Occupancy in observed URL | check-in 2026-11-02 → check-out 2026-11-03 (1 night), 2 adults, 1 room, 0 children | URL query |
| 6 | Price display mode | `sb_price_type=total` / `type=total` → page set to **total-stay** price presentation | URL query |
| 7 | Room-block identifiers | `all_sr_blocks` / `sr_pri_blocks` / `matching_block_id` = `1732890704_444880524_2_0_0` | URL query — **not** HTML selectors and **not** confirmed `room_type_id` values |

Tracking parameters and any session identifiers were omitted from the
recorded URL.

## 3. Verification checklist (resolved 2026-11-01 — see §8 / §8.5)

Original pre-inspection checklist; final status per item:

- [x] Currency actually displayed → **INR / ₹** end-to-end (§8 row 2)
- [x] Room categories for the selected dates → **4 rendered rows** (§8.5)
- [x] Quantity selector present, highest selectable value → **2 / 7 / 2 / 3** (§8.5)
- [x] Scarcity message → on search-result cards ("We have N left at
      this price"); **none rendered on the room table** for 2026-11-01
- [ ] Sold-out / not-bookable wording → **still unobserved**; never
      fabricate (§8.2)
- [x] Price format → **stay total, taxes excluded** (§8 row 7, spec §7.4)
- [x] DOM anchors → `hprt-*` rooms table (§8.5)
- [x] JSON carrying room/price data → embedded in page scripts only;
      scripts stripped from fixtures; **DOM path confirmed**
- [x] CAPTCHA / bot-protection check → WAF block recorded (§7); no
      challenge in the human browser (§8 row 11)
- [x] Near-date vs far-date comparison → same categories; promo price
      flicker is price variance, not inventory change (§8 row 10)

Unchanged rule: extraction code may only be written against observed
evidence (the §8.5 fixtures) — never against guesses.

## 4. Room categories (UNVERIFIED — from indexed property information)

Must be re-confirmed on the live page for the actual search dates before
being treated as observed:

- Deluxe Double Room
- 6-Bed Mixed Dormitory Room
- Deluxe Quadruple Room
- Standard Double Room
- Two-Bedroom Apartment
- Three-Bedroom Apartment

**Superseded by the §8.5 fixture (2026-11-01):** only four of these are
rendered in the rooms table for that date (Standard Double, Deluxe
Double, Deluxe Quadruple, 6-Bed Mixed Dormitory); the two apartments
exist only in embedded page JSON (room ids …705 / …706) and are **not
offered** for the searched dates.

## 5. Configuration impact

- `config/config.yaml`: enabled property is now
  `heaven_and_harmony_tapovan` (with verified URL + observed `dest_id`).
- `shiv_vilas` record retained, **disabled**, for the later re-check.
- The exact-ID Phase 1 guard in `services/property_service.py` was
  relaxed to "exactly one enabled property" because of this deviation;
  the structural single-property rule still holds.

## 6. Open questions / flags

1. **CSV filename** — was `shivvilas_inventory_<timestamp>.csv` per
   spec §8.1 despite a different scraped property. **Resolved
   2026-11-02 (decision D14):** the filename now derives from the
   scraped property id (`<property_id>_inventory_<timestamp>.csv`, e.g.
   `heaven_and_harmony_tapovan_inventory_….csv`) — a recorded deviation
   from spec §8.1, so the file names the hotel actually scraped.
2. **Hotel Shiv Vilas re-check:** repeat Step 1 of M0 later (direct
   Booking.com search, "also known as" section, Google Maps address
   cross-check). If found, swap the enabled record — no code changes.
3. **Live inspection (section 3)** must happen before M5; until then the
   parser can only be built against synthetic fixtures.

## 7. Live access block event (2026-10-31, M4)

During the first automated startup verification, Booking.com served an
**AWS WAF bot-protection challenge** instead of the listing:

| Evidence | Value |
|----------|-------|
| HTTP status | `202` |
| Page title | empty (`''`) |
| Visible body text | empty (length 0) |
| HTML markers | `window.awsWafCookieDomainList = ['booking.com']`, script `https://www.booking.com/__challenge_…` |
| Outcome | `VerificationError` initially → reclassified as **`BlockedError`** after adding the observed markers to `scraper/browser.py` (`BLOCK_HTML_MARKERS`), exit code 3 |

**Response taken (spec §4, §10):** the affected activity was **stopped**;
no bypass was attempted (no challenge solving, no retry loop, no
headless/stealth evasion, no user-agent spoofing). The event is logged
by the run (`logs/run_*.log`) and must be explained in the README/run
report (M8).

**Implications:**

- Automated live verification / smoke test / full runs are currently
  **blocked** for this environment.
- Per spec §16, deliverables may fall back to *"a clearly labelled
  sample based on saved page evidence if live access is blocked"* —
  parser development continues against sanitized fixtures.
- Manual human access (your normal browser) may still work — this needs
  to be checked by the developer during M0 manual inspection, and is
  **not** the same as automation bypassing the challenge.
- Open question for the assignment owner: how to proceed when WAF
  challenges block automated access (see README at M8).

**Recurrences (2026-11-01, 2026-11-02 — block confirmed persistent):**

| # | Run (IST) | Log | Signature | Outcome |
|---|-----------|-----|-----------|---------|
| 1 | 2026-10-31 22:24 | `logs/run_20261031_2224.log` (curated in repo) | HTTP 202, empty title, `awsWafCookieDomainList` | stopped, exit 3 |
| 2 | 2026-11-01 22:31 | `logs/run_20261101_2231.log` | identical (HTTP 202, `awsWafCookieDomainList`) | stopped in 1.2 s, exit 3 |
| 3 | 2026-11-02 05:52 | `logs/run_20261102_0552.log` | identical | stopped in 1.3 s, exit 3 |

Three independent human-initiated runs across three days, same
challenge page each time → the block is **persistent for this
environment**, not a transient blip. No bypass or evasion was
attempted in any of them; per decision D8 no further automated
attempts are planned until conditions change (development continues
offline; manual human browser access still works for spot checks).

## 8. Manual inspection results (2026-11-01, developer's own browser)

Human access works (no challenge encountered). Recorded from the
developer's live session; items initially marked PENDING were later
resolved from two sanitized page fixtures the developer saved with a
normal browser (see §8.5).

| # | Item | Result | Status |
|---|------|--------|--------|
| 0 | Listing loads in a normal human browser | yes — name, address, images, availability, room selection, calculated price all visible | ✅ VERIFIED |
| 1 | Prices show ₹ | yes | ✅ VERIFIED |
| 2 | Currency URL parameter | re-check shows `selected_currency=INR` (an earlier capture showed `EUR` — stale, superseded) | ✅ RESOLVED — INR end-to-end, no conversion needed |
| 3 | Room categories for 2026-11-01 | **4 rendered rows**: Standard Double Room, Deluxe Double Room, Deluxe Quadruple Room, 6-Bed Mixed Dormitory Room. Two-/Three-Bedroom Apartments exist only in embedded page JSON (room ids …705/…706), **not rendered** → not offered for this date | ✅ VERIFIED (fixture §8.5) |
| 4 | Quantity selector | present per room row (`<select class="hprt-nos-select …">`, options `0..max`); observed maxes on 2026-11-01: **2, 7, 2, 3** (varies per room; the earlier "1..6" note was a separate manual observation) | ✅ VERIFIED — record `qty_selectable_max` from the max option; `is_capped` semantics finalized in M5 |
| 5 | Scarcity text | "We have N left at this price" (N = 1..6) observed on **search-result cards**. Room table on 2026-11-01: **no scarcity message rendered** (remaining matches are JS string templates, stripped from fixtures) | ✅ VERIFIED — scarcity wording exists site-wide but was not rendered on the room table in the fixture |
| 6 | Sold-out wording | **not observed** (rooms were available for the tested dates) | ❌ NOT OBSERVED — see §8.2 |
| 7 | Price display | price-popover math observed: `₹ 2,000 × 1 night` → `− ₹ 960 Late Escape Deal` → **`Total ₹ 1,040`** → `+₹ 52 taxes and charges` → displayed price = **stay total, taxes excluded** | ✅ VERIFIED — spec §7.4 resolved; for the 1-night CSV rows, total = per-night |
| 8 | DOM hints | saved page renders Booking's **`hprt-*` rooms table** (`hprt-table-row`, `hprt-roomtype-name`, `hprt-roomtype-link`, `hprt-nos-select`, `bui-price-display__value`, `[data-excl-charges-raw]`, popover `price-area-block-id-*`). **No `rp-room-title`/`rp-content` in this save** — an earlier live view showed `rp-*` testids (different render state); note kept, parser anchors = what the fixture contains | ✅ VERIFIED (fixture §8.5) |
| 9 | JSON/XHR room data | no separate XHR captured; the page **embeds** room inventory JSON inside scripts (`b_roomtype_id`, `translations`, unrendered apartment types …705/…706) — scripts are stripped from fixtures; parsing target = DOM | ✅ RESOLVED — DOM parsing path confirmed |
| 10 | Near vs far date comparison | room categories the same for both; the "Late Escape Deal" discount price appears and disappears between checks | ✅ VERIFIED — that is price/promo variance, not inventory change |
| 11 | Challenge page in human browser | none | ✅ VERIFIED |

### 8.2 Honesty note — discarded wording (spec rules 4/8)

The sold-out strings quoted during inspection (*"Sold out", "No longer
available on our site", "You missed it!", "Fully booked", "Limited
availability"*) were generated by an AI assistant, **not observed on the
page**. They are **discarded** and must never be used as parser evidence
or as justification for a `sold_out` status. The sold-out state for this
property remains genuinely unobserved; it will only ever be recorded
from real observed evidence.

### 8.3 Redaction

The search-results URL captured during inspection contained a session
identifier (`sid=…`) and ad-tracking parameters; it is **redacted** here
and must never be committed (spec §4: no session data).

### 8.4 Open items — ALL RESOLVED (2026-11-01)

1. ✅ Room-table fixture saved and sanitized → §8.5.
2. ✅ Room-category list for live dates extracted → §8.5
   (4 rendered; 2 apartments not offered for the date).
3. ✅ Price semantics resolved → stay total, taxes excluded (§8 row 7).
4. ✅ Room-table scarcity wording → none rendered for 2026-11-01
   (§8 row 5).

**M0 is closed.** Milestone M5 (parser) may be built strictly against
the §8.5 fixtures.

### 8.5 Fixtures (sanitized, 2026-11-01) — ground truth for M5

Two pages saved by the developer in a normal browser (Save As → HTML
Only), then sanitized: all `<script>` blocks removed, session ids
(`sid=`), JWT/ad tokens (including one embedding the client's IP
address) and placeholder emails redacted. Post-sanitization sensitive
scan: **empty**; scripts: **0**.

> **Correction (2026-11-01, pre-public-push scan):** the original scan
> pattern (`sid=`) missed **URL-encoded** occurrences (`sid%3D…` inside
> ad-network redirect URLs, e.g. Kayak). A follow-up scan for the raw
> redacted session-id values themselves found 6 residues (2 raw + 4
> URL-encoded) of two session ids across the two fixtures. All were
> redacted to `[REDACTED_SESSION_ID]`; re-scan for the raw values, the
> client IP and JWT prefixes is **empty**; all 171 tests still pass.
> Sanitization lesson recorded: scan for the sensitive **values**, not
> for a URL-pattern form of them.
>
> **Correction 2 (2026-11-02, GitHub secret-scanning alert):** GitHub
> flagged a **Google API key** (`AIza…`, 39 chars) embedded in
> Booking.com's own page HTML — their public, referrer-restricted web
> key that loads Maps/analytics for every visitor. It rode inside both
> fixtures in **plain HTML**, not a `<script>` block, so the original
> sanitization (which stripped scripts, session ids, JWTs and emails)
> never touched it. It is not this project's credential and not a
> confidential secret, but it was redacted to
> `[REDACTED_GOOGLE_API_KEY]` and **all of git history was rewritten**
> (`git filter-branch`) so no commit contains it; that same rewrite also
> purged the residual session ids from the two earliest commits. Re-scan
> across every commit: clean; 171 tests still pass. Lesson recorded:
> third-party pages embed **their own** credentials — scan fixtures for
> provider-key patterns (`AIza…`, `AKIA…`, `sk-…`, `ghp_…`), not just
> session/JWT material.

| Fixture | Size | Content |
|---------|------|---------|
| `tests/fixtures/live_2026-11-01_roomtable.html` | 492 KB | property page — `hprt-*` rooms table, search 2026-11-01 → 2026-11-02, 2 adults, 1 room, INR |
| `tests/fixtures/search_results_2026-11-01.html` | 653 KB | search-results page — 25 property cards; card-level scarcity wording |

**Room table for 2026-11-01 (1 night, 2 adults, 1 room) — parsed by
hand from the fixture; this is the expected parser output:**

| data-room-id | room_category | beds | qty max (dropdown) | price (stay total, INR) | taxes | original / deal |
|---|---|---|---|---|---|---|
| 1732890704 | Standard Double Room | 1 double bed | 2 | ₹1,040 | +₹52 | ₹2,000 (−48%, Late Escape Deal) |
| 1732890701 | Deluxe Double Room | 1 double bed | 7 | ₹1,144 | +₹57 | ₹2,200 (−48%) |
| 1732890703 | Deluxe Quadruple Room | 4 bunk beds | 2 | ₹2,080 | +₹104 | ₹4,000 (−48%) |
| 1732890702 | 6-Bed Mixed Dormitory Room | 6 bunk beds | 3 | ₹2,184 | +₹109 | ₹4,200 (−48%) |

Observed DOM anchors (all in the fixture, none invented):

- row container: `div.hprt-table-row`
- name: `a.hprt-roomtype-link[data-room-id]` → `span.hprt-roomtype-icon-link`
- occupancy text: `span.hprt-roomtype-occupancy-text` ("2 adults")
- beds: `hprt-roomtype-bed` → `ul.rt-bed-types > li.rt-bed-type`
- quantity: `select.hprt-nos-select[name^="nr_rooms_"]`, options `0..max`
- price: `span.bui-price-display__value` ("₹&nbsp;1,040" — `&nbsp;` inside)
- strikethrough original: `data-strikethrough-value="2000"`
- rounded raw price: `data-hotel-rounded-price="1040"`
- taxes line: `[data-excl-charges-raw="52.0…"]` → "+₹ 52 taxes and charges"
- price breakdown popover: `id="price-area-block-id-…"`
  (`₹ 2,000 × 1 night … Total ₹ 1,040`)

**Availability-state evidence:** all 4 rooms available on 2026-11-01;
no scarcity message and no sold-out rendering on the room table.
`sold_out` remains unobserved and must never be emitted without real
evidence (§8.2).
