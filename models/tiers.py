"""Confidence-tier thresholds — the single source of truth.

These two floats decide which picks are served at all and what badge each one
wears. They used to live in `models/predict.py` and were RE-TYPED AS ENGLISH
PROSE in `webapp/record.html` ("MEDIUM — 55–65%"). When TIER_MEDIUM moved from
0.55 to 0.60 on 26 Aug 2026 the prose was not updated, so the one page whose
entire purpose is honest disclosure published the wrong bands for a day. No
build step could catch it, because the two copies had nothing in common but a
number typed twice.

So they live here now: a module with no dependencies beyond the standard
library, importable by anything. `models/predict.py` imports them to make the
picks; `api/server.py` serves them from `/api/stats/tiers`; `record.html` builds
its tooltip from that response. One number, one place, three readers.

Deliberately import-cheap. `models/predict.py` pulls in joblib, pandas and
sklearn at module level, which is fine for a batch job but not for the API
process — the Render dyno has 512MB and a ~50s cold start already. Keep this
file free of imports so the API can read the thresholds without paying for the
model. Nothing here should ever need one.
"""

# Confidence tiers calibrated from training evaluation.
#   >=65% model prob -> ~73% historical accuracy -> HIGH
#   >=60%            -> ~62% for the band        -> MEDIUM
# Re-measured on rf_v2 (23 Aug 2026) and the ACCURACY AT A THRESHOLD held:
# 65% -> 74.6% (was 74.2%), 55% -> 65.0% (was 65.1%). The fix did not move the
# accuracy at a given threshold; it moved the VOLUME that clears it (65% tier:
# 8.4% -> 12.5% of matches).
#
# TIER_MEDIUM raised 0.55 -> 0.60 (26 Aug 2026). Measured on a 13,762-fixture
# held-out slice, time-ordered so no future informs the fit:
#
#     bar    served picks   served accuracy
#     0.55        12.5/day            65.1%
#     0.60         8.5/day            69.1%
#     0.62         7.0/day            70.8%
#
# TIER_HIGH is deliberately NOT raised, because raising it cannot improve the
# record. Served picks are everything above the MEDIUM bar, so served accuracy
# is a function of that bar alone — moving TIER_HIGH only relabels picks between
# the two tiers. Measured: 0.70 and 0.72 both leave served accuracy at exactly
# 65.1% while halving HIGH's volume from 5.4 to 2.7 picks a day. HIGH stays at
# 0.65 delivering ~73%, so its bar, its claim and its history all stay
# comparable across this change.
#
# 0.62 was rejected on volume: it thins the MEDIUM band to 1.7 picks a day, and
# a tier that fires twice a week is not a product. 0.60 is the knee.
#
# This does NOT contradict the standing "do not re-tune from a single run"
# warning in predict.py — the evidence here is 13,762 held-out fixtures, roughly
# 58x the 236 served picks the ledger has graded. The ledger remains the real
# test, and the expected effect there is a served rate near 69%.
#
# Do not re-tune these from a single run — the served-picks record in the ledger
# is the real test.
TIER_HIGH = 0.65
TIER_MEDIUM = 0.60


# The tiers as BANDS, which is how a reader experiences them: a MEDIUM pick is
# one that cleared 60% but not 65%, so quoting MEDIUM as ">=60%" overstates it
# by including every HIGH pick. That exact band-vs-cumulative confusion already
# cost this project a false alarm in the health checks (see the CLAIMED comment
# in db/track_record_health.py), so the band form is defined once, here.
#
# max_prob is None for the top tier because its band IS the cumulative tail.
TIER_BANDS = {
    "HIGH":   {"min_prob": TIER_HIGH,   "max_prob": None},
    "MEDIUM": {"min_prob": TIER_MEDIUM, "max_prob": TIER_HIGH},
    "LOW":    {"min_prob": None,        "max_prob": TIER_MEDIUM},
}


if __name__ == "__main__":
    # Self-test, the convention in this project (there is no pytest).
    assert 0.5 < TIER_MEDIUM < TIER_HIGH < 1.0, "tiers must be ordered and sane"
    assert TIER_BANDS["MEDIUM"]["max_prob"] == TIER_BANDS["HIGH"]["min_prob"], \
        "bands must be contiguous — a gap means picks with no tier"
    assert TIER_BANDS["LOW"]["max_prob"] == TIER_BANDS["MEDIUM"]["min_prob"], \
        "bands must be contiguous — a gap means picks with no tier"
    assert TIER_BANDS["HIGH"]["max_prob"] is None, "top band is the whole tail"
    print(f"TIER_HIGH={TIER_HIGH} TIER_MEDIUM={TIER_MEDIUM}")
    for name, band in TIER_BANDS.items():
        lo = f"{band['min_prob']:.0%}" if band["min_prob"] is not None else "0%"
        hi = f"{band['max_prob']:.0%}" if band["max_prob"] is not None else "100%"
        print(f"  {name:<7} {lo} - {hi}")
    print("tiers self-test OK")
