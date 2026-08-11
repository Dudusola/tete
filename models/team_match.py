"""
team_match.py - the abbreviation-aware team name matcher, in ONE place.

WHY THIS FILE EXISTS (Aug 2026). Two matchers were in play against the same
ESPN feed:

  * scrapers/kickoff_times.py had this one, which understands how feeds
    actually differ — "Wolves"/"Wolverhampton Wanderers",
    "Union Saint Gilloise"/"Union St.-Gilloise".
  * models/live_scores.py used models.consensus.team_sim, which is plain
    string similarity.

That mismatch meant live_scores could not find a fixture that kickoff_times
had ALREADY matched — the espn_league column it keys off is written by
kickoff_times, so every row it looks up is by definition one the strong
matcher succeeded on. Measured on Bodo/Glimt v Union Saint Gilloise (live at
2-2, 90'+3): consensus scored the away pair 0.65 against a 0.80 floor and the
score silently never appeared, while the same fixture matched at 0.92 here.
A weaker matcher downstream of a stronger one can only ever lose rows.

Bug #12's rule, applied to matching instead of to booking providers: one
implementation, read by everyone who needs it.

NOT (yet) used by consensus.py, dedupe_fixtures.py, results_api.py or
clubelo.py. Those group and GRADE — a looser matcher there risks merging two
different fixtures or scoring a pick against the wrong match, which is bug #1
territory. Switching them over is a deliberate change that wants its own
before/after count on the ledger, not a drive-by.
"""

import re
import unicodedata

from models.consensus import _sim

# Both sides must be plausible, and the PAIR must be strong on average — so
# one exact name plus one abbreviation matches, but two vague names don't.
SIM_FLOOR = 0.55        # neither side may be worse than this
SIM_MEAN = 0.86         # ...and the two together must average this
# 0.86 not 0.82: "Manchester United" vs "Manchester City" scores 0.81 on raw
# string similarity, which is unavoidable. Requiring a high PAIR mean means a
# wrong fixture would need the OTHER team to match strongly too.

# Noise words that carry no identity ("FC Thun" vs "Thun").
# NOTE: "united" and "city" are NOT here. Dropping them made "Manchester
# United" and "Manchester City" both normalise to "manchester" and score 1.00 —
# they carry identity, they are not noise.
_NOISE = {"fc", "cf", "sc", "afc", "ac", "club", "de", "the",
          "sv", "bk", "if", "fk", "cd", "ca", "us", "ss",
          # Honorifics that one feed spells out and another abbreviates:
          # "Union St.-Gilloise" vs "Union Saint Gilloise". Punctuation is
          # already stripped below, so "st" and "saint" both land here and
          # the two names normalise identically.
          "st", "saint", "sainte", "santo"}


def normalise(name: str) -> str:
    """Lowercase, strip accents and punctuation, drop noise words."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = [t for t in s.split() if t and t not in _NOISE]
    return " ".join(tokens) or s.strip()


def team_sim(a: str, b: str) -> float:
    """Similarity that understands abbreviations.

    Beyond raw string similarity, treat a shared leading stem of >=5 chars as
    strong evidence ("wolves" / "wolverhampton"), and full containment as near
    certain ("santos" inside "santos laguna"). Both are how these feeds
    actually differ; neither matches two genuinely different clubs.
    """
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    best = _sim(na, nb)

    # One name fully contained in the other, on a token boundary.
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb and (ta <= tb or tb <= ta):
        best = max(best, 0.92)

    # Shared leading stem: wolves / wolverhampton. Restricted to cases where
    # one side is a SINGLE word, otherwise shared first words fire wrongly —
    # "Real Madrid" vs "Real Sociedad" and "Inter Turku" vs "Inter Milan" both
    # scored 0.88 on the stem "real" / "inter" alone.
    if len(ta) == 1 or len(tb) == 1:
        fa, fb = na.split()[0], nb.split()[0]
        stem = 0
        for ca, cb in zip(fa, fb):
            if ca != cb:
                break
            stem += 1
        if stem >= 5 and stem >= min(len(fa), len(fb)) * 0.6:
            best = max(best, 0.88)

    return best


def pair_score(home_a: str, away_a: str, home_b: str, away_b: str):
    """Score a whole fixture. Returns the pair mean, or None if it fails.

    Both callers had this same floor-then-mean logic written out inline; a
    fixture is matched as a PAIR or not at all.
    """
    sh = team_sim(home_a, home_b)
    sa = team_sim(away_a, away_b)
    if sh < SIM_FLOOR or sa < SIM_FLOOR:
        return None
    mean = (sh + sa) / 2
    return mean if mean >= SIM_MEAN else None
