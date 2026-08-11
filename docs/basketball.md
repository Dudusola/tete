# Basketball as the second sport — groundwork, 11 Aug 2026

> Status: **RESEARCH ONLY. Nothing built, nothing wired into the pipeline.**
> The decision in CLAUDE.md stands — the second sport waits until football is
> validated. This file exists so that when that day comes, the unknowns are
> already answered and the traps are already found. Everything below was
> PROBED against the live services, not assumed.

## Why this file exists

Boss found https://github.com/kyleskom/NBA-Machine-Learning-Sports-Betting
while researching and asked whether it was useful. Assessing it turned up
several things worth writing down before they are forgotten, including one
that would have cost real money.

---

## 1. The reference repo: read it, don't use it

kyleskom/NBA-Machine-Learning-Sports-Betting — 1.7k stars, 570 forks, Python,
last pushed 9 Jan 2026 (~7 months stale), 4 open issues.

**It has NO LICENSE.** `license: null`, no LICENSE file in the tree. Under
default copyright that means all rights reserved: readable, not reusable. For
a project that intends to go commercial, that settles the question of copying
code. Ideas are free; the code is not.

Beyond the licensing, its stack does not survive our constraints:

| Theirs | Ours | Consequence |
|---|---|---|
| TensorFlow 2.14 + XGBoost | scikit-learn RF | TF alone is ~500MB — will not fit Render free tier or our Actions budget |
| `sbrscrape` (Sports Book Review) | SportyBet / Bet9ja / MSport | US books; wrong market entirely for Nigerian bettors |
| Flask app | FastAPI + PWA | We already have this, plus a ledger they don't have |
| No published accuracy, no ledger | honest track record | The one thing that matters here is the thing it lacks |

**What IS worth taking (ideas, not code):**
- `Add_Days_Rest.py` — rest days between games is a genuine NBA signal with no
  football analogue. Easy to miss coming from a football codebase.
- A **separate totals model** rather than deriving O/U from a score matrix the
  way `models/markets.py` does. Basketball totals are not Poisson goals; a
  ~170.5 line is a different kind of quantity to a 2.5 line.
- Confirmation that gradient-boosting / RF over team stats + odds-implied
  probabilities is the standard approach. Our design is not naive.

---

## 2. THE TRAP: regulation-time markets (probed, confirmed)

**This is the most important finding here, and it is the one that would have
cost money.** It is bug #1 wearing a basketball jersey: grading against a
different question than the one we bet on.

SportyBet's basketball feed offers the SAME market ids our football code
already uses — and they mean something different:

| id | desc | scope |
|---|---|---|
| 1 | 1X2 | **REGULATION ONLY** |
| 11 | Draw No Bet | **REGULATION ONLY** |
| 18 | Over/Under | **REGULATION ONLY** |
| 219 | Winner (incl. overtime) | full game ← the real moneyline |
| 223 | Handicap (incl. overtime) | full game, ±1.5 to ±15.5 |
| 225 | Over/Under (incl. overtime) | full game, 164.5-175.5 |

The tell was hiding in plain sight: **market 1 for basketball has a Draw
outcome.** Basketball cannot end in a draw — unless you stop the clock at the
end of the fourth quarter. That Draw is the giveaway that markets 1/11/18 are
regulation-time bets.

So if `booking_codes.py` were pointed at basketball with its existing
`GEN_MARKET_IDS = "1,10,11,16,18,29"`, it would happily generate valid booking
codes on regulation-only markets, while `results_espn.py` graded them against
final scores that INCLUDE overtime. Every OT game would be graded wrong, the
codes would be real, and the ledger would look merely unlucky rather than
broken. Nothing would error.

**Rule for whoever builds this: basketball uses 219 / 223 / 225. A market id
is not portable across sports just because the endpoint is.**

---

## 3. What ALREADY works, probed live

### SportyBet booking codes — mostly free
Same endpoint, same auth-less headers, same Betradar `sr:match:` event ids,
just `sportId=sr:sport:2`:

```
GET https://www.sportybet.com/api/ng/factsCenter/pcUpcomingEvents
    ?sportId=sr:sport:2&marketId=219,223,225&pageSize=100&pageNum=1&option=1
```
Returned `bizCode 10000`, `totalNum 28` (NBA is out of season — these were
LNBP and similar). Because the event ids are Betradar, the MSport half of
`generate_code()` should port for free too, exactly as it did for football.

Note: `marketId` is REQUIRED. Omitting it returns `bizCode 19000` (invalid),
not "all markets" — the same shape of trap as bug #10's page limit.

Bet9ja runs its own ids and its own market palimpsest, so it needs its own
survey; assume nothing carries over from football there.

### ESPN — results, kickoffs and live scores, all for free
`site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD`
returns the **identical payload shape** we already parse for football:
`events[] → competitions[0] → competitors[]` with `homeAway`, `displayName`,
`score`, plus `status.type.state` / `.completed`.

That means `scrapers/kickoff_times.py`, `scrapers/results_espn.py` and
`models/live_scores.py` port to basketball by changing a league slug — the
matcher in `models/team_match.py` is sport-agnostic already. Verified a
completed historical game: 10 Jun 2026, Knicks 107 - 106 Spurs, completed=true.

### Free training history, from a source we already trust
ESPN serves completed NBA scoreboards back to at least 2010 (probed
2010/2015/2019/2022/2025/2026 — all returned completed games). At ~1,230 games
per regular season that is roughly **18,000 historical games** available with
no key, from an API already proven to work from GitHub Actions IPs.

This matters more than it looks: it means we do NOT need the reference repo's
data layer at all.

### What does NOT work
**`stats.nba.com` timed out** from Boss's own Nigerian IP (20s read timeout).
That is the endpoint `nba_api` wraps and the one the reference repo depends on.
Do not build on it without re-probing — and if it needs a proxy from Lagos, it
will almost certainly be worse from a datacenter IP (the Forebet lesson).

---

## 4. Open questions — cheap to answer, but only IN SEASON

The NBA season starts late Oct 2026. These could not be settled in August:

1. **Does ESPN carry NBA odds?** The football path gets its cloud odds from
   ESPN's DraftKings moneyline (`kickoff_times._extract_odds`). Off-season
   fixtures had no `odds` block, so this is unverified. If it is absent, the
   model has no odds-implied features and — per `predict.py`'s refusal to
   invent a prior — would produce ZERO predictions. **Check this before
   building anything else; it is load-bearing.**
2. Do the SportyBet OT markets (219/223/225) stay populated for NBA
   specifically, and how many days ahead?
3. Does Bet9ja offer basketball at all, and under which market ids?

---

## 5. Suggested order of work (when football is validated)

Deliberately the reverse of the tempting order. The model is the fun part and
the least risky; the plumbing is what actually decides whether this works.

1. **Probe ESPN NBA odds** (open question 1). If absent, stop and solve that
   first — everything downstream depends on it.
2. Results + kickoffs: port `results_espn.py` / `kickoff_times.py` with the NBA
   slug. This starts building a gradeable ledger BEFORE any prediction exists,
   which is the right way round — coverage first, claims second (bug #16).
3. Build the training set from ESPN history (~18k games).
4. Model: RF or XGBoost, moneyline first, totals as a SEPARATE model.
   Include days-rest. Keep `MODEL_VERSION` eras separable from football's.
5. Booking codes: markets 219/223/225 only, with a test that asserts no
   basketball leg is ever mapped to market 1, 11 or 18.
6. Frontend: the sport needs to be visible everywhere a pick appears, and the
   track record MUST be reported per sport. A blended "we hit 62%" across two
   sports would hide a failing one behind a working one — the same dishonesty
   as reporting only gradeable picks.

## 6. The thing not to lose

Football is not validated yet: 184 graded picks, tiers finally ranking in the
right order, mid-range calibration still visibly off (60-65% band hitting 50%).
Basketball would be a second unvalidated engine sharing one credibility score.
The reason to keep this in a file rather than in the pipeline is that the
ledger cannot be rushed, and it is the only asset here that cannot be rebuilt.
