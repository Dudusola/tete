"""
The ESPN competition slugs this project queries — ONE list, shared.

WHY THIS FILE EXISTS: the list used to live in results_espn.py, seeded to cover
the leagues football-data.org's free tier misses. kickoff_times.py later needed
a much broader list (every competition we predict) and defined its own on top.
The two then disagreed by 31 slugs, and the consequences were invisible but
serious: 184 finished EFL Cup matches sat ungraded because eng.league_cup was
in the kickoff list but not the results list. We knew when those matches
kicked off and that they had finished, but never asked ESPN for the score.

A league we predict must be a league we can GRADE. Keeping one list is what
makes that true by construction rather than by remembering.
"""

# Leagues football-data.org's free tier misses — the original results set.
RESULTS_CORE = {
    "arg.1", "arg.2", "arg.copa", "aus.1", "aut.1", "bra.2", "chi.1", "chn.1",
    "col.1", "den.1", "ecu.1", "fifa.world", "fin.1", "ind.1", "irl.1",
    "jpn.1", "mex.1", "nor.1", "par.1", "per.1", "rus.1", "sco.1", "sui.1",
    "swe.1", "swe.2", "uefa.champions", "uefa.champions_qual", "uefa.europa",
    "uefa.europa.conf", "uefa.europa.conf_qual", "uefa.europa_qual", "uru.1",
    "usa.1", "usa.usl.1", "usa.usl.l1",
}

# Everything else we predict: the big European leagues and the domestic cups.
# These were missing from the results set, which is how EFL Cup ties went
# ungraded for weeks.
EXTRA = {
    "bra.1", "ned.2", "sco.2",
    "eng.1", "eng.2", "eng.3", "eng.4", "eng.league_cup", "eng.fa",
    "esp.1", "esp.2", "ger.1", "ger.2", "ita.1", "ita.2",
    "fra.1", "fra.2", "ned.1", "por.1", "bel.1", "tur.1", "gre.1",
    "concacaf.leagues.cup", "uefa.super_cup", "concacaf.champions",
    "conmebol.libertadores", "conmebol.sudamericana",
    "esp.copa_del_rey", "ger.dfb_pokal", "ita.coppa_italia",
    "fra.coupe_de_france",
}

# Both the results scraper and the kickoff/live enrichment use this.
ALL_LEAGUES = RESULTS_CORE | EXTRA
