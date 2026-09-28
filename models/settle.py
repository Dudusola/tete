"""settle.py - settle one bet leg against a final score.

Used to grade the accumulators users track in My Record (the Builder and the
Checker both produce legs in every market models/markets.py derives). The
public track record grades 1X2 only; this module exists because a user's slip
is Double Chance / Handicap / Over-Under heavy, and a slip is only as settled
as its least-settled leg.

RULES THAT MATTER:
- Selections are matched EXACTLY against strings built from the leg's own
  home/away names, the same way derive_markets() writes them. Anything that
  does not match is UNGRADABLE (None) - never guessed. A wrong settlement is
  bug #1's shape: a result we invented, shown as a result.
- Draw No Bet on a draw is VOID: the stake comes back, the leg counts as odds
  1.00. That is how every bookmaker here settles it.
- The score is the one in `results` - the same score the public record uses.
  For a cup tie that went to extra time a source may record the after-extra-
  time score, while bookmakers settle on 90 minutes. That affects the public
  record identically and is rare; it is noted, not special-cased.
"""

WON, LOST, VOID = "won", "lost", "void"


def settle(market: str, selection: str, home: str, away: str,
           hg: int, ag: int) -> str | None:
    """Return 'won' / 'lost' / 'void', or None if the leg can't be graded."""
    if hg is None or ag is None:
        return None
    hg, ag = int(hg), int(ag)
    m, s = (market or "").strip(), (selection or "").strip()
    res = lambda ok: WON if ok else LOST

    if m == "Match Result":
        table = {f"{home} Win": hg > ag, "Draw": hg == ag, f"{away} Win": ag > hg}
        return res(table[s]) if s in table else None

    if m == "Double Chance":
        table = {f"{home} or Draw": hg >= ag, f"{home} or {away}": hg != ag,
                 f"Draw or {away}": ag >= hg}
        return res(table[s]) if s in table else None

    if m == "Draw No Bet":
        if s not in (f"{home} (DNB)", f"{away} (DNB)"):
            return None
        if hg == ag:
            return VOID
        return res((hg > ag) == (s == f"{home} (DNB)"))

    if m == "Handicap":
        # Half-goal lines only (+/-1.5), so there is never a push.
        for team, diff in ((home, hg - ag), (away, ag - hg)):
            if s == f"{team} +1.5":
                return res(diff >= -1)
            if s == f"{team} -1.5":
                return res(diff >= 2)
        return None

    if m.startswith("Over/Under"):
        for side in ("Over", "Under"):
            if s.startswith(side + " ") and s.endswith(" Goals"):
                try:
                    line = float(s[len(side) + 1:-len(" Goals")])
                except ValueError:
                    return None
                if line != int(line) + 0.5:     # half lines only: no pushes
                    return None
                return res(hg + ag > line if side == "Over" else hg + ag < line)
        return None

    if m == "Both Teams To Score":
        table = {"BTTS - Yes": hg >= 1 and ag >= 1, "BTTS - No": not (hg >= 1 and ag >= 1)}
        return res(table[s]) if s in table else None

    if m == "Result & Goals":
        for team, won_match in ((home, hg > ag), (away, ag > hg)):
            for line in (1.5, 2.5):
                if s == f"{team} & Over {line}":
                    return res(won_match and hg + ag > line)
        return None

    return None


if __name__ == "__main__":
    H, A = "Arsenal", "Chelsea"
    cases = [
        ("Match Result", "Arsenal Win", 2, 1, WON), ("Match Result", "Draw", 1, 1, WON),
        ("Match Result", "Chelsea Win", 2, 1, LOST),
        ("Double Chance", "Arsenal or Draw", 1, 1, WON), ("Double Chance", "Arsenal or Chelsea", 0, 0, LOST),
        ("Double Chance", "Draw or Chelsea", 2, 1, LOST),
        ("Draw No Bet", "Arsenal (DNB)", 1, 1, VOID), ("Draw No Bet", "Chelsea (DNB)", 0, 2, WON),
        ("Draw No Bet", "Arsenal (DNB)", 0, 2, LOST),
        ("Handicap", "Arsenal +1.5", 0, 1, WON), ("Handicap", "Arsenal +1.5", 0, 2, LOST),
        ("Handicap", "Chelsea -1.5", 0, 2, WON), ("Handicap", "Chelsea -1.5", 0, 1, LOST),
        ("Over/Under 2.5", "Over 2.5 Goals", 2, 1, WON), ("Over/Under 2.5", "Under 2.5 Goals", 2, 1, LOST),
        ("Over/Under 1.5", "Under 1.5 Goals", 1, 0, WON),
        ("Both Teams To Score", "BTTS - Yes", 1, 1, WON), ("Both Teams To Score", "BTTS - No", 2, 0, WON),
        ("Result & Goals", "Arsenal & Over 2.5", 3, 0, WON), ("Result & Goals", "Arsenal & Over 2.5", 2, 0, LOST),
        ("Result & Goals", "Chelsea & Over 1.5", 3, 0, LOST),
        # Ungradable: never guessed.
        ("Match Result", "Arsenal FC Win", 2, 1, None), ("Correct Score", "2-1", 2, 1, None),
        ("Over/Under 2", "Over 2 Goals", 3, 0, None), ("Match Result", "Arsenal Win", None, 1, None),
    ]
    bad = [(c, settle(c[0], c[1], H, A, c[2], c[3])) for c in cases
           if settle(c[0], c[1], H, A, c[2], c[3]) != c[4]]
    for c, got in bad:
        print("FAIL", c, "->", got)
    print(f"{len(cases) - len(bad)}/{len(cases)} passed")
    raise SystemExit(1 if bad else 0)
