#!/usr/bin/env python3
"""
wc_sync.py — keep the World Cup Night Watch planner current.

What it does
------------
1. RESULTS  : reads fixtures/standings from a results API (football-data.org by
              default) to learn final group positions, the real third-place
              allocation, and completed knockout results. Once the knockout draw
              is made, it just reads who actually plays whom — no guessing.
2. TITLE ODDS: reads Polymarket's "win the 2026 World Cup" markets -> a
              well-calibrated tournament win probability per team.
3. CALIBRATE: fits the planner's Elo-style team ratings with a bracket Monte
              Carlo so the model's simulated title odds match the market. This
              is what grounds the projected (future) matchups in real numbers.
4. MATCH ODDS (optional): reads per-match moneylines (The Odds API) for fixtures
              already on the board, de-vigs them, and stores sharp single-game
              probabilities the planner can use as exact overrides.
5. WRITE     : emits wc-data.json, which the planner (HTML or React) loads to
              override its built-in defaults.

This is a DATA pipeline. Market prices are used only as probability signals;
nothing here is betting advice.

Run it
------
    pip install -r requirements.txt
    export FOOTBALL_DATA_TOKEN=...      # free at football-data.org (optional)
    export ODDS_API_KEY=...             # free at the-odds-api.com (optional)
    python wc_sync.py                   # writes ./wc-data.json
    python wc_sync.py --dry-run         # print, don't write
    python wc_sync.py --no-network      # rebuild JSON from built-in priors only

Schedule it (cron, or the GitHub Action in the README) to refresh after each game.

Note: the Polymarket title-odds parser and team-name matching are verified
against the live Gamma API (all 48 finalists map; book sums to ~0.98). The
results (football-data.org) and per-match odds (the-odds-api) paths were written
without live access, so those two are the places most likely to need a tweak the
first time you wire in their tokens. Each logs what it finds.
"""
from __future__ import annotations
import argparse, json, math, os, random, sys, time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    requests = None  # --no-network still works

# ----------------------------------------------------------------------------
# Team roster (all 48). code -> (display name, flag, base/prior rating, aliases)
# Aliases help match whatever strings the APIs hand back.
# ----------------------------------------------------------------------------
TEAMS = {
    "MEX": ("Mexico","\U0001F1F2\U0001F1FD",1860,["mexico"]),
    "RSA": ("South Africa","\U0001F1FF\U0001F1E6",1775,["south africa","rsa"]),
    "KOR": ("South Korea","\U0001F1F0\U0001F1F7",1835,["south korea","korea republic","korea"]),
    "CZE": ("Czechia","\U0001F1E8\U0001F1FF",1820,["czechia","czech republic"]),
    "SUI": ("Switzerland","\U0001F1E8\U0001F1ED",1895,["switzerland","suisse"]),
    "CAN": ("Canada","\U0001F1E8\U0001F1E6",1835,["canada"]),
    "BIH": ("Bosnia & H.","\U0001F1E7\U0001F1E6",1815,["bosnia","bosnia and herzegovina","bosnia & herzegovina"]),
    "QAT": ("Qatar","\U0001F1F6\U0001F1E6",1760,["qatar"]),
    "BRA": ("Brazil","\U0001F1E7\U0001F1F7",2035,["brazil"]),
    "MAR": ("Morocco","\U0001F1F2\U0001F1E6",1945,["morocco"]),
    "HAI": ("Haiti","\U0001F1ED\U0001F1F9",1660,["haiti"]),
    "SCO": ("Scotland","\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",1825,["scotland"]),
    "USA": ("USA","\U0001F1FA\U0001F1F8",1850,["usa","united states","united states of america"]),
    "PAR": ("Paraguay","\U0001F1F5\U0001F1FE",1785,["paraguay"]),
    "AUS": ("Australia","\U0001F1E6\U0001F1FA",1800,["australia"]),
    "TUR": ("Turkiye","\U0001F1F9\U0001F1F7",1845,["turkiye","turkey","türkiye"]),
    "GER": ("Germany","\U0001F1E9\U0001F1EA",1985,["germany"]),
    "CUW": ("Curacao","\U0001F1E8\U0001F1FC",1640,["curacao","curaçao"]),
    "CIV": ("Ivory Coast","\U0001F1E8\U0001F1EE",1860,["ivory coast","cote d'ivoire","côte d'ivoire"]),
    "ECU": ("Ecuador","\U0001F1EA\U0001F1E8",1865,["ecuador"]),
    "NED": ("Netherlands","\U0001F1F3\U0001F1F1",1995,["netherlands","holland"]),
    "JPN": ("Japan","\U0001F1EF\U0001F1F5",1900,["japan"]),
    "SWE": ("Sweden","\U0001F1F8\U0001F1EA",1875,["sweden"]),
    "TUN": ("Tunisia","\U0001F1F9\U0001F1F3",1800,["tunisia"]),
    "BEL": ("Belgium","\U0001F1E7\U0001F1EA",1945,["belgium"]),
    "EGY": ("Egypt","\U0001F1EA\U0001F1EC",1815,["egypt"]),
    "IRN": ("Iran","\U0001F1EE\U0001F1F7",1815,["iran","ir iran"]),
    "NZL": ("New Zealand","\U0001F1F3\U0001F1FF",1680,["new zealand"]),
    "ESP": ("Spain","\U0001F1EA\U0001F1F8",2095,["spain"]),
    "CPV": ("Cabo Verde","\U0001F1E8\U0001F1FB",1700,["cabo verde","cape verde"]),
    "KSA": ("Saudi Arabia","\U0001F1F8\U0001F1E6",1760,["saudi arabia"]),
    "URU": ("Uruguay","\U0001F1FA\U0001F1FE",1975,["uruguay"]),
    "NOR": ("Norway","\U0001F1F3\U0001F1F4",1945,["norway"]),
    "FRA": ("France","\U0001F1EB\U0001F1F7",2045,["france"]),
    "SEN": ("Senegal","\U0001F1F8\U0001F1F3",1910,["senegal"]),
    "IRQ": ("Iraq","\U0001F1EE\U0001F1F6",1720,["iraq"]),
    "ARG": ("Argentina","\U0001F1E6\U0001F1F7",2060,["argentina"]),
    "ALG": ("Algeria","\U0001F1E9\U0001F1FF",1820,["algeria"]),
    "AUT": ("Austria","\U0001F1E6\U0001F1F9",1865,["austria"]),
    "JOR": ("Jordan","\U0001F1EF\U0001F1F4",1700,["jordan"]),
    "POR": ("Portugal","\U0001F1F5\U0001F1F9",2005,["portugal"]),
    "COD": ("DR Congo","\U0001F1E8\U0001F1E9",1785,["dr congo","congo dr","democratic republic of the congo","congo democratic republic"]),
    "UZB": ("Uzbekistan","\U0001F1FA\U0001F1FF",1760,["uzbekistan"]),
    "COL": ("Colombia","\U0001F1E8\U0001F1F4",1935,["colombia"]),
    "ENG": ("England","\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",2025,["england"]),
    "CRO": ("Croatia","\U0001F1ED\U0001F1F7",1955,["croatia"]),
    "GHA": ("Ghana","\U0001F1EC\U0001F1ED",1805,["ghana"]),
    "PAN": ("Panama","\U0001F1F5\U0001F1E6",1740,["panama"]),
}
NAME_TO_CODE = {}
for _c, (_n, _f, _r, _al) in TEAMS.items():
    NAME_TO_CODE[_n.lower()] = _c
    for _a in _al:
        NAME_TO_CODE[_a.lower()] = _c

def code_for(name: str):
    if not name:
        return None
    key = name.strip().lower()
    if key in NAME_TO_CODE:
        return NAME_TO_CODE[key]
    # loose contains-match fallback
    for alias, c in NAME_TO_CODE.items():
        if alias in key or key in alias:
            return c
    return None

# ----------------------------------------------------------------------------
# Bracket — must mirror the planner exactly.
# ----------------------------------------------------------------------------
# Real R32 ties in FIFA bracket order (group stage complete). Match numbers and
# the tree below mirror the planner's bracket exactly so result keys line up.
R32_TEAMS = {
    73:("GER","PAR"), 74:("FRA","SWE"), 75:("RSA","CAN"), 76:("NED","MAR"),
    77:("POR","CRO"), 78:("ESP","AUT"), 79:("USA","BIH"), 80:("BEL","SEN"),
    81:("BRA","JPN"), 82:("CIV","NOR"), 83:("MEX","ECU"), 84:("ENG","COD"),
    85:("ARG","CPV"), 86:("AUS","EGY"), 87:("SUI","ALG"), 88:("COL","GHA"),
}
CHILDREN = {89:(73,74),90:(75,76),91:(77,78),92:(79,80),93:(81,82),94:(83,84),
            95:(85,86),96:(87,88),97:(89,90),98:(91,92),99:(93,94),100:(95,96),
            101:(97,98),102:(99,100),103:(101,102)}
ORDER = list(range(73, 104))

# Final group standings, 2026 World Cup (group stage complete). [1st, 2nd, 3rd];
# the 3rd entry only matters where that group's third-place team qualified.
# Source: official standings, cross-checked against Polymarket group-winner markets.
DEFAULT_GROUP_SEEDS = {
    "A":["MEX","RSA","KOR"], "B":["SUI","CAN","BIH"], "C":["BRA","MAR","SCO"],
    "D":["USA","AUS","PAR"], "E":["GER","CIV","ECU"], "F":["NED","JPN","SWE"],
    "G":["BEL","EGY","IRN"], "H":["ESP","CPV","KSA"], "I":["FRA","NOR","SEN"],
    "J":["ARG","AUT","ALG"], "K":["COL","POR","COD"], "L":["ENG","CRO","GHA"],
}
# The eight best third-placed teams and the R32 slot each fills (FIFA allocation
# resolved by the actual qualifiers). Derived from the real R32 fixtures.
DEFAULT_THIRD_BY_MATCH = {75:"D",78:"F",79:"E",80:"K",81:"I",82:"B",85:"J",88:"L"}
THIRD_QUAL_DEFAULT = ["B","D","E","F","I","J","K","L"]

def win_prob(ra, rb):
    return 1.0 / (1.0 + 10 ** (-(ra - rb) / 400.0))

# ----------------------------------------------------------------------------
# Monte Carlo title odds for the current bracket + ratings
# ----------------------------------------------------------------------------
def resolve_r32(seeds=None, third_by_match=None):
    """The 16 R32 ties as {match: (codeA, codeB)}. Groups are complete so the
    pairings are fixed; args are accepted only for call-site compatibility."""
    return dict(R32_TEAMS)

def simulate_titles(ratings, seeds, third_by_match, results, n=20000, seed=0):
    rng = random.Random(seed)
    r32 = resolve_r32(seeds, third_by_match)
    champ = {}
    for _ in range(n):
        winners = {}
        for m in ORDER:
            if m in results and results[m]:
                winners[m] = results[m]; continue
            if m < 89:
                a, b = r32[m]
            else:
                ca, cb = CHILDREN[m]; a, b = winners.get(ca), winners.get(cb)
            if a is None and b is None:
                winners[m] = None; continue
            if a is None: winners[m] = b; continue
            if b is None: winners[m] = a; continue
            pa = win_prob(ratings.get(a, 1800), ratings.get(b, 1800))
            winners[m] = a if rng.random() < pa else b
        c = winners.get(103)
        if c:
            champ[c] = champ.get(c, 0) + 1
    return {c: v / n for c, v in champ.items()}

def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))

def calibrate(market, seeds, third_by_match, results, base_ratings,
              iters=30, sims=8000, step=90.0, cap=350.0, floor=0.02, log=print):
    """Nudge ratings so simulated title odds track the market title odds.

    Only teams the market gives a MATERIAL chance (>= floor, default 2%) are
    fitted. Longshots priced at ~0.1% are mostly the book's over-round on the
    field; forcing the sim to reproduce them pins minnows to the rating cap and
    makes Saudi Arabia look as strong as France. Left on their priors, their
    simulated title odds fall naturally as the real contenders are lifted."""
    targets = {c: p for c, p in market.items() if p >= floor}
    ratings = dict(base_ratings)
    for it in range(iters):
        sim = simulate_titles(ratings, seeds, third_by_match, results, n=sims, seed=it)
        worst = 0.0
        damp = step * (1 - 0.5 * it / max(1, iters - 1))  # settle as we converge
        for code, mp in targets.items():
            sp = sim.get(code, 1e-4)
            err = _logit(mp) - _logit(sp)
            worst = max(worst, abs(mp - sp))
            delta = max(-damp, min(damp, damp * err))
            base = base_ratings.get(code, 1800)
            ratings[code] = max(base - cap, min(base + cap, ratings.get(code, base) + delta))
        log(f"  calibrate iter {it+1}/{iters}: max title-odds gap {worst*100:.1f} pts "
            f"(fitting {len(targets)} contenders >= {floor*100:.0f}%)")
        if worst < 0.005:
            break
    return ratings

# ----------------------------------------------------------------------------
# Source 1: results / standings / knockout draw
# ----------------------------------------------------------------------------
def fetch_results_football_data(token, log=print):
    """football-data.org v4. Returns (group_seeds, third_by_match, results)."""
    if not (requests and token):
        log("  results: skipped (no requests or FOOTBALL_DATA_TOKEN)")
        return None
    base = "https://api.football-data.org/v4/competitions/WC/matches"
    try:
        r = requests.get(base, headers={"X-Auth-Token": token}, timeout=30)
        r.raise_for_status()
        matches = r.json().get("matches", [])
    except Exception as e:
        log(f"  results: fetch failed ({e})"); return None

    # --- group standings -> seeds
    tbl = {}  # group -> {code: {pts,gd,gf}}
    ko = []   # knockout matches with assigned teams
    for mt in matches:
        stage = (mt.get("stage") or "").upper()
        grp = (mt.get("group") or "").replace("GROUP_", "").replace("GROUP ", "").strip() or None
        h = code_for((mt.get("homeTeam") or {}).get("name", ""))
        a = code_for((mt.get("awayTeam") or {}).get("name", ""))
        status = mt.get("status")
        ft = (mt.get("score") or {}).get("fullTime", {}) or {}
        hg, ag = ft.get("home"), ft.get("away")
        if stage == "GROUP_STAGE" and grp and h and a:
            for c in (h, a):
                tbl.setdefault(grp, {}).setdefault(c, {"pts":0,"gd":0,"gf":0})
            if status == "FINISHED" and hg is not None:
                _apply(tbl[grp], h, a, hg, ag)
        elif stage in ("LAST_32","ROUND_OF_32","LAST_16","QUARTER_FINALS",
                        "SEMI_FINALS","FINAL","THIRD_PLACE") and h and a:
            winner = None
            if status == "FINISHED" and hg is not None:
                wn = (mt.get("score") or {}).get("winner")
                winner = h if wn == "HOME_TEAM" else a if wn == "AWAY_TEAM" else None
            ko.append({"home":h,"away":a,"winner":winner})

    seeds = {g: _rank(tbl[g]) for g in tbl} or dict(DEFAULT_GROUP_SEEDS)
    for g in DEFAULT_GROUP_SEEDS:
        seeds.setdefault(g, list(DEFAULT_GROUP_SEEDS[g]))

    # Knockout pairings are fixed (bracket order in R32_TEAMS) and results come
    # from the Polymarket reader, so we only take final group standings here.
    log(f"  results: {sum(len(v) for v in tbl.values())} group entries read; "
        f"knockout pairings from the fixed bracket")
    return seeds, dict(DEFAULT_THIRD_BY_MATCH), {}

def _apply(t, h, a, hg, ag):
    t[h]["gf"] += hg; t[a]["gf"] += ag
    t[h]["gd"] += hg - ag; t[a]["gd"] += ag - hg
    if hg > ag: t[h]["pts"] += 3
    elif ag > hg: t[a]["pts"] += 3
    else: t[h]["pts"] += 1; t[a]["pts"] += 1

def _rank(group):
    return [c for c, _ in sorted(group.items(),
            key=lambda kv: (kv[1]["pts"], kv[1]["gd"], kv[1]["gf"]), reverse=True)]

# ----------------------------------------------------------------------------
# Source 2: Polymarket title odds
# ----------------------------------------------------------------------------
POLYMARKET_WINNER_SLUG = "world-cup-winner"

def fetch_title_odds_polymarket(log=print):
    """Read Polymarket's 'World Cup Winner' market -> normalized title probs.

    Verified against the live Gamma API (Jun 2026). The outright-winner market
    lives at event slug 'world-cup-winner' as ~60 binary "Will <team> win the
    2026 FIFA World Cup?" sub-markets. Each carries the team in groupItemTitle
    and a Yes/No price pair; the Yes price is the implied title probability.
    NOTE: the /events 'search' param is silently ignored by Gamma — it returns a
    generic list — so we MUST query by slug, not search. (That bug priced a lone
    team at "100%" and blew a rating to its cap.)"""
    if not requests:
        return None
    try:
        r = requests.get("https://gamma-api.polymarket.com/events",
                         params={"slug": POLYMARKET_WINNER_SLUG}, timeout=30)
        r.raise_for_status()
        events = r.json()
    except Exception as e:
        log(f"  polymarket: fetch failed ({e})"); return None
    events = events if isinstance(events, list) else events.get("data", [])
    if not events:
        log(f"  polymarket: event '{POLYMARKET_WINNER_SLUG}' not found"); return None

    prices = {}  # code -> raw Yes price (implied title prob)
    for ev in events:
        for mk in ev.get("markets", []):
            code = code_for(mk.get("groupItemTitle") or "")
            yes = _yes_price(mk)
            if code and yes is not None and yes > 0:
                prices[code] = yes

    # Sanity guard: a credible outright market prices many teams and the raw Yes
    # prices sum to roughly 1 (a book is slightly over-round). Anything else is a
    # bad read -> return None so we calibrate on nothing and keep the priors.
    raw_sum = sum(prices.values())
    if len(prices) < 10 or not (0.5 <= raw_sum <= 1.8):
        log(f"  polymarket: implausible market ({len(prices)} teams, sum={raw_sum:.2f}); skipping")
        return None

    norm = {c: p / raw_sum for c, p in prices.items()}  # normalize to 1
    top = sorted(norm, key=norm.get, reverse=True)[:4]
    log(f"  polymarket: {len(norm)} teams priced (raw sum {raw_sum:.2f}; top: "
        + ", ".join(f"{c} {norm[c]*100:.0f}%" for c in top) + ")")
    return norm

# Polymarket 'reach round X' markets, in bracket order. A team priced at ~1.0 in
# the market for the NEXT round has, by definition, already won its match in the
# round below -> a decided result. (match-number range each market decides.)
KO_STAGE_SLUGS = [
    ("world-cup-nation-to-reach-round-of-16",   73, 88),  # won R32
    ("world-cup-nation-to-reach-quarterfinals", 89, 96),  # won R16
    ("world-cup-nation-to-reach-semifinals",    97, 100), # won QF
    ("world-cup-nation-to-reach-final",        101, 102), # won SF
    ("world-cup-winner",                       103, 103), # won Final
]

def _reach_probs(slug, log):
    try:
        r = requests.get("https://gamma-api.polymarket.com/events",
                         params={"slug": slug}, timeout=30)
        r.raise_for_status(); evs = r.json()
    except Exception as e:
        log(f"  knockouts: {slug} fetch failed ({e})"); return {}
    evs = evs if isinstance(evs, list) else evs.get("data", [])
    probs = {}
    for ev in evs:
        for mk in ev.get("markets", []):
            c = code_for(mk.get("groupItemTitle") or "")
            y = _yes_price(mk)
            if c and y is not None:
                probs[c] = y
    return probs

def fetch_knockout_results_polymarket(seeds, third_by_match, log=print, win=0.99):
    """Decided knockout winners, read from Polymarket 'reach round X' markets.

    A team priced >= `win` to reach the next round has clinched its current-round
    match. Walk the bracket so each decided winner is attributed to the right slot
    and feeds the rounds above it. Returns {match_no: winning_code}."""
    if not requests:
        return {}
    stage_probs = {slug: _reach_probs(slug, log) for slug, _, _ in KO_STAGE_SLUGS}
    def probs_for(m):
        for slug, lo, hi in KO_STAGE_SLUGS:
            if lo <= m <= hi:
                return stage_probs.get(slug, {})
        return {}
    r32 = resolve_r32(seeds, third_by_match)
    results = {}
    for m in ORDER:
        a, b = (r32.get(m, (None, None)) if m < 89
                else (results.get(CHILDREN[m][0]), results.get(CHILDREN[m][1])))
        if not a or not b:
            continue  # this tie isn't set yet (a feeder match is undecided)
        p = probs_for(m)
        if   p.get(a, 0) >= win: results[m] = a
        elif p.get(b, 0) >= win: results[m] = b
    log("  knockouts: " + (", ".join(f"{m}->{results[m]}" for m in sorted(results))
                           if results else "none decided yet"))
    return results

def _as_list(v):
    if isinstance(v, list): return v
    if isinstance(v, str):
        try: return json.loads(v)
        except Exception: return None
    return None

def _yes_price(mk):
    outs = _as_list(mk.get("outcomes")); ops = _as_list(mk.get("outcomePrices"))
    if outs and ops:
        for nm, pr in zip(outs, ops):
            if str(nm).strip().lower() == "yes":
                try: return float(pr)
                except (TypeError, ValueError): return None
    return None

# ----------------------------------------------------------------------------
# Source 3 (optional): per-match moneylines -> de-vigged single-game probs
# ----------------------------------------------------------------------------
def fetch_match_odds(api_key, seeds, third_by_match, log=print):
    if not (requests and api_key):
        return {}
    url = "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds"
    try:
        r = requests.get(url, params={"regions":"eu,uk","markets":"h2h",
                                      "oddsFormat":"decimal","apiKey":api_key}, timeout=30)
        r.raise_for_status()
        games = r.json()
    except Exception as e:
        log(f"  match odds: fetch failed ({e})"); return {}
    r32 = resolve_r32(seeds, third_by_match)
    pair_to_match = {frozenset(v): m for m, v in r32.items() if all(v)}
    out = {}
    for g in games:
        h, a = code_for(g.get("home_team","")), code_for(g.get("away_team",""))
        if not (h and a):
            continue
        dec = _avg_h2h(g, h, a)
        if not dec:
            continue
        ph, pa = 1/dec[0], 1/dec[1]
        s = ph + pa
        ph, pa = ph/s, pa/s   # de-vig
        mno = pair_to_match.get(frozenset({h, a}))
        if mno:
            out[mno] = {h: round(ph,4), a: round(pa,4)}
    log(f"  match odds: {len(out)} board fixtures mapped")
    return out

def _avg_h2h(game, h, a):
    hs, as_ = [], []
    for bk in game.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt.get("key") != "h2h": continue
            for oc in mkt.get("outcomes", []):
                c = code_for(oc.get("name",""))
                if c == h: hs.append(oc.get("price"))
                elif c == a: as_.append(oc.get("price"))
    if hs and as_:
        return (sum(hs)/len(hs), sum(as_)/len(as_))
    return None

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="wc-data.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-network", action="store_true")
    ap.add_argument("--sims", type=int, default=20000)
    ap.add_argument("--no-polymarket", action="store_true")
    args = ap.parse_args()
    log = print

    seeds = {g: list(v) for g, v in DEFAULT_GROUP_SEEDS.items()}
    third_by_match = dict(DEFAULT_THIRD_BY_MATCH)
    results, market, match_odds = {}, None, {}
    sources = {}

    if not args.no_network:
        log("Fetching results...")
        rr = fetch_results_football_data(os.environ.get("FOOTBALL_DATA_TOKEN"), log)
        if rr:
            seeds, third_by_match, results = rr
            sources["results"] = "football-data.org"
        if not args.no_polymarket:
            log("Fetching Polymarket title odds...")
            market = fetch_title_odds_polymarket(log)
            if market: sources["titleOdds"] = "polymarket"
            log("Fetching knockout results (Polymarket)...")
            ko = fetch_knockout_results_polymarket(seeds, third_by_match, log)
            if ko:
                results.update(ko)
                sources["knockoutResults"] = "polymarket"
        log("Fetching per-match odds...")
        match_odds = fetch_match_odds(os.environ.get("ODDS_API_KEY"), seeds, third_by_match, log)
        if match_odds: sources["matchOdds"] = "the-odds-api"

    base = {c: TEAMS[c][2] for c in TEAMS}
    if market:
        log("Calibrating ratings to the market...")
        ratings = calibrate(market, seeds, third_by_match, results, base, log=log)
    else:
        log("No market odds -> using prior ratings.")
        ratings = dict(base)

    final_titles = simulate_titles(ratings, seeds, third_by_match, results, n=args.sims, seed=99)
    third_qual = sorted({third_by_match[m] for m in third_by_match}) or THIRD_QUAL_DEFAULT

    data = {
        "asOf": datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
        "sources": sources or {"results": "built-in priors"},
        "teams": {c: {"n": TEAMS[c][0], "f": TEAMS[c][1], "r": round(ratings[c])} for c in TEAMS},
        "groupSeeds": seeds,
        "thirdByMatch": {str(k): v for k, v in third_by_match.items()},
        "thirdQual": third_qual,
        "results": {str(k): v for k, v in results.items()},
        "marketTitle": {c: round(market[c], 4) for c in market} if market else {},
        "modelTitle": {c: round(p, 4) for c, p in sorted(final_titles.items(), key=lambda kv: -kv[1])},
        "matchOdds": {str(k): v for k, v in match_odds.items()},
    }

    top = sorted(final_titles.items(), key=lambda kv: -kv[1])[:6]
    log("\nModel title odds (post-calibration): " +
        ", ".join(f"{c} {p*100:.1f}%" for c, p in top))

    blob = json.dumps(data, indent=2, ensure_ascii=False)
    if args.dry_run:
        log("\n--- wc-data.json (dry run) ---\n" + blob)
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(blob)
        log(f"\nWrote {args.out} ({len(blob)} bytes).")

if __name__ == "__main__":
    main()
