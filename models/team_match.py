"""
team_match.py - the abbreviation-aware team name matcher, in ONE place.

WHY THIS FILE EXISTS (Aug 2026). Two matchers were in play against the same
ESPN feed:

  * scrapers/kickoff_times.py had this one, which understands how feeds
    actually differ — "Wolves"/"Wolverhampton Wanderers",
    "Union Saint Gilloise"/"Union St.-Gilloise".
  * models/live_scores.py used models.consensus.team_sim.

The two differ in BOTH directions, and getting that backwards is how this
module shipped with a hole in it. consensus.team_sim is not "plain string
similarity" — it is stricter, having gained two safeguards in c22a506 that
this one lacked:

  * containment requires the FIRST WORD to agree, or "AC Milan" (noise word
    stripped -> "milan") is contained in "Inter Milan" and scores 0.92;
  * multi-word names that share a token but differ on the rest are clamped to
    0.65, or "Manchester United"/"Manchester City" rides the shared
    "manchester" to 0.81 — and 0.91 as a PAIR against a shared opponent,
    which is what actually gets tested.

Both are now here. So this module is the union: kickoff_times' abbreviation
handling AND consensus' anti-false-match guards. Checking the per-team score
against the 0.86 bar is NOT sufficient and was the mistake that let this
through review the first time — a fixture is matched as a PAIR, and an
identical opponent drags a 0.81 team score over the line.

That mismatch meant live_scores could not find a fixture that kickoff_times
had ALREADY matched — the espn_league column it keys off is written by
kickoff_times, so every row it looks up is by definition one the strong
matcher succeeded on. Measured on Bodo/Glimt v Union Saint Gilloise (live at
2-2, 90'+3): consensus scored the away pair 0.65 against a 0.80 floor and the
score silently never appeared, while the same fixture matched at 0.92 here.
A weaker matcher downstream of a stronger one can only ever lose rows.

Bug #12's rule, applied to matching instead of to booking providers: one
implementation, read by everyone who needs it.

THE PRICE OF THE FIRST-WORD GUARD, measured rather than guessed: on 76
upcoming fixtures it costs 2 real matches — "Recoleta"/"Deportivo Recoleta"
and "Torque"/"Montevideo City Torque", where a genuine club name is contained
in the other but the LEADING word differs. Structurally that is the same shape
as "Milan" inside "Inter Milan", so no lexical rule separates them; the guard
cannot keep one without the other. 28 -> 26 matched.

Paying it is right, and the reason is worth stating: a bad match here does not
just show a wrong score. kickoff_times.py writes ESPN ODDS off this matcher,
and odds are model FEATURES — so a crossed fixture silently poisons a
prediction. A missed match degrades to kickoff_utc = NULL, which downstream
already treats as "unknown" and keeps visible. Losing a timing is recoverable;
predicting off another match's odds is not.

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

    ta, tb = set(na.split()), set(nb.split())
    # Containment, but ONLY when the first word agrees. Without that clause
    # "AC Milan" (noise word stripped -> "milan") is contained in "Inter Milan"
    # and the two score 0.92 — different clubs entirely. "Santos" / "Santos
    # Laguna" and "Club Tijuana" / "Club Tijuana de Caliente" both keep their
    # leading word, which is what makes them safe.
    if ta and tb and (ta <= tb or tb <= ta) and na.split()[0] == nb.split()[0]:
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

    # Multi-word names that SHARE a word but differ on the rest are the
    # dangerous case: "Manchester United" vs "Manchester City" scores 0.81 on
    # raw similarity purely from the shared "manchester", and paired with an
    # identical opponent that is 0.91 as a PAIR — comfortably over the bar.
    # The distinguishing words have to agree too.
    only_a, only_b = ta - tb, tb - ta
    if only_a and only_b:
        best_distinct = max(_sim(x, y) for x in only_a for y in only_b)
        if best_distinct < 0.6:
            best = min(best, 0.65)

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
