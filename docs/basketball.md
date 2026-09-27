# Basketball as the second sport — groundwork, 11 Aug 2026

> Status (27 Sep 2026): **the LEDGER and the TRAINING HISTORY exist; no model,
> odds, markets or picks.** `scrapers/espn_ledger.py` records NBA (and NFL, NHL,
> MLB) fixtures and finals daily; `data/sports_history/nba.csv` holds 20,497
> games since 2010-11. Before building the model, read §7.
>
> Original status (11 Aug): **RESEARCH ONLY. Nothing built, nothing wired into the pipeline.**
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

1. ~~**Does ESPN carry NBA odds?**~~ **ANSWERED YES — 17 Sept 2026, probed
   live.** This was the load-bearing unknown and it clears. See §4a below for
   the answer and for the two things it turned up that are NOT free.
2. Do the SportyBet OT markets (219/223/225) stay populated for NBA
   specifically, and how many days ahead?
3. Does Bet9ja offer basketball at all, and under which market ids?

### 4a. ESPN NBA odds: answered, with two catches (17 Sept 2026)

`basketball/nba/scoreboard?dates=20261022` returns a full `odds` block on
opening night, DraftKings, containing **more** than the football path gets:

```
details:    "PHI -4.5"
moneyline:  home close -175 / open -175,  away close +145 / open +145
overUnder:  233.5
spread:     -4.5
```

So the moneyline is there — the feature `predict.py` refuses to work without.
The `overUnder` and `spread` are a bonus football does not get for free, and
they are exactly the inputs §1 said a separate totals model would need. That is
a real head start, not a nice-to-have.

**Catch 1 — `_extract_odds` rejects every basketball game, and does it
silently.** The format is not the problem: `kickoff_times._american_to_decimal`
already converts `-175` → 2.57 and `+145` → 2.45, and it is sport-agnostic. The
problem is the gate on the line below it:

```python
h, d, a = side("home"), side("draw"), side("away")
if h and d and a:          # <- basketball has no draw leg
    return (round(h, 2), round(d, 2), round(a, 2))
return None
```

A two-way market has no `draw`, so `d` is always `None` and the function
returns `None` for every NBA fixture that ever passes through it. **This means
§3's claim that `kickoff_times.py` "ports to basketball by changing a league
slug" is wrong for odds** — true for kickoff times and live state, false here.
And it fails the way this codebase's worst bugs fail: no error, no log line,
just an odds-less fixture that `predict.py` then correctly skips. It would read
as "ESPN has no NBA odds", which is the opposite of what is true.

The fix is not a patch to this function. A 2-way market is a different shape
from a 3-way one all the way up: the model's feature vector is
`odds_home/draw/away`, `odds_is_market` assumes that triple, and the RF is
trained on it. Basketball needs its own extraction and its own feature layout,
not a `d = 1.0` fudge — that would be inventing a prior, which is precisely
what bug #7 exists to forbid.

**Catch 2 — coverage is thin this far out, and unmeasured in season.** Probed
17 Sept, 35 days before tip-off:

| date | events | with odds |
|---|---|---|
| 22 Oct (opening night) | 2 | **2** |
| 28 Oct | 12 | 0 |
| 3 Nov | 0 | — |
| 15 Dec | 2 | 0 |

Only the marquee opening-night games are priced; books have not posted the rest
yet. That is normal and consistent with a daily pipeline that predicts today and
tomorrow — but **it is not evidence that in-season coverage is good.** Football's
whole cloud odds path exists because PredictZ silently served 0 odds from
Actions IPs (see `_extract_odds`'s own docstring), and that was found by
counting, not by assuming. Re-probe a mid-season week once the season is running
and count what fraction of games carry a moneyline before committing to this as
the odds source. If it is low, there is no second free source identified yet.

---

## 5. Suggested order of work (when football is validated)

Deliberately the reverse of the tempting order. The model is the fun part and
the least risky; the plumbing is what actually decides whether this works.

1. ~~**Probe ESPN NBA odds**~~ **DONE 17 Sept 2026 — moneyline present (§4a).**
   Replaced by: re-probe a mid-season week and COUNT moneyline coverage, and
   write a basketball odds extractor rather than reusing the 3-way one.
2. ~~Results + kickoffs~~ **DONE 23-27 Sept 2026** as `scrapers/espn_ledger.py`
   (one ledger for NBA/NFL/NHL/MLB, results joined on ESPN's event id, with
   `results.decided_in` + regulation scores for §2). Original note: port
   `results_espn.py` / `kickoff_times.py` with the NBA slug. This starts building a gradeable ledger BEFORE any prediction exists,
   which is the right way round — coverage first, claims second (bug #16).
   Note the odds half of `kickoff_times.py` does NOT port (§4a catch 1); the
   kickoff/live-state half does.
3. ~~Build the training set from ESPN history~~ **DONE 27 Sept 2026:**
   `data/sports_history/nba.csv`, 20,497 games, preseason and All-Star games
   excluded, franchise ids stored. Results only — every FEATURE built from it
   must pass §7.
4. Model: RF or XGBoost, moneyline first, totals as a SEPARATE model.
   Include days-rest. Keep `MODEL_VERSION` eras separable from football's.
5. Booking codes: markets 219/223/225 only, with a test that asserts no
   basketball leg is ever mapped to market 1, 11 or 18.
6. Frontend: the sport needs to be visible everywhere a pick appears, and the
   track record MUST be reported per sport. A blended "we hit 62%" across two
   sports would hide a failing one behind a working one — the same dishonesty
   as reporting only gradeable picks.

## 6. The thing not to lose

Football was not validated when this file was written: 184 graded picks, tiers
only just ranking in the right order, mid-range calibration visibly off (60-65%
band hitting 50%). Basketball would have been a second unvalidated engine
sharing one credibility score.

**That gate has largely cleared — re-measured 17 Sept 2026.** The ledger is 7x
bigger and the two objections that blocked acting on it are both answered:

    tier       n     hit     band          claim
    HIGH     194   76.3%   >=65%           74.6% hold-out  -> BEATS it
    MEDIUM   215   60.9%   60-65%          ~65%            -> slightly under
    LOW      938   44.6%   below 60%       n/a
    served   409   ~68%                    69.1% predicted -> HITS it

- **1,347 graded picks, coverage 94.1%** (1,431 eligible, 84 ungraded). This is
  the big one. Bug #16 measured the ESPN-backfill ceiling at ~84% and warned
  that every tier figure was a claim about a non-random subset. At 94.1% the
  coverage-bias caveat that blocked every calibration decision is much weaker —
  it is no longer a plausible explanation for the whole signal.
- **Tiers rank monotonically and the separation is wide** (76.3 / 60.9 / 44.6),
  on samples too large for the n=13 and n=47 artefacts that reversed the
  conclusion twice before (CLAUDE.md "What's next" item 4, bug #27).
- **The 26 Aug `TIER_MEDIUM` 0.55 -> 0.60 change was a genuine prediction that
  came true.** It forecast served accuracy near 69.1% from a held-out slice;
  the live ledger has since delivered ~68% on 409 served picks. A threshold
  change that predicted its own effect and hit it is the strongest validation
  evidence in this project so far.

What is still open, and why this is "largely" not "fully": MEDIUM at 60.9% sits
under the ~65% its band implies, so the served figure leans on HIGH. Worth a
re-measure before the tier claims are used in marketing copy. And the honest
top-line has not moved — overall accuracy is still below the ~53-55% a
bookmaker's favourite gets, and the value claim still rests entirely on the
calibrated top tier.

The reason to keep this in a file rather than in the pipeline was that the
ledger cannot be rushed, and it is the only asset here that cannot be rebuilt.
That reason has now been served rather than abandoned: the ledger got its time.
Point 6 of §5 is what carries the principle forward — **per-sport track records,
never blended** — because the moment basketball ships, football's 1,347-pick
record is the thing a blended number would put at risk.

---

## 7. Leakage checklist — read BEFORE writing the trainer (27 Sep 2026)

Written after reading the full codebase of
`nealmick/Sports-Betting-ML-Tools-NBA` (the FireBet.ai tool Boss found). Its
idea is sound — predict both SCORES, derive the margin, and only bet where the
model disagrees with the spread by N+ points. Its published results are not,
because the pipeline makes almost every leakage mistake available, and each one
INFLATES the numbers rather than breaking anything visibly. Each item below is
one of its mistakes, restated as a rule. They apply to every non-soccer sport,
not just basketball, and most are also the lessons of football's bugs #7 and #18.

1. **Point-in-time features only.** Every feature for a game on date D is
   computed from games strictly BEFORE D. Never season averages or
   end-of-season aggregates. *Their mistake:* player features were each
   player's FULL-SEASON average, so a November game was predicted using how
   good the player turned out to be by April.
   **Enforce it with a test, not a promise:** build a game's features from the
   full history, then again from the history truncated at D, and assert they
   are identical. Run it on a random sample of games in CI.

2. **Availability as it was known BEFORE tip-off.** Injury or lineup
   information may only come from a snapshot taken at pick time, the way
   `market_snapshot.py` snapshots the price. *Their mistake:* the "top 7
   players" were taken from the BOX SCORE of the game being predicted, so the
   model knew who had actually played. If there is no pre-game snapshot,
   leave availability out rather than reconstruct it afterwards.

3. **One feature builder for training and serving.** The same function builds
   both, so what the model learns on is what it sees live. *Their mistake:*
   training used the box-score lineup and serving used the roster — the
   train/serve mismatch that is football's bug #7. Assert the feature column
   list at serve time equals the one saved with the model.

4. **Split by DATE, never at random.** The test set is a later period in full
   (e.g. the last season), with no same-season aggregate shared across the
   split. `models/train.py` already does a time-aware 80/20; copy that.
   *Their mistake:* the test set was a random half of 2021-22 games, with the
   other half in training — and both halves shared the same season averages,
   so the test mostly measured memory. Inside training, `test_size=0.0001`
   meant no validation at all.

5. **No hardcoded seasons or dates on the serving path.** *Their mistake:*
   live predictions loaded `2022SeasonAverages` long after 2022. Derive the
   current season from the date, and fail loudly when its data is missing
   (bug #10's "right today, wrong in six weeks").

6. **Beat the MARKET, not a coin.** The baseline is "always back the
   favourite", roughly 65-68% in the NBA — so a claimed "69% accuracy"
   demonstrates nothing on its own. Report results the way
   `db/edge_report.py` does for football: grouped by how far we disagreed with
   the de-vigged market price at pick time, with 95% intervals, compared with
   what the market implied. Not against a flat -110.

7. **Classification metrics for probabilities.** Log loss or Brier score, plus
   calibration by probability band (`db/calibration_health.py` shape).
   *Their mistake:* Keras `accuracy` on a regression model, which is
   meaningless.

8. **Retrain on a schedule, never per game.** Each retrain gets a new
   `MODEL_VERSION` so the eras stay separable in the ledger. *Their mistake:*
   a "retrain" button nudged the weights after every single result, which
   chases noise and makes the model's history unrecoverable.

9. **Snapshot the price at pick time, or the edge question cannot be
   answered.** Odds move and cannot be backfilled (`market_snapshot.py`). A
   pick with no stored price is a pick whose edge is unknowable.

10. **Regulation vs full game (§2).** Grade and book full-game markets
    (219/223/225) against the final, and never let a regulation market
    (1/11/18) reach a basketball leg. `results.decided_in` and
    `reg_*_goals` exist so any regulation grading can be done honestly.

11. **Only games that count.** Preseason, All-Star and other exhibitions are
    already excluded by `espn_ledger._parse_event` (CLAUDE.md bug #31). A new
    data source must reuse that parser or reproduce its filters.

Safe to use freely, because they are genuinely known in advance: rest days
and back-to-backs (the schedule is published months ahead), home/away and
`neutral_site`, and Elo or form built only from earlier results.

What IS worth taking from that repo: predicting both scores gives win
probability, spread and total from one model — a reasonable alternative to
separate moneyline and totals models, **provided every input passes 1-5.**
Its arbitrage finder is correct maths (sum of 1/best odds < 1) but of little
use here: SportyBet and MSport both price off Betradar, so they rarely
disagree, and bookmakers restrict accounts that arbitrage. Licence: a GPL-2.0
file sits in its `bb/` subfolder and none at the root — ideas only.

---

## 8. NBA reference search — what exists and what to use (27 Sep 2026)

Searched GitHub (141 repos across eight queries), shortlisted on licence and
substance, and read the code of five against §7. The finding that matters:
**nothing public beats a well-built Elo by much, and one licensed Elo exists.**

| Repo | Licence | Verdict |
|---|---|---|
| `nicidob/nba_elo` | MIT | **Use.** FiveThirtyEight's NBA Elo reimplemented (K=20, home advantage 100 Elo points, margin-of-victory multiplier `((mov+3)^0.8)/(7.5+0.006*elo_diff)`, season carry-over toward the mean), plus a logistic-regression Elo. Point-in-time by construction: a rating only ever uses games already played. |
| `luke-lite/NBA-Prediction-Modeling` | none | Evidence, not code: on 12k games **plain Elo 65.3% beat every ML model (best 63.5%)**. |
| `cmunch1/nba-prediction` | none | Method is sound (`rolling(..., closed="left")`, latest season held out, log loss + calibration curves, league-average-relative features). Honest: 61.5% vs home-always 58% vs a public model's 65.6%. Ideas only. |
| `atticusdrex/nba-moneyline` | MIT | Features point-in-time (`expanding().sum().shift(1)`), but the train/test split is random (§7 rule 4). |
| `yirgareminick/NBA-Betting-Model` | MIT | **Leaks.** `season_win_pct`/`season_avg_pts` are `shift(1).mean().over(team, season)` — a whole-season mean despite the comment "excluding current game" — and they are model inputs (§7 rule 1). |

**Step (1) DONE 27 Sep 2026 — `models/elo.py`.** Tuned on 2011-12 to 2023-24,
scored once on 2024-25 and 2025-26 (2,643 games): **66.9% accuracy, log loss
0.608** against 55.0% / 0.689 for "home always wins", and calibrated to within
half a point in every band (55->55.0, 65->65.1, 75->74.4, 85->85.1). K=18,
home advantage 60 Elo points, one third reverted each off-season. That is the
bar. All four leagues:

| League | K | HFA | revert | held-out | Elo acc | log loss | home-always |
|---|---|---|---|---|---|---|---|
| NBA | 18 | 60 | 0.33 | 2024-25, 2025-26 (2,643 g) | 66.9% | 0.608 | 55.0% / 0.689 |
| NFL | 20 | 50 | 0.50 | 2024, 2025 (570 g) | 67.0% | 0.623 | 53.1% / 0.692 |
| NHL | 6 | 25 | 0.25 | 2024-25, 2025-26 (2,792 g) | 56.2% | 0.679 | 54.1% / 0.690 |
| MLB | 4 | 20 | 0.33 | 2025, 2026* (4,888 g) | 55.7% | 0.682 | 53.6% / 0.691 |

**Therefore the NBA plan:** (1) build 538-style Elo on
`data/sports_history/nba.csv` as the baseline — cheap, leak-free, and the bar
every later model must clear on log loss; (2) add rest days/back-to-backs;
(3) only then try ML, and keep it only if it beats Elo on a held-out season AND
the market comparison in §7 rule 6. The same Elo code works for the NFL, NHL and
MLB histories with different K and home-advantage values.
