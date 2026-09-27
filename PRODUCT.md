# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Nigerian football bettors who already bet on SportyBet, MSport and Bet9ja. Two groups, both confirmed (28 Sep 2026), and the product must serve them with a **clear split**:

- **Everyday punters** — bet regularly, not stats people. Their job: "what should I back today, and can I trust it?" — answered fast.
- **Analytical bettors** — comfortable with probabilities, odds and value. Their job: check the model's reasoning, calibration and the market comparison before trusting a pick.

Simple by default; depth available on demand, never forced on the first group.

**Device (inferred, not confirmed):** the app is an installable PWA with a mobile bottom nav and Telegram delivery, which points to phones first, often on mobile data. Treat as the working assumption until Boss confirms.

## Product Purpose

An AI football-prediction service whose value rests entirely on an **honest, published, graded track record**. Every pick is graded against the real full-time result; the Record page shows hit rates, grading coverage, calibration and the weeks that went badly. Success = a first-time visitor leaves thinking **"I can trust this."**

## Positioning

Most tipster products sell confidence. PotentialPro sells **verifiable calibration**: when it says ~75%, those picks have won ~76% (452 graded served picks, 27 Sep 2026), it publishes every loss, states what it could not grade, compares itself with the bookmaker's price on every pick, and says plainly that its all-predictions hit rate (50.9%) is below a bookmaker favourite's.

## Operating Context

- Picks are published daily at ~07:00 Lagos (Cloudflare cron → GitHub Actions) and pushed to Telegram; the site is read in the morning and before kickoffs.
- Users act on picks at bookmakers: booking codes (SportyBet, MSport, Bet9ja), the slip Builder, the Slip Checker.
- Optional Google sign-in keeps a personal **My Record** (tracked picks, stakes in naira, P/L). The model's record stays global and separate.

## Capabilities and Constraints

- Existing, live system (static HTML + Tailwind CDN + vanilla JS on Cloudflare Pages; FastAPI on Render). **This is a refinement of the incumbent system, not a redesign** (Boss, 28 Sep 2026: "we have our system currently, we just need to fix it up").
- Six pages: Picks (index), Builder (slip), Record, Consensus, Checker, My Record (account); plus privacy/terms.
- Shared chrome, tokens, dark mode, accessibility helpers and error/booking copy live in `webapp/js/api.js` and `webapp/js/theme.js`.
- Terminology: HIGH / MEDIUM / LOW = confidence tiers only. Risk is shown as Safer / Balanced / Longer odds (`PP_RISK_LABEL`).

## Brand Commitments

- Name **PotentialPro**, logo `webapp/logo.png` / `logo-mark.png`.
- Voice: calm, plain, honest; no hype, no "guaranteed", no celebratory styling on numbers that have not earned it (chosen by Boss, Sept 2026).
- Every disclosure the product makes (coverage, draw ceiling, calibration, market check, 18+) must stay visible — design may make it clearer, never remove it.

## Evidence on Hand

Real, live data only: the graded ledger (db/sports_predictor.db), `/api/stats` (hit rates, coverage, calibration), `/api/stats/trend`, real daily picks with SportyBet prices. There are **no** testimonials, user counts, press or partnerships — never fabricate them.

## Product Principles

1. **Trust is earned by disclosure.** Show the denominator, the losses and the uncertainty next to every claim.
2. **Simple first, depth on demand.** The everyday punter gets the answer in one glance; the analyst finds the reasoning one tap away.
3. **The record is the product.** Nothing in the interface may out-shout the evidence.
4. **Honest states.** Loading, empty, stale and error states say exactly what is true.

## Accessibility & Inclusion

Keyboard focus visible on every control, decorative icons hidden from screen readers, icon-only controls labelled (shipped site-wide 27 Sep 2026). 18+ product; responsible-gambling notice on sign-up.
