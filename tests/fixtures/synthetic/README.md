# Synthetic fixture scenarios

**These files are test data. None of them is a live Booking.com observation.**
They are deliberately small, hand-written HTML pages that exercise the
parser, the central access-state classifier and the per-state retry policy
without any network access. No challenge is solved and no bypass technique
is represented — a challenge fixture exists so the scraper can prove it
*stops*.

Run one with:

```bash
.venv/bin/python main.py --config config/config.yaml --mode fixture --scenario available
```

`--scenario evidence` (the default, from Stage 1) is a separate, sanitized
real page saved during manual M0 verification; it is **not** in this
directory.

| Scenario | File | What it proves |
|----------|------|----------------|
| `available` | `available.html` | Two ordinary rows -> `ok` with observed selector maxima. |
| `sold_out` | `sold_out.html` | Selector maximum 0 -> `sold_out` with `rooms_available=0` (D15); the second row remains `ok`. |
| `no_rooms` | `no_rooms.html` | Property chrome, no rows -> one date-level `no_rooms_listed` row. |
| `waf_challenge` | `waf_challenge.html` | HTTP 202 + observed WAF markers + empty title -> `AWS_WAF_CHALLENGE`, stop. |
| `captcha` | `captcha.html` | Text + markup + title -> `CAPTCHA_PRESENT`, stop. |
| `bot_protection` | `bot_protection.html` | Bot wording + interstitial shape -> `BOT_PROTECTION`, stop. |
| `http_denied` | `http_denied.html` | Replayed HTTP 403 -> `HTTP_ACCESS_DENIED`, stop with no retry. |
| `property_mismatch` | `property_mismatch.html` | Different property name -> identity check refuses to scrape (exit 3). |
| `structure_changed` | `structure_changed.html` | Unreadable room name -> `PAGE_STRUCTURE_CHANGED`, error row, no retry. |

`manifest.json` carries each scenario's replayed HTTP status and expected
property name. The synthetic pages reuse only selectors and wording already
observed in the real saved fixture; the *scenarios* are invented, but no
fabricated Booking.com behaviour is claimed as evidence.
