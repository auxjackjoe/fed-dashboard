#!/usr/bin/env python
"""
Dove / Hawk Cheat Sheet - data builder.

    python -I build_data.py            # live build (needs the proxy / internet)
    python -I build_data.py --demo     # synthetic demo data (no internet)

Writes data.json next to this file.  Scoring is a plain word-counting exercise
(see phrases below).  Everything the dashboard shows about *how* a score is made
comes from this file's constants, so the Method tab can never drift from the code.

STATUS: the scoring / aggregation / demo code was run and checked.  The live
fetchers (Fed site, Google/Bing news, BLS, BEA, NY Fed, Yahoo, Wikipedia) could
NOT be tested where this was written (those hosts were blocked) - expect to
tweak parsers on first run.  Each fetcher fails soft and reports in build_log.
"""
import json, os, sys, re, math, time, random, hashlib, html, datetime as dt
import urllib.request, urllib.parse, concurrent.futures, xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
PHOTOS = os.path.join(HERE, "photos")
os.makedirs(CACHE, exist_ok=True); os.makedirs(PHOTOS, exist_ok=True)
DEMO = "--demo" in sys.argv
def _proxy_default():  # office proxy lives in proxy.txt (kept out of git); FED_PROXY env var overrides
    try: return open(os.path.join(HERE, "proxy.txt"), encoding="utf-8").read().strip()
    except OSError: return ""
PROXY = os.environ.get("FED_PROXY", _proxy_default())
START_MONTH = "2022-01"
TODAY = dt.date.today()
LOG = []

def log(src, status, msg=""):
    LOG.append({"source": src, "status": status, "msg": msg})
    print(f"[{status}] {src} {msg}", flush=True)

# ----------------------------------------------------------------- parameters
P = dict(
    speech_weight=3.0, headline_weight=1.0, snippet_bonus=0.3, daily_news_cap=3.0,
    lookback_days=30, lookback_weight=0.5, shrink_K=1.5, tanh_k=1.1,
    scale_percentile=0.93, max_carry=2, min_items=8, flip_before="2024-09-01",
)

# ------------------------------------------------------------- phrase lists
# (id, side, label, weight, regex, example sentence, consume_first)
# consume_first: matched words are removed before later phrases are searched,
# so e.g. "no case for cutting rates" is not ALSO counted as "cutting rates".
PH = [
 ("h_nocut",   "H", "no case or need for cuts", 2.8, r"\bno (?:case|need|urgency|reason|room|scope)\W+(?:\w+\W+){0,2}?(?:cut|cuts|cutting|lower|lowering|ease|easing)", "I see no case for cutting rates.", True),
 ("h_against", "H", "against / rules out a cut", 2.6, r"\b(?:against|opposes?|opposed to|rules? out|ruled out)\W+(?:\w+\W+){0,3}?cuts?\b", "He rules out a rate cut.", True),
 ("h_premat",  "H", "premature or too soon to cut", 2.6, r"\b(?:premature|too soon|too early)\W+(?:\w+\W+){0,4}?(?:cut|cutting|cuts|ease|easing|lower|lowering)", "It is premature to cut rates.", True),
 ("d_room",    "D", "supports / sees room or need for cuts or easing", 2.6, r"\b(?:supports?|supported|favou?rs?|favou?red|backs?|sees?|saw)\W+(?:\w+\W+){0,6}?(?:room|need|case|scope)\W+(?:\w+\W+){0,3}?(?:cuts?|cutting|easing|lower rates)|\b(?:supports?|favou?rs?|backs?)\W+(?:an? |the )?(?:rate )?cuts?\b", "He sees room for rate cuts.", True),
 ("d_dissent", "D", "dissent in favour of a cut", 3.2, r"\bdissent\w*\W+(?:\w+\W+){0,6}?(?:cut|cuts|cutting|lower|easing|ease)", "She dissented in favor of a cut.", True),
 ("h_infhigh", "H", "inflation is high / elevated / sticky", 2.0, r"inflation\W+(?:\w+\W+){0,3}?(?:too high|elevated|high|sticky|stubborn)", "Inflation is still too high.", False),
 ("h_infpers", "H", "elevated / persistent / rising inflation", 1.5, r"\b(?:elevated|persistent|rising|stubborn) inflation|\binflation (?:is |remains )?(?:persistent|rising|stubborn)", "Persistent inflation remains a concern.", False),
 ("h_infup",   "H", "inflation risks tilted to the upside", 2.0, r"inflation risks? (?:are |remain )?tilted (?:to|toward|towards) the upside", "Inflation risks are tilted to the upside.", False),
 ("h_upside",  "H", "upside risks to inflation or prices", 2.0, r"upside risks? to (?:inflation|prices)", "There are upside risks to inflation.", False),
 ("h_expect",  "H", "inflation expectations rising / unanchored", 2.0, r"inflation expectations?\W+(?:\w+\W+){0,2}?(?:rising|unanchored|drifting up|de-?anchor\w*)", "Inflation expectations are becoming unanchored.", False),
 ("h_patience","H", "no rush / patience", 1.5, r"\bno rush\b|\bpatien(?:ce|t)\b", "There is no rush to adjust policy.", False),
 ("h_hold",    "H", "hold / pause on rates", 1.4, r"\b(?:hold|holding|pause|pausing)\b\W+(?:\w+\W+){0,4}?rates?\b|\brates? (?:on hold|unchanged|steady)\b", "The Committee should hold rates steady for now.", False),
 ("h_restrict","H", "higher for longer / policy restrictive", 1.6, r"higher for longer|(?<!too )(?<!overly )\brestrictive (?:stance|policy|enough)", "The Fed will stay higher for longer.", False),
 ("h_hike",    "H", "rate hikes / raise rates / more tightening", 2.0, r"\b(?:rate )?hikes?\b|\brais(?:e|ing|es) (?:interest )?rates\b|\b(?:further|more|additional) (?:tightening|firming)\b", "Additional tightening may be needed.", False),
 ("h_labor",   "H", "labor market solid / resilient / strong", 1.5, r"labor market\W+(?:\w+\W+){0,2}?(?:solid|resilient|strong|tight|robust)", "The labor market remains resilient.", False),
 ("d_cutting", "D", "cutting / lowering rates, easing", 1.8, r"\b(?:cut|cutting|lower|lowering|ease|easing|reduce|reducing)\W+(?:\w+\W+){0,2}?(?:interest )?rates?\b|\brate cuts?\b", "Cutting rates would be appropriate.", False),
 ("d_labor",   "D", "labor market weakening / softening", 2.4, r"labor market\W+(?:\w+\W+){0,2}?(?:weak\w*|soft\w*|cool\w*|deteriorat\w*|slow\w*)|\b(?:weakening|softening|cooling)\W+(?:\w+\W+){0,2}?labor market", "The labor market is weakening.", False),
 ("d_downside","D", "downside risks to jobs or growth", 2.0, r"downside risks?\W+(?:to|for)\W+(?:\w+\W+){0,2}?(?:jobs|employment|labor|growth|activity|outlook)", "There are downside risks to employment.", False),
 ("d_jobloss", "D", "job losses / weak hiring / layoffs rising", 2.2, r"job losses|\bweak(?:er|ening)? (?:hiring|job growth|payrolls|jobs)|\blayoffs? (?:are |have )?(?:rising|increas\w+|picking up)|\brising layoffs", "Layoffs are rising and hiring is weak.", False),
 ("d_unemp",   "D", "unemployment rising", 1.6, r"\bunemployment(?: rate)? (?:is |has been |has )?(?:rising|risen|ticking up|edging up|moving up)|\brising unemployment", "Unemployment is rising.", False),
 ("d_infdown", "D", "inflation moderating / coming down", 2.0, r"\binflation (?:is |has been |has )?(?:moderat\w+|coming down|easing|cooling|declin\w+|falling)|\bdisinflation\w*", "Inflation is moderating.", False),
 ("d_tariff",  "D", "tariffs one-time / look through", 2.2, r"\btariffs?\W+(?:\w+\W+){0,6}?(?:one-time|one time|transitory|temporary)|\blook(?:ing)?[- ]through\W+(?:\w+\W+){0,3}?tariff", "Tariff effects are likely one-time.", False),
 ("d_toorestr","D", "policy too restrictive / too tight", 2.2, r"\b(?:too|overly|unnecessarily) (?:restrictive|tight)\b", "Policy is too restrictive.", False),
 ("d_slow",    "D", "slowdown / recession / weaker growth", 1.0, r"\bslowdown\b|\brecession\w*|\bweaker growth\b|\bslowing (?:growth|economy)\b|\beconomic weakness\b", "Growth is slowing and a slowdown is possible.", False),
 ("d_riskmgmt","D", "risk-management or insurance cut / gradual easing", 1.8, r"\brisk[- ]management (?:cut|approach)|\binsurance cuts?|\bgradual(?:ly)? (?:easing|lowering|reduc\w+|cuts?)", "A gradual easing path is appropriate.", False),
]
PHRASES = [dict(id=i, side=s, label=l, weight=w, pattern=p, example=e, consume=c) for i, s, l, w, p, e, c in PH]
FLIP_ID = "h_hold"
for ph in PHRASES: ph["rx"] = re.compile(ph["pattern"], re.I)

def score_text(text, date_iso):
    """Returns H, D, {family: count}. Every occurrence counts."""
    t = " " + text.lower() + " "
    H = D = 0.0; fam = {}
    for ph in PHRASES:                       # consume-first families are listed first
        n = 0
        def blank(m):
            nonlocal n
            n += 1
            return " " * len(m.group(0)) if ph["consume"] else m.group(0)
        t = ph["rx"].sub(blank, t)
        if not n: continue
        fam[ph["id"]] = n
        side = ph["side"]
        if ph["id"] == FLIP_ID and date_iso < P["flip_before"]:
            side = "D"
        if side == "H": H += ph["weight"] * n
        else: D += ph["weight"] * n
    return H, D, fam

# ---------------------------------------------------------------- members
def mem(id, name, short, role, bank, kind, start, end=None, wiki=None, aliases=None, verify=False):
    return dict(id=id, name=name, short=short, role=role, bank=bank, kind=kind, start=start, end=end,
                wiki=wiki or name.replace(" ", "_"), aliases=aliases or [short], verify=verify)

FED = [
 mem("powell","Jerome Powell","Powell","Governor · Board","Board","gov","2022-01",None,"Jerome_Powell"),
 mem("jefferson","Philip Jefferson","Jefferson","Vice Chair · Board","Board","gov","2022-05",None,"Philip_N._Jefferson"),
 mem("barr","Michael Barr","Barr","Vice Chair, Supervision · Board","Board","gov","2022-07",None,"Michael_Barr_(economist)"),
 mem("cook","Lisa Cook","Cook","Governor · Board","Board","gov","2022-05",None,"Lisa_D._Cook"),
 mem("waller","Christopher Waller","Waller","Governor · Board","Board","gov","2022-01",None,"Christopher_Waller"),
 mem("bowman","Michelle Bowman","Bowman","Governor · Board","Board","gov","2022-01",None,"Michelle_Bowman"),
 mem("warsh","Kevin Warsh","Warsh","Chair · Board","Board","gov","2026-06",None,"Kevin_Warsh"),   # start month is a seed guess
 mem("brainard","Lael Brainard","Brainard","Vice Chair (former)","Board","gov","2022-01","2023-02","Lael_Brainard"),
 mem("quarles","Randal Quarles","Quarles","Governor (former)","Board","gov","2022-01","2022-12","Randal_Quarles"),
 mem("kugler","Adriana Kugler","Kugler","Governor (former)","Board","gov","2023-09","2025-08","Adriana_Kugler"),
 mem("miran","Stephen Miran","Miran","Governor (former)","Board","gov","2025-09","2025-12","Stephen_Miran"),
 mem("williams","John Williams","Williams","President · New York","NY","pres","2022-01",None,"John_C._Williams",["Williams"]),
 mem("collins","Susan Collins","Collins","President · Boston","BOS","pres","2022-01",None,"Susan_M._Collins"),
 mem("paulson","Anna Paulson","Paulson","President · Philadelphia","PHI","pres","2025-07",None,"Anna_Paulson"),
 mem("harker","Patrick Harker","Harker","President · Philadelphia (former)","PHI","pres","2022-01","2023-06","Patrick_T._Harker"),
 mem("barkin","Thomas Barkin","Barkin","President · Richmond","RIC","pres","2022-01",None,"Thomas_Barkin"),
 mem("bostic","Raphael Bostic","Bostic","President · Atlanta (former)","ATL","pres","2022-01","2026-02","Raphael_Bostic"),
 mem("venable","Venable","Venable","Interim President · Atlanta","ATL","pres","2026-03",None,"Atlanta_Fed",["Venable"],True),
 mem("goolsbee","Austan Goolsbee","Goolsbee","President · Chicago","CHI","pres","2022-01",None,"Austan_Goolsbee"),
 mem("evans","Charles Evans","Evans","President · Chicago (former)","CHI","pres","2022-01","2023-01","Charles_L._Evans",["Charles Evans","Chicago Fed"]),
 mem("mester","Loretta Mester","Mester","President · Cleveland (former)","CLE","pres","2022-01","2024-06","Loretta_Mester"),
 mem("hammack","Beth Hammack","Hammack","President · Cleveland","CLE","pres","2024-08",None,"Beth_M._Hammack"),
 mem("musalem","Alberto Musalem","Musalem","President · St. Louis","STL","pres","2023-04",None,"Alberto_Musalem"),
 mem("bullard","James Bullard","Bullard","President · St. Louis (former)","STL","pres","2022-01","2023-07","James_Bullard"),
 mem("kashkari","Neel Kashkari","Kashkari","President · Minneapolis","MIN","pres","2022-01",None,"Neel_Kashkari"),
 mem("schmid","Jeff Schmid","Schmid","President · Kansas City","KC","pres","2023-08",None,"Jeffrey_Schmid"),
 mem("george","Esther George","George","President · Kansas City (former)","KC","pres","2022-01","2023-01","Esther_George",["Esther George"]),
 mem("logan","Lorie Logan","Logan","President · Dallas","DAL","pres","2022-08",None,"Lorie_Logan"),
 mem("daly","Mary Daly","Daly","President · San Francisco","SF","pres","2022-01",None,"Mary_C._Daly"),
]
# Bank-by-bank voting seats (Fed rotation: Cleveland/Chicago alternate; Boston-Phil-Richmond;
# Atlanta-St.Louis-Dallas; Minneapolis-KC-SF).  2026-28 matches the Fed's published table
# as shown on your earlier build.  2022-25 here is a SEED - the live build overwrites it
# from the official FOMC statements (who actually voted, incl. stand-ins).
ROTATION = {
 2022: ["CLE","BOS","STL","KC"], 2023: ["CHI","PHI","ATL","MIN"],
 2024: ["CLE","RIC","DAL","SF"], 2025: ["CHI","BOS","STL","KC"],
 2026: ["CLE","PHI","DAL","MIN"], 2027: ["CHI","RIC","ATL","SF"], 2028: ["CLE","BOS","STL","KC"],
}
YEARS = list(range(2022, 2029))

def ym(s): y, m = s.split("-"); return int(y), int(m)
def month_list(a, b):
    y, m = ym(a); out = []
    while (y, m) <= ym(b):
        out.append(f"{y}-{m:02d}"); m += 1
        if m > 12: y += 1; m = 1
    return out
MONTHS = month_list(START_MONTH, f"{TODAY.year}-{TODAY.month:02d}")

def seed_voters(members):
    v = {}
    for y in YEARS:
        ids = []
        for m in members:
            if m["bank"] == "Board":
                ey = ym(m["end"])[0] if m["end"] else 9999
                if ym(m["start"])[0] <= y <= ey: ids.append(m["id"])
            elif m["bank"] == "NY": ids.append(m["id"])
            elif m["bank"] in ROTATION[y]:
                # president in office during that year
                ey = ym(m["end"])[0] if m["end"] else 9999
                if ym(m["start"])[0] <= y <= ey: ids.append(m["id"])
        v[y] = ids
    return v

OTHER = {
 "ECB": [('panetta', 'Fabio Panetta', 'Panetta', 'Italy'),
         ('rehn', 'Olli Rehn', 'Rehn', 'Finland'),
         ('cipollone', 'Piero Cipollone', 'Cipollone', 'Exec Board'),
         ('stournaras', 'Yannis Stournaras', 'Stournaras', 'Greece'),
         ('moulin', 'Emmanuel Moulin', 'Moulin', 'France'),
         ('demarco', 'Alexander Demarco', 'Demarco', 'Malta'),
         ('lane', 'Philip Lane', 'Lane', 'Exec Board'),
         ('pereira', 'Alvaro Santos Pereira', 'Pereira', 'Portugal'),
         ('kazaks', 'Martins Kazaks', 'Kazaks', 'Latvia'),
         ('simkus', 'Gediminas Simkus', 'Simkus', 'Lithuania'),
         ('patsalides', 'Christodoulos Patsalides', 'Patsalides', 'Cyprus'),
         ('elderson', 'Frank Elderson', 'Elderson', 'Exec Board'),
         ('lagarde', 'Christine Lagarde', 'Lagarde', 'President'),
         ('escriva', 'Jose Luis Escriva', 'Escriva', 'Spain'),
         ('sleijpen', 'Olaf Sleijpen', 'Sleijpen', 'Netherlands'),
         ('makhlouf', 'Gabriel Makhlouf', 'Makhlouf', 'Ireland'),
         ('dolenc', 'Primoz Dolenc', 'Dolenc', 'Slovenia'),
         ('nagel', 'Joachim Nagel', 'Nagel', 'Germany'),
         ('zigman', 'Ante Zigman', 'Zigman', 'Croatia'),
         ('wunsch', 'Pierre Wunsch', 'Wunsch', 'Belgium'),
         ('kocher', 'Martin Kocher', 'Kocher', 'Austria'),
         ('vujcic', 'Boris Vujcic', 'Vujcic', 'Vice President'),
         ('radev', 'Dimitar Radev', 'Radev', 'Bulgaria'),
         ('reinesch', 'Gaston Reinesch', 'Reinesch', 'Luxembourg'),
         ('kaasik', 'Kaasik', 'Kaasik', 'Estonia'),
         ('kazimir', 'Peter Kazimir', 'Kazimir', 'Slovakia'),
         ('schnabel', 'Isabel Schnabel', 'Schnabel', 'Exec Board')],
 "BoE": [("bailey","Andrew Bailey","Bailey","Governor"),("lombardelli","Clare Lombardelli","Lombardelli","Deputy Governor"),
         ("breeden","Sarah Breeden","Breeden","Deputy Governor"),("ramsden","Dave Ramsden","Ramsden","Deputy Governor"),
         ("pill","Huw Pill","Pill","Chief Economist"),("mann","Catherine Mann","Mann","External"),
         ("greene","Megan Greene","Greene","External"),("dhingra","Swati Dhingra","Dhingra","External"),("taylor","Alan Taylor","Taylor","External")],
 "BoJ": [("ueda","Kazuo Ueda","Ueda","Governor"),("himino","Ryozo Himino","Himino","Deputy Governor"),
         ("uchida","Shinichi Uchida","Uchida","Deputy Governor"),("noguchi","Asahi Noguchi","Noguchi","Board"),
         ("takata","Hajime Takata","Takata","Board"),("tamura","Naoki Tamura","Tamura","Board"),
         ("koeda","Junko Koeda","Koeda","Board"),("masu","Kazuyuki Masu","Masu","Board")],
}
def other_members(bank):
    return [mem(i, n, s, f"{r} · {bank}", bank, "cb", START_MONTH, None, n.replace(" ", "_"), [n], True) for i, n, s, r in OTHER[bank]]

# ------------------------------------------------------------------ helpers
def pcts(xs, q):
    xs = sorted(xs)
    if not xs: return 1.0
    k = (len(xs) - 1) * q; f = int(math.floor(k)); c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)

def pearson(a, b):
    pr = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pr) < 8: return None
    mx = sum(x for x, _ in pr) / len(pr); my = sum(y for _, y in pr) / len(pr)
    sxy = sum((x - mx) * (y - my) for x, y in pr)
    sx = math.sqrt(sum((x - mx) ** 2 for x, _ in pr)); sy = math.sqrt(sum((y - my) ** 2 for _, y in pr))
    return round(sxy / (sx * sy), 2) if sx and sy else None

def month_of(d): return d[:7]
def d2date(d): return dt.date.fromisoformat(d)

# ---------------------------------------------------------------- scoring
def finalize_items(raw_items):
    """raw: dicts with d(date), kind, title, text, src, url, snippet(bool). -> scored items (H+D>0 only)."""
    out = []; seen = len(raw_items)
    for it in raw_items:
        H, D, fam = score_text(it["text"], it["d"])
        if H == 0 and D == 0: continue
        base = P["speech_weight"] if it["kind"] == "speech" else P["headline_weight"] + (P["snippet_bonus"] if it.get("snippet") else 0)
        out.append(dict(d=it["d"], k=it["kind"][0], t=it["title"][:200], q=it.get("quote", "")[:300], src=it.get("src", ""), u=it.get("url", ""),
                        H=round(H, 2), D=round(D, 2), s=round((H - D) / (H + D + 1), 4), w=base, f=fam))
    # daily cap on news per member
    byday = {}
    for it in out:
        if it["k"] == "n": byday.setdefault(it["d"], []).append(it)
    for d, its in byday.items():
        tot = sum(i["w"] for i in its)
        if tot > P["daily_news_cap"]:
            f = P["daily_news_cap"] / tot
            for i in its: i["w"] = round(i["w"] * f, 4)
    return out, seen

def member_series(m, items, mu=None):
    """Raw monthly x (pre-scale), with carry logic applied later after scale is known."""
    n = len(items)
    if n < P["min_items"]:
        return None, None, n
    mu = sum(i["w"] * i["s"] for i in items) / sum(i["w"] for i in items)
    xs = []
    for mo in MONTHS:
        y, mm = ym(mo); first = dt.date(y, mm, 1)
        sw = tw = 0.0; own = False
        for i in items:
            d = d2date(i["d"])
            if (d.year, d.month) == (y, mm): f = 1.0; own = True
            elif first - dt.timedelta(days=P["lookback_days"]) <= d < first: f = P["lookback_weight"]
            else: continue
            sw += i["w"] * f * i["s"]; tw += i["w"] * f
        xs.append(((sw + P["shrink_K"] * mu) / (tw + P["shrink_K"]), own) if tw else (None, False))
    return xs, mu, n

def tone_from_x(x, scale): return 100 * math.tanh(P["tanh_k"] * x / scale)

def build_bank(bank, members, items_by_member, seen_by_member):
    series = {}; mus = {}; counts = {}
    for m in members:
        xs, mu, n = member_series(m, items_by_member.get(m["id"], []))
        counts[m["id"]] = n; series[m["id"]] = xs; mus[m["id"]] = mu
    allx = [abs(x) for xs in series.values() if xs for (x, own) in xs if own and x is not None]
    scale = max(pcts(allx, P["scale_percentile"]), 1e-6)
    tone = {}; flag = {}
    for m in members:
        xs = series[m["id"]]
        if not xs: tone[m["id"]] = [None] * len(MONTHS); flag[m["id"]] = [None] * len(MONTHS); continue
        sm, em = ym(m["start"]), (ym(m["end"]) if m["end"] else (9999, 12))
        t_arr = []; f_arr = []; last = None; silent = 0
        for mo, (x, own) in zip(MONTHS, xs):
            if not (sm <= ym(mo) <= em): t_arr.append(None); f_arr.append(None); last = None; silent = 0; continue
            if own and x is not None:
                v = round(tone_from_x(x, scale), 2); t_arr.append(v); f_arr.append("o"); last = v; silent = 0
            elif last is not None and silent < P["max_carry"]:
                silent += 1; t_arr.append(last); f_arr.append("c")
            else:
                t_arr.append(None); f_arr.append(None)
        tone[m["id"]] = t_arr; flag[m["id"]] = f_arr
    return dict(tone=tone, flag=flag, mu={k: (round(v, 5) if v is not None else None) for k, v in mus.items()},
                scale=round(scale, 5), counts=counts, seen=seen_by_member)

# ----------------------------------------------------------- topic cards
TOPICS = {
 "jobs": re.compile(r"\b(?:job|jobs|employment|labor|labour|unemployment|payroll|hiring|layoff)\w*", re.I),
 "inflation": re.compile(r"\b(?:inflation|prices?|tariff|cpi|pce|disinflation)\w*", re.I),
 "guidance": re.compile(r"\b(?:rate|rates|policy|cut|cuts|hike|hikes|easing|tightening|outlook|stance)\b", re.I),
}
def stance_label(v):
    if v is None: return None
    return "Very hawkish" if v > .35 else "Hawkish" if v > .12 else "Neutral" if v > -.12 else "Dovish" if v > -.35 else "Very dovish"

def build_cards(members, items_by_member):
    cards = {}; cutoff = (TODAY - dt.timedelta(days=120)).isoformat()
    for m in members:
        its = [i for i in items_by_member.get(m["id"], []) if i["d"] >= cutoff]
        c = {}
        for tp, rx in TOPICS.items():
            sel = [i for i in its if rx.search(i["t"] + " " + i.get("q", ""))]
            if not sel: c[tp] = None; continue
            sel.sort(key=lambda i: i["d"], reverse=True)
            sw = sum(i["w"] for i in sel); avg = sum(i["w"] * i["s"] for i in sel) / sw
            lt = sel[0]
            c[tp] = dict(n=len(sel), stance=stance_label(avg), avg=round(avg, 3),
                         text=lt.get("q") or lt["t"], date=lt["d"], kind=lt["k"], src=lt["src"], url=lt["u"])
        cards[m["id"]] = c
    return cards

# --------------------------------------------------------------- DEMO data
def demo_everything(members_all):
    rnd = random.Random(20261114)
    anchors = [(0, .30), (8, .45), (18, .50), (30, -.05), (38, .10), (46, -.08), (54, .35), (len(MONTHS) - 1, .52)]
    def base(i):
        for (a, va), (b, vb) in zip(anchors, anchors[1:]):
            if a <= i <= b: return va + (vb - va) * (i - a) / (b - a)
        return anchors[-1][1]
    ex_h = [p for p in PHRASES if p["side"] == "H"]; ex_d = [p for p in PHRASES if p["side"] == "D"]
    items = {}; seen = {}
    for m in members_all:
        h = int(hashlib.md5(m["id"].encode()).hexdigest()[:6], 16)
        off = ((h % 1000) / 1000 - .5) * .5          # synthetic offset, NOT a view of the real person
        speaker_freq = .35 if m["kind"] == "gov" else 0.0
        lst = []; sm, em = ym(m["start"]), (ym(m["end"]) if m["end"] else (9999, 12))
        for i, mo in enumerate(MONTHS):
            if not (sm <= ym(mo) <= em): continue
            y, mm = ym(mo)
            quiet = rnd.random() < (.18 if m["kind"] != "cb" else .12)
            n_news = 0 if quiet else rnd.choice([1, 2, 2, 3, 4, 5])
            kinds = ["news"] * n_news + (["speech"] if rnd.random() < speaker_freq else [])
            for kd in kinds:
                day = rnd.randint(1, 28)
                if (y, mm) == (TODAY.year, TODAY.month): day = min(day, max(1, TODAY.day - 1))
                d = dt.date(y, mm, day).isoformat()
                target = base(i) + off + rnd.gauss(0, .25)
                ph = 1 / (1 + math.exp(-3.2 * target))
                nh = rnd.choice([1, 2, 2, 3]) if kd == "speech" else rnd.choice([1, 1, 2])
                if kd == "speech": nh += 2
                picks = [rnd.choice(ex_h if rnd.random() < ph else ex_d) for _ in range(nh)]
                text = " ".join(p["example"] for p in picks)
                lst.append(dict(d=d, kind=kd, title=f"[DEMO - synthetic text, not a real quote] {m['short']} {'speech' if kd=='speech' else 'headline'}",
                                quote=text if kd == "news" else picks[0]["example"], text=text,
                                src=("federalreserve.gov (demo)" if kd == "speech" else rnd.choice(["Reuters (demo)", "Bloomberg (demo)", "BigGo Finance (demo)", "VT Markets (demo)"])),
                                url="", snippet=rnd.random() < .5))
        items[m["id"]], seen[m["id"]] = finalize_items(lst)
    return items, seen

def demo_macro():
    rnd = random.Random(7); n = len(MONTHS)
    def interp(anch):
        out = []
        for i in range(n):
            for (a, va), (b, vb) in zip(anch, anch[1:]):
                if a <= i <= b: out.append(va + (vb - va) * (i - a) / (b - a)); break
            else: out.append(anch[-1][1])
        return out
    cpi = interp([(0, 7.4), (6, 8.8), (18, 4.2), (30, 2.9), (44, 2.7), (n - 1, 3.1)])
    core = interp([(0, 6.0), (9, 6.2), (24, 3.9), (40, 3.0), (n - 1, 3.0)])
    pce = interp([(0, 5.2), (10, 5.1), (24, 3.1), (40, 2.7), (n - 1, 2.8)])
    un = interp([(0, 4.0), (12, 3.5), (30, 4.1), (n - 1, 4.4)])
    ffr = interp([(0, .08), (3, .33), (6, 2.3), (13, 4.1), (18, 5.1), (31, 5.3), (36, 4.5), (44, 4.2), (n - 1, 3.88)])
    nfp = [round(max(-150, min(420, 200 + (-9 * i if i < 20 else -3 * i) + rnd.gauss(0, 70))), 0) for i in range(n)]
    nfp3 = [round(sum(nfp[max(0, i - 2):i + 1]) / len(nfp[max(0, i - 2):i + 1]), 1) for i in range(n)]
    r = lambda a: [round(x + rnd.gauss(0, .05), 2) for x in a]
    return dict(nfp=nfp, nfp3=nfp3, unemp=r(un), cpi=r(cpi), corecpi=r(core), corepce=r(pce), ffr=r(ffr))

def demo_market(effr):
    rnd = random.Random(3); meets = fomc_dates()
    strip = []; cur = effr
    for i in range(12):
        d = (TODAY.replace(day=1) + dt.timedelta(days=32 * i)).replace(day=1)
        strip.append(dict(month=d.strftime("%Y-%m"), rate=round(effr + .02 * i + rnd.gauss(0, .03) + .0, 3)))
    out = []
    for i, d in enumerate(meets):
        bp = round(.28 * 100 if i == 0 else 100 * (.06 * i + rnd.gauss(0, .03)), 1) / 1.0
        out.append(dict(date=d, bp=round(bp / 4 if i == 0 else bp / 2, 1)))
    return dict(effr=effr, strip=strip, meetings=out, source="DEMO (synthetic)")

FOMC_2026 = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]
FOMC_2027 = ["2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08"]
def fomc_dates():
    return [d for d in FOMC_2026 + FOMC_2027 if d >= TODAY.isoformat()][:8]

# ------------------------------------------------------------- LIVE fetchers
_opener = None
def opener():
    global _opener
    if _opener is None:
        px = PROXY
        if px:  # off the office network the proxy host won't resolve -> go direct instead of failing every fetch
            try:
                import socket
                socket.getaddrinfo(urllib.parse.urlparse(px).hostname, None)
            except Exception:
                print(f"[info] proxy {px} not reachable - connecting directly", flush=True); px = ""
        h = urllib.request.ProxyHandler({"http": px, "https": px}) if px else urllib.request.ProxyHandler()
        _opener = urllib.request.build_opener(h)
        _opener.addheaders = [("User-Agent", "Mozilla/5.0 (DoveHawkDashboard)")]
    return _opener

def http(url, data=None, headers=None, timeout=30, retries=2):
    last = None
    for a in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {})
            with opener().open(req, timeout=timeout) as r: return r.read()
        except Exception as e:
            last = e; time.sleep(1 + a)
    raise last

def cached(key, fn, ttl_days=None):
    p = os.path.join(CACHE, re.sub(r"[^\w.-]", "_", key) + ".json")
    if os.path.exists(p) and (ttl_days is None or time.time() - os.path.getmtime(p) < ttl_days * 86400):
        try: return json.load(open(p, encoding="utf-8"))
        except Exception: pass
    v = fn(); json.dump(v, open(p, "w", encoding="utf-8")); return v

def strip_html(h):
    h = re.sub(r"(?is)<(script|style|nav|header|footer).*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()

def fetch_fed_speeches(members):
    """Governors only: the Fed site does not carry regional presidents' speeches."""
    raw = {m["id"]: [] for m in members}
    try:
        js = json.loads(http("https://www.federalreserve.gov/json/ne-speeches.json").decode("utf-8-sig"))
    except Exception as e:
        log("Fed speeches list", "FAIL", str(e)[:120]); return raw
    gov = [m for m in members if m["kind"] == "gov"]; n = 0
    for e in js:
        if not isinstance(e, dict) or "d" not in e: continue
        try: d = dt.datetime.strptime(e["d"].split(" ")[0], "%m/%d/%Y").date().isoformat()
        except Exception: continue
        if d < START_MONTH + "-01": continue
        spk = e.get("s", "")
        for m in gov:
            if m["short"].lower() in spk.lower() and m["short"] != "Brainard" or m["short"].lower() in spk.lower():
                link = "https://www.federalreserve.gov" + e["l"]
                try:
                    txt = cached("speech_" + e["l"], lambda: strip_html(http(link).decode("utf-8", "ignore")))
                except Exception: continue
                raw[m["id"]].append(dict(d=d, kind="speech", title=e.get("t", "")[:160], text=txt, quote=best_sentence(txt),
                                         src="federalreserve.gov", url=link)); n += 1
    log("Fed speeches", "ok", f"{n} speeches")
    return raw

def best_sentence(txt):
    best = ("", 0)
    for s in re.split(r"(?<=[.!?])\s+", txt):
        if len(s) > 400 or len(s) < 30: continue
        H, D, _ = score_text(s, "2025-01-01"); sc = H + D
        if sc > best[1]: best = (s, sc)
    return best[0]

def _exclude_words():  # private word filter: env NEWS_EXCLUDE (cloud secret) or exclude_words.txt (local, not in git)
    raw = os.environ.get("NEWS_EXCLUDE", "")
    try: raw += "," + open(os.path.join(HERE, "exclude_words.txt"), encoding="utf-8").read()
    except OSError: pass
    return [w.strip().lower() for w in re.split(r"[,\n]", raw) if w.strip()]
EXCLUDE = _exclude_words()

def news_rss(url):
    out = []
    try:
        root = ET.fromstring(http(url))
    except Exception as e:
        raise
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "")).strip()
        link = it.findtext("link") or ""
        pub = it.findtext("pubDate") or ""
        desc = strip_html(html.unescape(it.findtext("description") or ""))
        src = it.findtext("source") or ""
        try: d = dt.datetime.strptime(pub[5:16], "%d %b %Y").date().isoformat()
        except Exception: continue
        if EXCLUDE and any(w in (title + " " + desc + " " + src).lower() for w in EXCLUDE): continue
        out.append(dict(d=d, title=title, desc=desc, src=src, url=link))
    return out

def fetch_news_member(m, bank_kw):
    items = []
    names = m["aliases"] if m["kind"] != "cb" else [m["name"]]
    for mo in MONTHS:
        y, mm = ym(mo); a = dt.date(y, mm, 1); b = (a + dt.timedelta(days=32)).replace(day=1)
        if a > TODAY: break
        ttl = None if b < TODAY.replace(day=1) else 0.25     # past months cached for good, recent ones refreshed
        q = f'"{names[0]}" {bank_kw} after:{a.isoformat()} before:{b.isoformat()}'
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
        try: res = cached(f"gn_{m['id']}_{mo}", lambda: news_rss(url), ttl)
        except Exception: continue
        for r in res:
            sur = m["short"].lower()
            if sur not in (r["title"] + " " + r["desc"]).lower(): continue
            txt = r["title"] + ". " + r["desc"]
            items.append(dict(d=r["d"], kind="news", title=r["title"], text=txt, quote=r["title"], src=r["src"], url=r["url"], snippet=bool(r["desc"] and r["desc"][:30] != r["title"][:30])))
        time.sleep(.15)
    # latest month supplement from Bing
    try:
        url = "https://www.bing.com/news/search?" + urllib.parse.urlencode({"q": f'"{names[0]}" {bank_kw}', "format": "rss"})
        for r in news_rss(url):
            if m["short"].lower() in r["title"].lower(): items.append(dict(d=r["d"], kind="news", title=r["title"], text=r["title"] + ". " + r["desc"], quote=r["title"], src="Bing/" + r["src"], url=r["url"], snippet=bool(r["desc"])))
    except Exception: pass
    uniq = {}
    for i in items: uniq[(i["d"], i["title"].lower())] = i
    return list(uniq.values())

def live_voters(members):
    """Official statements: who actually voted (incl. stand-ins)."""
    try:
        page = http("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm").decode("utf-8", "ignore")
    except Exception as e:
        log("FOMC statements (voters)", "FAIL", str(e)[:100]); return None
    urls = sorted(set(re.findall(r"/newsevents/pressreleases/monetary(\d{8})a\.htm", page)))
    v = {y: set() for y in range(2022, 2026)}; n = 0
    for d in urls:
        y = int(d[:4])
        if y not in v: continue
        try: txt = cached("stmt_" + d, lambda: strip_html(http(f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{d}a.htm").decode("utf-8", "ignore")))
        except Exception: continue
        mt = re.search(r"Voting for the monetary policy action were(.*?)(?:Voting against|For media inquiries|Implementation Note|$)", txt, re.S)
        ag = re.search(r"Voting against the action was(?:\s|:)?(.*?)(?:For media|Implementation|$)", txt, re.S)
        seg = (mt.group(1) if mt else "") + " " + (ag.group(1) if ag else "")
        for m in members:
            if re.search(r"\b" + m["short"] + r"\b", seg): v[y].add(m["id"])
        n += 1
    log("FOMC statements (voters)", "ok", f"{n} statements parsed")
    return {y: sorted(s) for y, s in v.items()}

def live_macro():
    n = len(MONTHS); mac = {}; ok = True
    def mkidx(): return {m: i for i, m in enumerate(MONTHS)}
    idx = mkidx()
    # BLS v1 (no key): start 2020 so y/y exists from Jan 2022
    try:
        body = json.dumps({"seriesid": ["CES0000000001", "LNS14000000", "CUUR0000SA0", "CUUR0000SA0L1E"], "startyear": "2020", "endyear": str(TODAY.year)}).encode()
        js = json.loads(http("https://api.bls.gov/publicAPI/v1/timeseries/data/", data=body, headers={"Content-Type": "application/json"}))
        ser = {}
        for s in js["Results"]["series"]:
            d = {}
            for r in s["data"]:
                if r["period"].startswith("M") and r["period"] != "M13":
                    try: d[f'{r["year"]}-{r["period"][1:]}'] = float(str(r["value"]).replace(",", ""))
                    except ValueError: pass  # BLS uses "-" for months not collected (e.g. Oct 2025 shutdown)
            ser[s["seriesID"]] = d
        def yoy(d): return {k: (v / d[f"{int(k[:4])-1}-{k[5:]}"] - 1) * 100 for k, v in d.items() if f"{int(k[:4])-1}-{k[5:]}" in d}
        nfp = ser["CES0000000001"]; chg = {}
        for k, v in nfp.items():
            y, m = ym(k); pk = f"{y}-{m-1:02d}" if m > 1 else f"{y-1}-12"
            if pk in nfp: chg[k] = v - nfp[pk]
        mac["nfp"] = [round(chg[m], 0) if m in chg else None for m in MONTHS]
        mac["nfp3"] = [round(sum(chg[x] for x in MONTHS[max(0, i - 2):i + 1] if x in chg) / max(1, sum(1 for x in MONTHS[max(0, i - 2):i + 1] if x in chg)), 1) if MONTHS[i] in chg else None for i in range(n)]
        mac["unemp"] = [ser["LNS14000000"].get(m) for m in MONTHS]
        c = yoy(ser["CUUR0000SA0"]); cc = yoy(ser["CUUR0000SA0L1E"])
        mac["cpi"] = [round(c[m], 2) if m in c else None for m in MONTHS]; mac["corecpi"] = [round(cc[m], 2) if m in cc else None for m in MONTHS]
        log("BLS API v1", "ok")
    except Exception as e:
        log("BLS API v1", "FAIL", str(e)[:100]); ok = False
    try:
        txt = http("https://apps.bea.gov/national/Release/TXT/NipaDataM.txt").decode("utf-8", "ignore")
        d = {}
        for line in txt.splitlines():
            if line.startswith("DPCCRG,"):  # CSV: SeriesCode,Period,"Value"  (exact code, not DPCCRGM)
                mt = re.match(r'DPCCRG,(\d{4})M(\d{2}),"?(-?[\d.,]+)"?\s*$', line.strip())
                if mt: d[f"{mt.group(1)}-{mt.group(2)}"] = float(mt.group(3).replace(",", ""))
        if not d: raise ValueError("series DPCCRG not found")
        mac["corepce"] = [round((d[m] / d[f"{int(m[:4])-1}-{m[5:]}"] - 1) * 100, 2) if m in d and f"{int(m[:4])-1}-{m[5:]}" in d else None for m in MONTHS]
        log("BEA NipaDataM.txt (DPCCRG)", "ok")
    except Exception as e:
        log("BEA NipaDataM.txt (DPCCRG)", "FAIL", str(e)[:100]); ok = False
    try:
        js = json.loads(http(f"https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate={START_MONTH}-01&endDate={TODAY.isoformat()}"))
        acc = {}
        for r in js["refRates"]: acc.setdefault(r["effectiveDate"][:7], []).append(float(r["percentRate"]))
        mac["ffr"] = [round(sum(acc[m]) / len(acc[m]), 3) if m in acc else None for m in MONTHS]
        mac["_effr_last"] = js["refRates"][0]["percentRate"]
        log("NY Fed EFFR API", "ok")
    except Exception as e:
        log("NY Fed EFFR API", "FAIL", str(e)[:100]); ok = False
    for k in ("nfp", "nfp3", "unemp", "cpi", "corecpi", "corepce", "ffr"): mac.setdefault(k, [None] * n)
    return mac, ok

def live_market(effr):
    codes = "FGHJKMNQUVXZ"; strip = []; d0 = TODAY.replace(day=1)
    for i in range(14):
        d = (d0 + dt.timedelta(days=32 * i)).replace(day=1)
        tk = f"ZQ{codes[d.month-1]}{str(d.year)[2:]}.CBT"
        try:
            js = json.loads(http(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}?range=5d&interval=1d"))
            closes = [c for c in js["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c is not None]
            strip.append(dict(month=d.strftime("%Y-%m"), rate=round(100 - closes[-1], 3)))
        except Exception: pass
        time.sleep(.3)
    if not strip: log("Yahoo ZQ futures", "FAIL", "no contracts returned"); return None
    log("Yahoo ZQ futures", "ok", f"{len(strip)} contracts")
    rate = {s["month"]: s["rate"] for s in strip}; out = []; prev = effr
    for d in fomc_dates():
        dd = d2date(d); mo = d[:7]
        if mo not in rate: continue
        N = (dd.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dd.replace(day=1); N = N.days
        before = dd.day; after = N - before
        post = (rate[mo] * N - prev * before) / after if after > 3 else rate.get((dd + dt.timedelta(days=32)).strftime("%Y-%m"), prev)
        out.append(dict(date=d, bp=round((post - prev) * 100, 1))); prev = post
    return dict(effr=effr, strip=strip, meetings=out, source="Yahoo ZQ futures (month-average method)")

def fetch_photo(m):
    p = os.path.join(PHOTOS, m["id"] + ".jpg")
    if os.path.exists(p): return "photos/" + m["id"] + ".jpg"
    try:
        js = json.loads(http("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(m["wiki"])))
        src = (js.get("thumbnail") or {}).get("source")
        if not src: return None
        open(p, "wb").write(http(src)); time.sleep(1.2)       # Wikipedia rate-limits: one at a time
        return "photos/" + m["id"] + ".jpg"
    except Exception: return None

# --------------------------------------------------------------------- main
def initials(n):
    parts = [x for x in re.split(r"\W+", n) if x]; return (parts[0][0] + parts[-1][0]).upper() if len(parts) > 1 else n[:2].upper()

def main():
    t0 = time.time()
    fed = FED; all_bank = {"FED": fed, "ECB": other_members("ECB"), "BoE": other_members("BoE"), "BoJ": other_members("BoJ")}
    flat = [m for ms in all_bank.values() for m in ms]
    voters = {y: v for y, v in seed_voters(fed).items()}; voters_src = "SEED (rotation table + tenure) - not verified from statements"
    if DEMO:
        items, seen = demo_everything(flat); macro = demo_macro()
        market = demo_market(round(macro["ffr"][-1], 2)); log("DEMO", "demo", "synthetic text + synthetic macro; scoring code is the real code")
        photos = {}
    else:
        items = {}; seen = {}
        raw = fetch_fed_speeches(fed)
        def work(m):
            kw = {"FED": "Fed", "ECB": "ECB", "BoE": "Bank of England", "BoJ": "Bank of Japan"}[[b for b, ms in all_bank.items() if m in ms][0]]
            r = raw.get(m["id"], []) + fetch_news_member(m, kw)
            return m["id"], finalize_items(r)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            for mid, (its, sn) in ex.map(work, flat): items[mid], seen[mid] = its, sn
        log("News (Google/Bing RSS)", "ok" if any(items.values()) else "FAIL", f"{sum(len(v) for v in items.values())} scored items")
        if not any(items.values()):
            print("No news/speech items could be fetched - keeping the existing data.json untouched.")
            json.dump(LOG, open(os.path.join(HERE, "build_log.json"), "w"), indent=1); sys.exit(2)
        macro, _ = live_macro(); effr = float(macro.pop("_effr_last", 0) or 0)
        market = live_market(effr) or dict(effr=effr, strip=[], meetings=[], source="unavailable")
        lv = live_voters(fed)
        if lv:
            for y, ids in lv.items(): voters[y] = ids
            voters_src = "2022-25: official FOMC statements (voters incl. stand-ins); 2026-28: Fed rotation table (seed)"
        photos = {m["id"]: fetch_photo(m) for m in flat if m["bank"] in ("Board", "NY") or m["kind"] == "pres"}
        log("Wikipedia photos", "ok", f"{sum(1 for v in photos.values() if v)} found")

    banks = {}
    for b, ms in all_bank.items():
        res = build_bank(b, ms, items, {m["id"]: seen.get(m["id"], 0) for m in ms})
        banks[b] = dict(res, members=[dict(id=m["id"], name=m["name"], short=m["short"], role=m["role"], bank=m["bank"], kind=m["kind"],
                                           start=m["start"], end=m["end"], initials=initials(m["name"]), photo=(photos.get(m["id"]) if photos else None),
                                           verify=m["verify"], votes=[y for y in YEARS if m["id"] in voters.get(y, [])] if b == "FED" else []) for m in ms])
    cards = build_cards(fed, items)

    # sanity checks (FED, all members with a line)
    tone = banks["FED"]["tone"]; avg = []
    for i in range(len(MONTHS)):
        vs = [t[i] for t in tone.values() if t[i] is not None]; avg.append(sum(vs) / len(vs) if vs else None)
    ffr = macro["ffr"]
    fwd = [(ffr[i + 3] - ffr[i]) if ffr[i] is not None and i + 3 < len(ffr) and ffr[i + 3] is not None else None for i in range(len(MONTHS))]
    checks = dict(vs_next3m_ffr_change=pearson(avg, fwd), vs_ffr_level=pearson(avg, ffr), vs_core_cpi=pearson(avg, macro["corecpi"]),
                  first=round(avg[0], 1) if avg[0] is not None else None, mid2024=round(avg[MONTHS.index("2024-07")], 1) if avg[MONTHS.index("2024-07")] is not None else None,
                  last=round(next(a for a in reversed(avg) if a is not None), 1))
    # self-test: every example matches its own family
    bad = [p["id"] for p in PHRASES if p["id"] not in score_text(p["example"], "2025-01-01")[2]]
    checks["phrase_selftest_failures"] = bad
    out = dict(
        meta=dict(built=dt.datetime.now().strftime("%Y-%m-%d %H:%M"), market=dt.datetime.now().strftime("%Y-%m-%d %H:%M"), demo=DEMO,
                  as_of=TODAY.isoformat(), params=P, scale_by_bank={b: banks[b]["scale"] for b in banks},
                  voters_source=voters_src, secs=round(time.time() - t0, 1)),
        months=MONTHS, banks=banks, items=items, cards=cards, macro=macro, market=market,
        phrases=[{k: v for k, v in p.items() if k != "rx"} for p in PHRASES], flip_id=FLIP_ID,
        voters={str(y): voters[y] for y in YEARS}, rotation={str(y): ROTATION[y] for y in YEARS},
        checks=checks, log=LOG,
        reference_checks=dict(vs_next3m_ffr_change=.47, vs_ffr_level=-.46, vs_core_cpi=.26, note="from the earlier build on the other PC"),
    )
    path = os.path.join(HERE, "data.json")
    json.dump(out, open(path + ".tmp", "w", encoding="utf-8"), separators=(",", ":")); os.replace(path + ".tmp", path)
    print("wrote", path, f"{os.path.getsize(path)/1e6:.2f} MB", "checks:", checks)

if __name__ == "__main__":
    main()
