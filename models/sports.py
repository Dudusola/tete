"""Which sport a row belongs to — and the SQL that keeps them apart.

WHY THIS FILE EXISTS
--------------------
`matches.sport` has been in the schema since the beginning (`db/database.py:30`,
NOT NULL), every one of the 5,702 rows says 'football', and **not one query in
the codebase filtered on it.** 24 sites read `FROM matches`; zero had a sport
predicate. The column was documentation, not a constraint.

That is harmless while one sport exists and becomes a data-integrity bug the
moment a second one lands. `docs/basketball.md` §5 step 2 says to port the ESPN
results and kickoff scrapers first, so a gradeable basketball ledger builds up
before any prediction is made. Do that against the schema as it was, and the
first NBA row flows straight into `/api/picks`, `/api/slip` and — the one that
matters — `/api/stats`, blending two sports into one accuracy figure.

§6 of that same doc calls a blended number the same dishonesty as reporting only
gradeable picks, and it is worse than it sounds here: football's record is
1,347 graded picks at 94.1% coverage, the only asset in this project that cannot
be rebuilt. An unvalidated second engine silently averaged into it would not
error, would not look wrong, and would take weeks of ledger to unwind.

So the filter goes in FIRST, while the database is still 100% football and the
change is therefore a provable no-op — every endpoint returns byte-identical
output before and after. There is no safer moment to do this, and every day of
basketball data makes it harder to verify.

USAGE
-----
    from models.sports import FOOTBALL, sport_sql

    rows = conn.execute(f'''
        SELECT ... FROM matches m
        WHERE m.match_date >= date('now')
          {sport_sql(FOOTBALL)}
    ''')

`sport_sql` emits a literal, not a bind parameter, because these queries are
f-strings assembled at import time and threading placeholders through every one
of them would be a bigger change than the fix. That is safe ONLY because the
value can never come from a request: `sport_sql` rejects anything not in
`SPORTS`, so an attacker-controlled string cannot reach the SQL even if a future
endpoint carelessly passes one through. Keep that check — it is the reason the
interpolation is defensible.

WHAT THIS DOES NOT DO
---------------------
Nothing here makes the engine multi-sport. `models/markets.py` still prices with
a Poisson goals matrix, which is wrong for basketball (a 170.5 line is not a 2.5
line); `models/predict.py` still expects a 3-way `odds_home/draw/away` feature
vector, which a 2-way moneyline cannot fill. Those are real work, documented in
`docs/basketball.md`. This file only guarantees that when basketball rows do
exist, the football surfaces cannot see them by accident.
"""

FOOTBALL = "football"
BASKETBALL = "basketball"

# Every sport that may legally appear in `matches.sport`. A value outside this
# set is a bug in whatever wrote the row, not a new sport to be tolerated.
SPORTS = frozenset({FOOTBALL, BASKETBALL})


def sport_sql(sport: str, alias: str = "m") -> str:
    """An `AND <alias>.sport = '<sport>'` fragment, for appending to a WHERE.

    Always returns a leading `AND`, so it can only be used where a WHERE clause
    already exists. That is deliberate: a fragment that silently became the
    whole predicate would be easy to paste into a query with no WHERE at all and
    produce invalid SQL at runtime rather than at review time.

    Raises on an unknown sport rather than returning an empty string. A typo
    ('footbal') that quietly filtered nothing would reintroduce exactly the bug
    this module exists to close, and it would look like it was working.
    """
    if sport not in SPORTS:
        raise ValueError(
            f"unknown sport {sport!r} — add it to models.sports.SPORTS "
            f"deliberately; known: {sorted(SPORTS)}"
        )
    if not alias.isidentifier():
        raise ValueError(f"bad table alias {alias!r}")
    return f"AND {alias}.sport = '{sport}'"
