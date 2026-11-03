"""Daily stock alerts to your phone via ntfy, sized for Trade Republic (EUR).

Runs the scanner and the news check, keeps what fits your rules in
trader/config.json, and sends messages to the ntfy topic in the NTFY_TOPIC
environment variable. Without NTFY_TOPIC it only prints.

    python alert.py --mode premarket          # day-trading list + growth list
    python alert.py --mode open               # breakouts with stop, target, euros per account
    python alert.py --mode watch --until 13:00   # sell checks every minute until 13:00 New York
    python alert.py --mode growth             # growth list only
    python alert.py --mode test               # one test push
    python alert.py                           # auto: premarket or open by New York time

Prices come in US dollars and are shown in euros too, at the day's EUR/USD
rate. Losses include the order fee (buy + sell). Facts and your own rules,
not advice.
"""
import argparse
import datetime
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "tools")
sys.path.insert(0, TOOLS)
import scan  # noqa: E402  (calendar, New York time, Yahoo helpers)
import tmachine  # noqa: E402  (the time machine for New York and Europe)

CONFIG = os.path.join(HERE, "trader", "config.json")
TODAY = os.path.join(HERE, "trader", "today.json")      # the 15:55 list, for the sell checks
OPENED = os.path.join(HERE, "trader", "opened.flag")    # this run did the 15:55 scan
EXPLAIN = {
    "breakout_20d": "Sie hat so hoch geschlossen wie seit einem Monat nicht mehr.",
    "bull_flag": "Sie ist zuletzt kräftig gestiegen und legt gerade eine Pause ein.",
    "reclaim_ma20": "Sie liegt wieder über ihrem Durchschnittskurs der letzten Wochen.",
    "oversold": "Sie ist in kurzer Zeit stark gefallen.",
    "oversold_bounce": "Sie ist stark gefallen und hat zuletzt wieder zugelegt.",
    "orb": "Sie ist nach Börsenstart über ihren Höchstkurs der ersten 15 Minuten gestiegen.",
}
# Shorter company names, easier to find in the Trade Republic search.
NAME_TAIL = re.compile(r"(,?\s+(Inc\.?|Incorporated|Corporation|Corp\.?|Company|Co\.?|plc|PLC|N\.V\.|Ltd\.?|"
                       r"Limited|Holdings?|Group|New|\(The\)|The))+\s*$")   # keeps AG / SE (Porsche SE)
TREND = ("breakout_20d", "bull_flag", "reclaim_ma20", "orb")
NAME_JUNK = re.compile(r"\s*(-\s*)?(Class [A-Z] )?(Common Stock|Common Shares|Ordinary Shares?|"
                       r"American Depositary Shares?.*|Depositary Shares?.*)\s*$", re.I)
MONTHS = ["Jan", "Feb", "März", "April", "Mai", "Juni", "Juli", "Aug", "Sep", "Okt", "Nov", "Dez"]
MAX_ITEMS = 5
MIN_PRICE_USD = 5.0
RATE = None   # US dollars for one euro, set in main()


# ---------------------------------------------------------------- helpers

def names():
    """{SYMBOL: company name} from trader/universe.txt."""
    out = {}
    try:
        with open(os.path.join(HERE, "trader", "universe.txt"), encoding="utf-8") as fh:
            for line in fh:
                sym, _, name = line.rstrip("\n").partition("|")
                out[sym] = NAME_JUNK.sub("", name).strip(" -,") or sym
    except OSError:
        pass
    return out


def sectors():
    """{TICKER: sector} from trader/tradable.txt (S&P 500 and MidCap 400, all on Trade Republic)."""
    out = {}
    try:
        with open(os.path.join(HERE, "trader", "tradable.txt"), encoding="utf-8") as fh:
            for x in fh:
                if x.strip() and not x.startswith("#"):
                    t, _, sec = x.strip().partition("|")
                    out[t] = sec
    except OSError:
        pass
    return out


def tradable():
    return set(sectors())


BRANCHE = [  # (word in the English sector name, German) - first match wins
    ("medical", "Medizintechnik"), ("energy tech", "Energietechnik"), ("construction", "Bau"),
    ("semicond", "Halbleiter"), ("software", "Software"), ("information tech", "Technologie"),
    ("it service", "IT-Dienste"), ("technolog", "Technologie"), ("e-commerce", "Online-Handel"),
    ("communication", "Kommunikation und Medien"), ("telecom", "Telekommunikation"), ("media", "Medien"),
    ("bank", "Banken"), ("insurance", "Versicherungen"), ("financ", "Finanzen"),
    ("real estate", "Immobilien"), ("health", "Gesundheit"), ("pharma", "Pharma"), ("biotech", "Biotechnologie"),
    ("medical", "Medizintechnik"), ("utilities", "Strom und Versorger"), ("renewable", "Erneuerbare Energie"),
    ("energy", "Energie"), ("oil", "Öl und Gas"), ("materials", "Rohstoffe und Materialien"),
    ("chemi", "Chemie"), ("metal", "Metalle"), ("mining", "Bergbau"), ("automo", "Autos"),
    ("aerospace", "Luftfahrt und Rüstung"), ("defen", "Rüstung"), ("airline", "Fluggesellschaften"),
    ("airport", "Flughafen"), ("logistic", "Logistik"), ("transport", "Transport"),
    ("construction", "Bau"), ("engineering", "Maschinenbau"), ("manufactur", "Industrie"),
    ("industrial", "Industrie"), ("consumer staples", "Alltagsprodukte (Lebensmittel, Getränke, Haushalt)"),
    ("consumer discretionary", "Konsum (Handel, Autos, Freizeit)"), ("consumer goods", "Konsumgüter"),
    ("apparel", "Mode und Sport"), ("clothing", "Mode"), ("sport", "Sportartikel"), ("tourism", "Reisen"),
    ("leisure", "Freizeit und Events"), ("delivery", "Lieferdienst"), ("meal", "Lieferdienst"),
    ("pharmacy", "Online-Apotheke"), ("packag", "Verpackung"), ("waste", "Recycling"),
    ("distribution", "Großhandel"), ("conglomerate", "Mischkonzern"), ("handling", "Maschinenbau"),
]


def branche(sector):
    s = (sector or "").lower()
    return next((de_ for en, de_ in BRANCHE if en in s), "")


def build_universe():
    """Download the US stock list, then keep only stocks Trade Republic offers."""
    run(["scan.py", "universe", "--json"])
    keep = tradable()
    path = os.path.join(HERE, "trader", "universe.txt")
    if not keep:
        print("trader/tradable.txt missing: scanning every US stock", file=sys.stderr)
        return
    with open(path, encoding="utf-8") as fh:
        rows = [x for x in fh if x.split("|", 1)[0] in keep]
    if len(rows) < 0.5 * len(keep):
        print("only %d of the %d Trade Republic stocks are in the Nasdaq lists" % (len(rows), len(keep)),
              file=sys.stderr)
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(rows)
    print("universe limited to Trade Republic stocks: %d" % len(rows), file=sys.stderr)


def short(name):
    """'Coca-Cola Company (The)' -> 'Coca-Cola', 'Walmart Inc.' -> 'Walmart'."""
    s = re.sub(r"\s*\((?!The\)).*?\)", "", name)                       # '(Ireland)'
    s = re.sub(r"\s+(Class [A-Z]\b|Common|Ordinary|American Depositary|Depositary).*$", "", s, flags=re.I)
    s = NAME_TAIL.sub("", s.strip(" ,-")).strip(" ,-")
    return s or name


def head(sym, name_of):
    return "%s (%s)" % (short(name_of.get(sym, sym)), sym)


def open_vienna():
    """Today's 9:30 New York opening bell in Austrian time."""
    day = scan.to_eastern(datetime.datetime.now(datetime.timezone.utc)).date()
    return vienna(datetime.datetime.combine(day, datetime.time(9, 30)))


def _last_sunday(year, month):
    d = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)


def vienna(et):
    """Naive New York time -> 'HH:MM' in Austria (EU summer time: last Sunday
    of March to last Sunday of October, 01:00 UTC)."""
    utc = scan.eastern_to_utc(et)
    start = datetime.datetime.combine(_last_sunday(utc.year, 3), datetime.time(1, 0))
    end = datetime.datetime.combine(_last_sunday(utc.year, 10), datetime.time(1, 0))
    return (utc + datetime.timedelta(hours=2 if start <= utc < end else 1)).strftime("%H:%M")


def eur(usd):
    return usd / RATE


def de(x):
    """German number: 1234.5 -> '1.234,50'"""
    return "{:,.2f}".format(x).replace(",", "X").replace(".", ",").replace("X", ".")


def pct(x):
    """+4.8 -> '+4,8 %'"""
    return ("%+.1f %%" % x).replace(".", ",")


EUR_ONLY = False   # True for European stocks: prices are euros already
CLOCK = None       # naive local datetime -> 'HH:MM' in Austria; None = New York time (vienna)


def money(usd):
    """'271,02 € (304,00 $)': euros first, as in Trade Republic."""
    if EUR_ONLY:
        return "%s €" % de(usd)
    return "%s € (%s $)" % (de(eur(usd)), de(usd))


def clock(dt):
    return CLOCK(dt) if CLOCK else vienna(dt)


def signed(x):
    """+12,30 € / −4,10 €"""
    return ("+" if x >= 0 else "−") + de(abs(x)) + " €"


def eur_rate():
    """US dollars per euro from Yahoo (EURUSD=X)."""
    doc = json.loads(scan.yahoo_get(scan.chart_path("EURUSD=X", "5d", "1d")))
    rate = doc["chart"]["result"][0]["meta"]["regularMarketPrice"]
    if not 0.5 < rate < 2.0:
        raise ValueError("odd EUR/USD rate %s" % rate)
    return float(rate)


def run(args, timeout=900):
    """Run a tool in tools/ and return its stdout parsed as JSON (or None)."""
    cmd = [sys.executable, os.path.join(TOOLS, args[0])] + args[1:]
    p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=timeout)
    if p.stderr.strip():
        print(p.stderr.strip()[-1500:], file=sys.stderr)
    try:
        return json.loads(p.stdout)
    except ValueError:
        print(p.stdout.strip()[-1500:])
        return None


def load_config():
    with open(CONFIG, encoding="utf-8-sig") as fh:   # -sig: Windows editors may add a BOM
        return json.load(fh)


def price_ok(usd, cfg):
    top = cfg.get("max_share_price_eur")   # null in the config = no upper limit
    return usd >= MIN_PRICE_USD and cfg.get("min_share_price_eur", 6) <= eur(usd) and (not top or eur(usd) <= top)


def accounts(cfg):
    """The money on each Trade Republic account."""
    return [row[0] for row in limits(cfg)]


def _env_accounts():
    """The ACCOUNTS secret: 'money:max loss per trade:max loss per day' per account, comma separated,
    e.g. '2000:20:100,8000:40:200'. Kept out of the public files on purpose."""
    out = []
    for part in os.environ.get("ACCOUNTS", "").replace(";", ",").split(","):
        bits = [b.strip().replace(".", "") for b in part.split(":") if b.strip()]
        try:
            nums = [float(b.replace(",", ".")) for b in bits]
        except ValueError:
            continue
        if nums and nums[0] > 0:
            out.append((nums[0], nums[1] if len(nums) > 1 else None, nums[2] if len(nums) > 2 else None))
    return out


def konto(acc):
    """2000 -> '2.000-€-Konto'"""
    return "%s-€-Konto" % "{:,}".format(int(acc)).replace(",", ".")


def limits(cfg):
    """[(account, max loss per trade, max loss per day)] - one row per account, from the
    ACCOUNTS secret, else the single budget in the config."""
    trade, day = cfg.get("max_loss_per_trade_eur", 20), cfg.get("max_daily_loss_eur", 100)
    rows = _env_accounts()
    if not rows:
        return [(cfg.get("budget_eur", 1000), trade, day)]
    return [(a, t if t else trade, d if d else day) for a, t, d in rows]


def limits_text(cfg, which=1):
    """'20 € (2.000-€-Konto) / 40 € (8.000-€-Konto)' for the per-trade (1) or per-day (2) limit."""
    return " / ".join("%g € (%s)" % (row[which], konto(row[0])) for row in limits(cfg))


def stakes(price, stop, target, cfg):
    """How many euros to put in, per account. The loss at the stop plus one fee to
    buy and one to sell stays within that account's max loss per trade, and one
    stock gets at most max_position_pct of the account. Bought by amount, so the
    share price does not matter. -> [{"account", "amount", "loss", "gain"}]"""
    fees = 2 * cfg.get("fee_per_order_eur", 1)
    down, up = (price - stop) / price, (target - price) / price
    out = []
    for acc, max_loss, _ in limits(cfg):
        room = max_loss - fees
        amount = 0 if down <= 0 or room <= 0 else math.floor(min(room / down,
                                                                 acc * cfg.get("max_position_pct", 75) / 100))
        out.append({"account": acc, "amount": amount, "loss": round(amount * down + fees, 2),
                    "gain": round(amount * up - fees, 2)})
    return out


def plan_rows(hits, cfg):
    """Breakouts -> plans with stop (range midpoint), target (2R) and the euros per account.
    Dropped when even the biggest account gains less at the target than it loses at the stop."""
    rows = []
    for h in hits:
        if not price_ok(h["price"], cfg) or h["symbol"] in cfg.get("blocked", []):
            continue
        stop = math.floor((h["or_high"] + h["or_low"]) / 2 * 100) / 100   # range midpoint, rounded down
        target = math.ceil((h["price"] + cfg.get("target_r", 2) * (h["price"] - stop)) * 100) / 100
        st = stakes(h["price"], stop, target, cfg)
        big = max(st, key=lambda s: s["amount"])
        if big["amount"] < 50 or big["gain"] < big["loss"]:
            continue   # too little money in play, or the fees eat the edge
        rows.append(dict(h, stop=stop, target=target, stakes=st, max_loss_eur=max(s["loss"] for s in st),
                         gain_eur=big["gain"]))
    rows.sort(key=lambda r: -r["gain_eur"])   # the biggest possible gain within your loss limit first
    return rows


def results(p, last, cfg):
    """'−8,10 € (2.000-€-Konto) / −19,80 € (8.000-€-Konto)' at the price `last`."""
    fees = 2 * cfg.get("fee_per_order_eur", 1)
    return " / ".join("%s (%s)" % (signed(s["amount"] * (last / p["price"] - 1) - fees), konto(s["account"]))
                      for s in p["stakes"])


def news_for(tickers, name_of=None):
    """{ticker: {"pct", "price", "news": [title]}} from the news tool."""
    out = {t: {"pct": None, "price": None, "news": []} for t in tickers}
    if not tickers:
        return out
    args = ["news.py", "--tickers"] + tickers + ["--hours", "20", "--all", "--dry-run", "--json"]
    if not os.environ.get("SEC_USER_AGENT"):
        args.append("--no-sec")
    doc = run(args, timeout=300) or {}
    for m in doc.get("moves", []):
        if m.get("ticker") in out:
            out[m["ticker"]]["pct"] = m.get("pct")
            out[m["ticker"]]["price"] = m.get("price")
    name_of = name_of or names()
    for t in out:
        out[t]["text"] = ""
    for it in doc.get("items", []):
        title = it.get("title", "")
        for t in it.get("touches") or [it.get("ticker")]:
            if t not in out or len(out[t]["news"]) >= 3:
                continue
            # Only headlines that name the company or its ticker: Yahoo's feed mixes in others.
            word = (name_of.get(t, "").split() or [""])[0].strip(",.").lower()
            base = t.split(".")[0] if re.search(r"\.[A-Z]{2,3}$", t) else t   # SAP.DE -> SAP
            if not (re.search(r"\b%s\b" % re.escape(base), title) or (len(word) >= 4 and word in title.lower())):
                continue
            flag = "! " if it.get("important") else ""
            out[t]["news"].append(flag + title[:90])
            out[t]["text"] += " " + title + " " + (it.get("summary") or "")
    return out


REASONS = [   # (pattern in English headlines, German reason) - the first ones are the most specific
    (r"\bbeats?\b|\btops? (estimates|expectations|forecasts)|better[- ]than[- ]expected|record (quarter|revenue|"
     r"profit|sales)", "Zahlen besser als erwartet"),
    (r"\bmiss(es|ed)? (estimates|expectations)|falls? short|worse[- ]than[- ]expected", "Zahlen schlechter als erwartet"),
    (r"(raises?|lifts?|boosts?|hikes?) (its |full[- ]year |annual )?(guidance|outlook|forecast)", "Prognose erhöht"),
    (r"(cuts?|lowers?|slashes|trims?) (its |full[- ]year |annual )?(guidance|outlook|forecast)", "Prognose gesenkt"),
    (r"\bupgrade", "Analysten stufen die Aktie hoch"),
    (r"\bdowngrade", "Analysten stufen die Aktie herunter"),
    (r"(raises?|lifts?|boosts?|hikes?) (its |the )?price target|price target (raised|hiked|lifted)", "Kursziel erhöht"),
    (r"(cuts?|lowers?|trims?) (its |the )?price target|price target (cut|lowered)", "Kursziel gesenkt"),
    (r"earnings|results|\bquarter|\bq[1-4]\b", "neue Quartalszahlen"),
    (r"acqui|merger|takeover|buyout|\bto buy\b|\bbid for\b", "Übernahme oder Fusion"),
    (r"\bfda\b|approval|\bapproved\b|clearance", "Zulassung erhalten"),
    (r"(wins?|awarded|secures?|lands?) (a |an |the )?(\$?[\d.]+ ?(million|billion|bn|m) )?(contract|order|deal)",
     "neuer Auftrag"),
    (r"buyback|repurchase", "Aktienrückkauf"),
    (r"dividend", "Dividende"),
    (r"lawsuit|\bsued\b|\bsues\b|investigation|antitrust|\bfined?\b", "Rechtsstreit oder Ermittlungen"),
    (r"\bceo\b|chief executive|steps? down|resign", "Wechsel an der Spitze"),
    (r"\bai\b|artificial intelligence|data[- ]cent", "Thema KI und Rechenzentren"),
    (r"tariff|trade war|sanction", "Zölle und Handelsstreit"),
    (r"\bfed\b|interest rate|inflation|rate cut", "Zinsen und Wirtschaftslage"),
]


def reasons(text):
    """German reasons from the headlines and their summaries, at most three."""
    t = (text or "").lower()
    found = []
    for pat, why in REASONS:
        if re.search(pat, t) and why not in found:
            if why == "neue Quartalszahlen" and any(w.startswith(("Zahlen", "Prognose")) for w in found):
                continue   # already said more precisely
            found.append(why)
    return found[:3]


FIRM = {}


def firm_info(sym, name_of, full=None):
    """First sentence about the company from the German Wikipedia, or ''."""
    if sym in FIRM:
        return FIRM[sym]
    name = name_of.get(sym, "")
    tries = []
    first_word = short(name).split()[0] if short(name) else ""
    for n in (full, NAME_JUNK.sub("", name).strip(" -,"), short(name), short(name) + " AG", short(name) + " Inc.",
              first_word if len(first_word) >= 4 else "", first_word.title() if first_word.isupper() else ""):
        if n and n not in tries:
            tries.append(n)
    text = ""
    for n in tries:
        url = "https://de.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(n.replace(" ", "_"))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "trading-alarm (personal use)"})
            with urllib.request.urlopen(req, timeout=15) as r:
                doc = json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
        if doc.get("type") == "standard" and doc.get("extract"):
            clean = re.sub(r"\s*\[[^\]]*\]", "", doc["extract"].replace("\n", " ").replace("\xad", ""))  # [ˈlaut]
            parts = re.split(r"\. (?=[A-ZÄÖÜ])", clean)
            first = parts[0]
            while len(first) < 40 and len(parts) > 1:      # 'Die X Inc. Die …' style splits
                parts = parts[1:]
                first += ". " + parts[0]
            first = first.rstrip(".")
            text = first + "." if len(first) <= 220 else first[:220].rsplit(" ", 1)[0] + " …"
            break
    FIRM[sym] = text
    return text


def about_lines(sym, i, name_of, full=None):
    """'Die Firma: …' and 'Warum sie sich bewegt: …' plus the headlines."""
    out = []
    firm = firm_info(sym, name_of, full)
    if firm:
        out.append("   Die Firma: " + firm)
    news = i.get("news") or []
    why = reasons(i.get("text"))
    if why:
        out.append("   Warum sie sich bewegt (laut Schlagzeilen): %s." % ", ".join(why))
    elif not news:
        out.append("   Warum sie sich bewegt: Dazu habe ich heute keine Schlagzeile gefunden.")
    else:
        out.append("   Warum sie sich bewegt: Aus den Schlagzeilen ist kein klarer Grund erkennbar.")
    if news and news[0].startswith("! "):
        out.append("   Achtung: Die Firma hat gerade eine wichtige Pflichtmeldung veröffentlicht (z. B. "
                   "Quartalszahlen oder ein Chefwechsel). Der Kurs kann dadurch stark springen.")
    if news:
        out.append("   Schlagzeilen (Englisch): " + " · ".join("„%s“" % n.lstrip("! ") for n in news[:2]))
    return out




# ---------------------------------------------------------------- the daily lists

def premarket(cfg):
    build_universe()
    doc = run(["scan.py", "daily", "--json"])
    if not doc:
        return ("Vorbörse: Scan fehlgeschlagen",
                "Heute sind leider keine Kursdaten angekommen, deshalb gibt es keine Vorbörse-Liste. "
                "Die Ausbrüche um ca. 15:55 Uhr kommen trotzdem, wenn die Daten bis dahin da sind.")
    c = doc.get("counts", {})
    print("daily counts: %s" % json.dumps(c), file=sys.stderr)   # diagnostics for the run log
    hits = [h for h in doc.get("hits", []) if price_ok(h["price"], cfg)
            and h["symbol"] not in cfg.get("blocked", [])]
    pick = pick_five(hits)
    info = news_for([h["symbol"] for h in pick])
    lines = ["Hallo! Die Börse in New York öffnet heute um %s Uhr." % open_vienna(),
             "",
             "Diese US-Aktien sind mir am letzten Börsentag aufgefallen. Noch nicht kaufen, nur anschauen. "
             "Um ca. 15:55 Uhr schreibe ich dir, ob eine davon wirklich steigt.",
             "",
             "(Ich habe %s große US-Firmen geprüft, die es bei Trade Republic gibt.)" % c.get(
                 "fetched", c.get("scanned", "?"))]
    name_of = names()
    sec = sectors()
    for k, h in enumerate(pick, 1):
        i = info[h["symbol"]]
        b = branche(sec.get(h["symbol"]))
        lines.append("")
        lines.append("%d. %s%s" % (k, head(h["symbol"], name_of), " – " + b if b else ""))
        lines.append("   1 Aktie kostet: %s%s" % (money(h["price"]),
                                                  "" if i["pct"] is None else ", zuletzt " + pct(i["pct"])))
        lines.append("   Was auffällt: " + " ".join(EXPLAIN.get(s, s) for s in h["setups"]))
        lines += about_lines(h["symbol"], i, name_of)
    if not pick:
        lines += ["", "Heute ist keine Aktie aufgefallen, die zu deinen Regeln passt."]
    lines += ["", "Das sind Fakten aus den Kursdaten, keine Empfehlung. Du entscheidest selbst."]
    return "Vorbörse: %d Aktien für heute im Blick" % len(pick), "\n".join(lines)


def pick_five(hits):
    """Up to 3 rising patterns and 2 sharp falls, filled up to MAX_ITEMS from the rest."""
    trend = [h for h in hits if set(h["setups"]) & set(TREND)]
    weak = [h for h in hits if not set(h["setups"]) & set(TREND)]
    pick = trend[:3] + weak[:2]
    rest = [h for h in trend[3:] + weak[2:]]
    return (pick + rest)[:MAX_ITEMS]


def growth_list(cfg):
    """10 stocks with long, steady profit growth (SEC filings)."""
    doc = run(["../growth.py", "--top", "80"], timeout=600)
    if not doc or not doc.get("top"):
        return ("Wachstums-Liste: keine Daten",
                "Die Firmenzahlen der US-Börsenaufsicht sind heute nicht angekommen. Morgen probiere ich es wieder.")
    keep = tradable()
    if keep:   # only stocks Trade Republic offers
        doc["top"] = [r for r in doc["top"] if r["symbol"] in keep][:25]
    info = news_for([r["symbol"] for r in doc["top"]])
    top = [r for r in doc["top"] if info[r["symbol"]]["price"] is not None
           and price_ok(info[r["symbol"]]["price"], cfg)
           and r["symbol"] not in cfg.get("blocked", [])][:10]
    if not top:
        return ("Wachstums-Liste: keine passt",
                "Heute passt keine Wachstums-Firma in deine Preisspanne von %g bis %g € pro Aktie." % (
                    cfg.get("min_share_price_eur", 6), cfg.get("max_share_price_eur", 500)))
    name_of = names()
    month_now = datetime.date.today().strftime("%Y-%m")
    fee = cfg.get("fee_per_order_eur", 1)
    lines = ["Diese %d Firmen haben ihren Gewinn über 6 Jahre stark und stetig gesteigert. Die Zahlen stammen "
             "aus den Pflichtmeldungen an die US-Börsenaufsicht (SEC)." % len(top),
             "",
             "Das ist für längeres Halten gedacht (Monate bis Jahre), nicht fürs schnelle Day Trading.",
             "",
             "(%s von %s Firmen haben das geschafft, hier die stärksten.)" % (doc["matched"], doc["screened"])]
    sec = sectors()
    for k, r in enumerate(top, 1):
        i = info[r["symbol"]]
        b = branche(sec.get(r["symbol"]))
        lines.append("")
        lines.append("%d. %s (%s)%s" % (k, short(name_of.get(r["symbol"], r["sec_name"].title())), r["symbol"],
                                        " – " + b if b else ""))
        lines.append("   1 Aktie kostet: %s%s" % (money(i["price"]),
                                         "" if i["pct"] is None else ", zuletzt " + pct(i["pct"])))
        lines.append("   Der Gewinn wächst um %.0f %% pro Jahr, der Umsatz um %.0f %%." % (
            r["ni_cagr"], r["rev_cagr"]))
        lines.append("   Von 100 € Umsatz bleiben heute %.0f € Gewinn (vor 5 Jahren %.0f €)." % (
            r["margin_last"], r["margin_first"]))
        if r.get("quarter"):
            y, m = r["next_report"].split("-")
            due = "bald" if r["next_report"] <= month_now else "ca. im %s %s" % (MONTHS[int(m) - 1], y)
            if r.get("q_loss"):
                q = "Im letzten Quartal gab es einen Verlust."
            elif r.get("q_growth") is not None:
                q = "Im letzten Quartal war der Gewinn %s als ein Jahr davor." % (
                    "%.0f %% höher" % r["q_growth"] if r["q_growth"] >= 0 else "%.0f %% niedriger" % -r["q_growth"])
            else:
                q = ""
            lines.append("   %s Neue Zahlen kommen %s." % (q, due) if q else "   Neue Zahlen kommen %s." % due)
            if r.get("warning"):
                lines.append("   Vorsicht: Das Wachstum ist zuletzt ins Stocken geraten.")
        lines += about_lines(r["symbol"], i, name_of)
    lines += ["",
              "Wie lange halten? Schau nach den nächsten Quartalszahlen nach, ob der Gewinn weiter wächst. "
              "Wenn nicht, ist der Grund für die Aktie weg.",
              "",
              "Gute Zahlen in der Vergangenheit sind keine Garantie für die Zukunft. Keine Empfehlung. "
              "Gebühr bei Trade Republic: %g € pro Kauf und %g € pro Verkauf." % (fee, fee)]
    return "Wachstums-Liste: %d Firmen für langfristig" % len(top), "\n".join(lines)


PELOSI_SEEN = os.path.join(HERE, "trader", "state", "pelosi-seen.json")
TX = {"P": "Gekauft", "S": "Verkauft", "S (partial)": "Teilweise verkauft", "E": "Getauscht"}
OWNER = {"SP": "über ihren Ehemann", "JT": "über ein gemeinsames Konto", "DC": "über ihr Kind", "": "selbst"}


def usd_range(text):
    """'$1,000,001 - $5,000,000' -> '1 bis 5 Mio. $'"""
    nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", text or "")]
    if not nums:
        return text or "?"
    nums = [n // 1000 * 1000 if n % 1000 == 1 else n for n in nums]   # 1,000,001 -> 1,000,000
    if nums[-1] >= 1000000:
        f = lambda n: ("%g" % (n / 1e6)).replace(".", ",")  # noqa: E731
        return "%s Mio. $" % " bis ".join(f(n) for n in nums)
    return "%s $" % " bis ".join("{:,}".format(n).replace(",", ".") for n in nums)


def pelosi():
    """[(title, message)] for Nancy Pelosi's stock reports (STOCK Act) not pushed before."""
    rows = run(["ptr.py", "index", "--member", "pelosi", "--json"], timeout=300) or []
    state = pelosi_state()
    first = not state["seen"]
    seen, parsed, tickers = set(state["seen"]), set(state["parsed"]), state["tickers"]
    week_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    out = []
    for r in sorted(rows, key=lambda x: x["filed"]):
        push = r["doc_id"] not in seen and (not first or r["filed"] >= week_ago)
        if not push and r["doc_id"] in parsed:
            continue
        rep = run(["ptr.py", "fetch", r["doc_id"], "--year", str(r["year"]), "--filed", r["filed"], "--json",
                   "--pdf-dir", os.path.join("trader", "state", "ptr-pdfs")], timeout=300)
        if rep and rep.get("rows"):
            parsed.add(r["doc_id"])
            for x in rep["rows"]:     # remember the latest trade per ticker, for the day-trading checks
                if x.get("ticker") and x.get("date") and x["date"] >= tickers.get(x["ticker"], {}).get("date", ""):
                    tickers[x["ticker"]] = {"date": x["date"], "type": x.get("type")}
        if not push:
            continue
        filed = datetime.date.fromisoformat(r["filed"]).strftime("%d.%m.%Y")
        lines = ["Nancy Pelosi (US-Politikerin) muss ihre Aktiengeschäfte öffentlich melden. Am %s ist eine "
                 "neue Meldung erschienen." % filed]
        groups = {}
        for x in (rep or {}).get("rows", []):
            what = short(x.get("asset") or "?")
            if x.get("ticker"):
                what += " (%s)" % x["ticker"]
            bits = []
            o = x.get("option")
            if o:
                bits.append("%s, %d Kontrakte, Basispreis %s $, läuft bis %s" % (
                    "Kaufoption (Call)" if o["kind"].startswith("call") else "Verkaufsoption (Put)",
                    o["contracts"], de(o["strike"]),
                    datetime.date.fromisoformat(o["expires"]).strftime("%d.%m.%Y") if o.get("expires") else "?"))
            elif x.get("shares"):
                bits.append("%s Aktien" % "{:,}".format(x["shares"]).replace(",", "."))
            bits.append("Wert %s" % usd_range(x.get("amount")))
            if x.get("date"):
                bits.append("gehandelt am %s" % datetime.date.fromisoformat(x["date"]).strftime("%d.%m.%Y"))
            bits.append(OWNER.get(x.get("owner") or "", x.get("owner_name") or ""))
            groups.setdefault(TX.get(x.get("type"), x.get("type") or "?"), []).append(
                "• %s: %s" % (what, ", ".join(bits)))
        for kind, items in groups.items():
            lines += ["", kind + ":"] + items
        if not groups:
            lines += ["", "Die einzelnen Geschäfte konnte ich diesmal nicht auslesen. Du findest sie im Original."]
        lines += ["",
                  "Wichtig: Gemeldet wird bis zu 45 Tage nach dem Handel. Der Kurs kann sich seitdem schon stark "
                  "bewegt haben. Das ist keine Empfehlung, es nachzumachen.",
                  "", "Original: " + r["url"]]
        out.append(("Pelosi: neue Aktien-Meldung vom %s" % filed, "\n".join(lines)))
        seen.add(r["doc_id"])
    seen.update(r["doc_id"] for r in rows)   # older reports count as known
    os.makedirs(os.path.dirname(PELOSI_SEEN), exist_ok=True)
    with open(PELOSI_SEEN, "w", encoding="utf-8") as fh:
        json.dump({"seen": sorted(seen), "parsed": sorted(parsed), "tickers": tickers}, fh)
    return out


def pelosi_state():
    try:
        with open(PELOSI_SEEN, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        doc = {}
    if isinstance(doc, list):       # the first version kept only the seen ids
        doc = {"seen": doc}
    return {"seen": doc.get("seen", []), "parsed": doc.get("parsed", []), "tickers": doc.get("tickers", {})}


def pelosi_note(sym):
    """'kein Handel gemeldet' or 'gekauft am 15.09.2026' for a ticker."""
    t = pelosi_state()["tickers"].get(sym)
    if not t:
        return "kein Handel gemeldet"
    return "%s am %s" % (TX.get(t.get("type"), "gehandelt").lower(),
                         datetime.date.fromisoformat(t["date"]).strftime("%d.%m.%Y"))


def opening(cfg):
    build_universe()  # each cloud run starts empty
    uni = {"count": len(names())}
    doc = run(["scan.py", "orb", "--json", "--max-confirm", "150"])
    if not doc:
        return ("Eröffnung: kein Scan",
                "Heute sind leider keine Kursdaten für die Ausbrüche angekommen. Ich kann dir deshalb "
                "keine Aktie nennen. Kein Trade ist heute die sichere Wahl.")
    # Diagnostics for the run log: how much of the market the data source answered for.
    failed = doc.get("failed") or {}
    print("universe: %s" % ((uni or {}).get("count")), file=sys.stderr)
    print("orb counts: %s" % json.dumps(doc.get("counts", {})), file=sys.stderr)
    print("orb failed: %d, e.g. %s" % (len(failed), json.dumps(dict(list(failed.items())[:5]))
                                        if isinstance(failed, dict) else str(failed)[:300]), file=sys.stderr)
    coverage = doc.get("counts", {}).get("scanned", 0)
    c = doc.get("counts", {})
    rows = plan_rows(doc.get("hits", []), cfg)
    day = scan.to_eastern(datetime.datetime.now(datetime.timezone.utc)).date().isoformat()
    flat = scan.parse_hhmm(cfg.get("flat_by", "15:55"), "flat_by")

    def replay(r):
        start = datetime.datetime.strptime(r["bar_start"], "%H:%M")
        bar = (start.hour * 60 + start.minute - (9 * 60 + 30)) // 5
        return tmachine.replay(r["symbol"].replace(".", "-"), day, r["price"], r["stop"], r["target"], bar,
                               "us", until=flat.hour * 60 + flat.minute)
    rows = rank_by_probe(rows, replay, cfg)
    info = news_for([r["symbol"] for r in rows])
    name_of = names()
    with open(TODAY, "w", encoding="utf-8") as fh:
        json.dump({"date": day, "rate": RATE, "plays": [
            {k: r[k] for k in ("symbol", "price", "stop", "target", "stakes", "max_loss_eur", "at", "bar_start")}
            for r in rows]}, fh, indent=2)
    with open(OPENED, "w") as fh:
        fh.write(day)
    lines = ["Die Börse in New York ist seit %s Uhr offen. Hier sind Aktien, die gerade stark steigen: "
             "höher als in der ersten Viertelstunde des Tages. Manchmal geht so ein Anstieg weiter, manchmal "
             "nicht." % open_vienna(),
             ""]
    if rows:
        lines += ["Alles ist so ausgerechnet, dass du pro Aktie höchstens %s verlieren kannst, Gebühren "
                  "schon dabei." % limits_text(cfg), ""]
    lines += ["(%s von %s Firmen sind über ihre erste Viertelstunde gestiegen, %d %s zu deinen Regeln.)" % (
                 c.get("hits", len(doc.get("hits", []))), c.get("scanned", "?"), len(rows),
                 "passt" if len(rows) == 1 else "passen")]
    if coverage < 450:   # about half of the ~900 Trade Republic stocks
        lines += ["", "Achtung: Heute kamen nur Kursdaten für %s Aktien an. Es kann also gute Kandidaten "
                      "geben, die hier fehlen." % coverage]
    if not rows:
        lines += ["", "Heute passt keine Aktie zu deinen Regeln (höchstens %s Verlust pro Trade). Das ist "
                      "völlig in Ordnung: Kein Trade ist besser als ein schlechter." % limits_text(cfg)]
    sec = sectors()
    lines += best_line(rows, name_of)
    for k, r in enumerate(rows, 1):
        brk = datetime.datetime.combine(datetime.date.fromisoformat(day),
                                        datetime.datetime.strptime(r["bar_start"], "%H:%M").time())
        lines += play_lines(k, r, info[r["symbol"]], head(r["symbol"], name_of), branche(sec.get(r["symbol"])),
                            vienna(brk + datetime.timedelta(minutes=5)), r.get("tm"), cfg, name_of)
    if rows:
        lines += howto_lines(cfg, vienna(datetime.datetime.combine(datetime.date.fromisoformat(day), flat)))
        lines += ["", "Die €-Preise sind umgerechnet (1 € = %s $). In der App gilt der €-Kurs von Trade Republic."
                  % ("%.2f" % RATE).replace(".", ",")]
    return open_title("Eröffnung", rows, name_of), "\n".join(lines)


def rank_by_probe(rows, replay, cfg, candidates=10):
    """Run the time machine on the best `candidates` by possible gain, then sort them by the
    average result in euros per trade (average R x the euros at risk, minus both fees)."""
    fees = 2 * cfg.get("fee_per_order_eur", 1)
    cand = rows[:candidates]
    for r in cand:
        try:
            r["tm"] = replay(r)
        except Exception as e:
            print("time machine %s: %s" % (r["symbol"], e), file=sys.stderr)
            r["tm"] = None
        down = (r["price"] - r["stop"]) / r["price"]
        r["exp"] = ([round(r["tm"]["avg_R"] * s["amount"] * down - fees, 2) for s in r["stakes"]]
                    if r["tm"] else None)
        r["exp_eur"] = max(r["exp"]) if r["exp"] else None   # the best account, for sorting and the star
    cand.sort(key=lambda r: -(r["exp_eur"] if r["exp_eur"] is not None else -1e9))
    return cand[:MAX_ITEMS]


def per_account(values, accs):
    """[-1.2, 0.8] -> '−1,20 € (2.000-€-Konto) / +0,80 € (8.000-€-Konto)'"""
    return " / ".join("%s (%s)" % (signed(v), konto(a)) for v, a in zip(values, accs))


def best_line(rows, name_of):
    """The headline: which one paid off best in the probe, or that none did."""
    if not rows:
        return []
    best = rows[0]
    if best.get("exp_eur") is None:
        return ["", "Für die Probe fehlen heute Kursdaten. Die Reihenfolge ist nach dem möglichen Gewinn."]
    if best["exp_eur"] <= 0:
        return ["", "Ehrlich gesagt: Laut Probe bringt heute keine davon im Schnitt Gewinn. Am wenigsten "
                    "schlecht ist %s. Kein Trade ist auch eine gute Entscheidung." % head(best["symbol"], name_of)]
    return ["", "★ Am meisten zahlt sich laut Probe %s aus: im Schnitt %s pro Trade. Die Liste ist danach "
                "sortiert, die beste zuerst." % (head(best["symbol"], name_of),
                                                 per_account(best["exp"], [s["account"] for s in best["stakes"]]))]


def open_title(prefix, rows, name_of):
    if not rows:
        return "%s: heute keine passende Aktie" % prefix
    best = rows[0]
    star = (", am besten %s" % short(name_of.get(best["symbol"], best["symbol"]))
            if best.get("exp_eur") is not None and best["exp_eur"] > 0 else "")
    return "%s: %d %s%s" % (prefix, len(rows), "Aktie" if len(rows) == 1 else "Aktien", star)


def play_lines(k, r, i, name, sector, when, tmr, cfg, name_of=None):
    """The block for one breakout in the opening message (US and Europe)."""
    exp = r.get("exp_eur")
    accs = [s["account"] for s in r["stakes"]]
    out = ["", "%s%d. %s%s" % ("★ " if k == 1 and exp is not None and exp > 0 else "", k, name,
                               " – " + sector if sector else ""),
           "   Was passiert ist: Um %s Uhr ist sie höher gestiegen als in der ersten Viertelstunde%s." % (
               when, "" if i.get("pct") is None else " (im Vergleich zu gestern %s)" % pct(i["pct"])),
           "   1 Aktie kostet: %s" % money(r["price"]),
           "   Kaufen für: " + " / ".join("%s € (%s)" % (de(s["amount"]).replace(",00", ""), konto(s["account"]))
                                         for s in r["stakes"]),
           "   Sicherheitsnetz (Stop): %s. Fällt sie so tief, verkaufen." % money(r["stop"]),
           "   Gewinnziel: %s. Steigt sie so hoch, verkaufen." % money(r["target"]),
           "   Im schlechtesten Fall: " + per_account([-s["loss"] for s in r["stakes"]], accs),
           "   Wenn das Ziel klappt: " + per_account([s["gain"] for s in r["stakes"]], accs) +
           " (Gebühren schon abgezogen)"]
    weak = [konto(s["account"]) for s in r["stakes"] if s["gain"] < s["loss"]]
    if weak:
        out.append("   Hinweis: Beim %s ist der Gewinn am Ziel wegen der Gebühren kleiner als der mögliche Verlust. "
                   "Dort lohnt sich diese Aktie eher nicht." % " und ".join(weak))
    if r.get("exp"):
        out.append("   Zahlt sich laut Probe im Schnitt aus: %s pro Trade%s." % (
            per_account(r["exp"], accs), "" if exp > 0 else " (also eher nicht)"))
    out += tmachine.german(tmr)
    note = pelosi_note(r["symbol"])
    if note != "kein Handel gemeldet":
        out.append("   Hinweis: Nancy Pelosi hat diese Aktie %s." % note)
    return out[:2] + about_lines(r["symbol"], i, name_of or {}) + out[2:]


def howto_lines(cfg, flat_vienna):
    """Why the stop, when the sell pushes come, the day limits and the probe (US and Europe)."""
    return ["",
            "Warum das Sicherheitsnetz (Stop-Loss)? Das ist ein Verkaufsauftrag, den du gleich nach dem Kauf "
            "in Trade Republic setzt. Fällt der Kurs auf diesen Preis, verkauft Trade Republic automatisch, "
            "auch wenn du gerade nicht aufs Handy schaust. So verlierst du höchstens den Betrag von oben. "
            "Ohne Sicherheitsnetz kann aus einem kleinen Verlust schnell ein großer werden. Bei einem "
            "plötzlichen Absturz kann der Verkauf etwas unter dem Preis passieren.",
            "",
            "Der Betrag bei „Kaufen für“ ist so gewählt, dass du beim Sicherheitsnetz höchstens %s verlierst "
            "(Gebühren dabei) und nie mehr als %g %% des Kontos in einer Aktie steckt. Der mögliche Gewinn beim "
            "Ziel ist etwa doppelt so groß." % (limits_text(cfg), cfg.get("max_position_pct", 75)),
            "",
            "Wann verkaufen? Für jede Aktie bekommst du eine eigene Nachricht mit Alarm-Ton, ich schaue jede "
            "Minute:",
            "• Gefallen bis zum Sicherheitsnetz → „VERKAUFEN: … ist gefallen“",
            "• Gestiegen bis zum Gewinnziel → „VERKAUFEN: … hat das Ziel erreicht“",
            "• Erst gestiegen, dann wieder stark gefallen → „VERKAUFEN: … Gewinn sichern“",
            "• Spätestens um %s Uhr alles verkaufen, nichts über Nacht behalten. Davor kommt eine "
            "Erinnerung." % flat_vienna,
            "",
            "Deine Regeln: höchstens %d Käufe am Tag und höchstens %s Verlust am Tag (Europa und USA "
            "zusammen). Ist eins davon erreicht, kaufst du auf diesem Konto heute nichts mehr." % (
                cfg.get("max_trades_per_day", 3), limits_text(cfg, 2)),
            "",
            "Was ist die Probe? Ich spiele den Rest des Tages 3.000-mal mit den echten Kursbewegungen der "
            "Aktie aus den letzten Wochen nach. Weil der mögliche Gewinn doppelt so groß ist wie der mögliche "
            "Verlust, reicht es schon, wenn das Ziel in mehr als 1 von 3 Fällen klappt. „Echt passiert“ "
            "zeigt, wie dieselbe Methode an den letzten Börsentagen wirklich ausging (mit dem "
            "Sicherheitsnetz des jeweiligen Tages, darum können die Zahlen anders sein). Ein Blick zurück, "
            "keine Garantie.",
            "",
            "„Zahlt sich im Schnitt aus“ ist das durchschnittliche Ergebnis aller Probeläufe in Euro, nach "
            "Gebühren. Danach ist die Liste sortiert.",
            "",
            "Das sind Zahlen und deine eigenen Regeln, keine Empfehlung. Ob du kaufst, entscheidest du."]


# ---------------------------------------------------------------- sell checks

def sent_titles():
    """Titles already pushed to the topic today (ntfy keeps 12 hours), to send each alert once."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return set()
    url = "%s/%s/json?poll=1&since=12h" % (os.environ.get("NTFY_SERVER", "https://ntfy.sh"),
                                           urllib.parse.quote(topic))
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            lines = r.read().decode("utf-8", "replace").splitlines()
        return {json.loads(x).get("title", "") for x in lines if x.strip()}
    except Exception as e:
        print("ntfy history not read (%s): alerts may repeat" % e, file=sys.stderr)
        return set()


def minute_bars(sym, day, now_et, since):
    """1-minute bars of `day` complete at `now_et` and starting at or after `since`,
    plus the live price as a last pseudo bar. -> (bars, last price)."""
    series = scan.parse_chart(scan.yahoo_get(scan.chart_path(sym, "1d", "1m")), sym)
    bars = []
    for i, ts in enumerate(series["t"]):
        h, l, c = series["h"][i] if series["h"] else None, series["l"][i] if series["l"] else None, series["c"][i]
        if c is None or h is None or l is None:
            continue
        et = scan.ts_to_eastern(ts)
        if et.date() != day or et < since or et.time() >= datetime.time(16, 0):
            continue
        if et + datetime.timedelta(minutes=1) > now_et:
            continue   # still in progress
        bars.append({"start": et, "h": float(h), "l": float(l), "c": float(c)})
    live = series["meta"].get("regularMarketPrice")
    if live:
        bars.append({"start": now_et, "h": float(live), "l": float(live), "c": float(live), "live": True})
    last = bars[-1]["c"] if bars else None
    return bars, last


def check(p, bars, cfg):
    """Walk the bars in order. Per bar: stop first (the careful reading when one
    bar touches two levels), then the target, then profit protection: once the
    price has been protect_after_r x risk above the entry, a fall back that keeps
    only protect_keep_pct of the best gain is a sell signal too.
    -> (kind, bar, level, peak) or None."""
    risk = p["price"] - p["stop"]
    arm = p["price"] + cfg.get("protect_after_r", 1) * risk
    keep = cfg.get("protect_keep_pct", 50) / 100.0
    peak = p["price"]
    for b in bars:
        if b["l"] <= p["stop"]:
            return ("Stop", b, p["stop"], peak)
        if b["h"] >= p["target"]:
            return ("Ziel", b, p["target"], max(peak, b["h"]))
        if peak >= arm:
            floor = p["price"] + keep * (peak - p["price"])
            if b["l"] <= floor:
                return ("Gewinn sichern", b, floor, peak)
        peak = max(peak, b["h"])
    return None


def sell_message(p, kind, b, level, peak, last, cfg, name_of):
    when = clock(b["start"] + (datetime.timedelta(0) if b.get("live") else datetime.timedelta(minutes=1)))
    name = head(p["symbol"], name_of)
    firm = short(name_of.get(p["symbol"], p["symbol"]))
    if kind == "Stop":
        title = "VERKAUFEN: %s ist gefallen" % name
        what = ["%s ist um %s Uhr auf %s gefallen. Das ist dein Sicherheitsnetz (Stop-Loss): Der Anstieg "
                "hat leider nicht gehalten." % (firm, when, money(b["l"])),
                "",
                "Jetzt verkaufen, damit der Verlust klein bleibt. Genau dafür ist das Sicherheitsnetz da."]
    elif kind == "Ziel":
        title = "VERKAUFEN: %s hat das Ziel erreicht" % name
        what = ["Sehr gut! %s ist um %s Uhr auf %s gestiegen. Dein Ziel ist erreicht." % (
                    firm, when, money(b["h"])),
                "",
                "Jetzt verkaufen und den Gewinn mitnehmen, bevor er wieder kleiner wird."]
    else:
        title = "VERKAUFEN: %s, Gewinn sichern" % name
        what = ["%s war schon bis %s gestiegen, ist um %s Uhr aber wieder auf %s gefallen. Die Hälfte "
                "des Gewinns ist damit schon wieder weg." % (firm, money(peak), when, money(b["l"])),
                "",
                "Jetzt verkaufen, damit aus dem Gewinn kein Verlust wird."]
    msg = what + [
        "",
        "Kurs jetzt: %s" % money(last),
        "Dein Ergebnis jetzt: %s (nach Gebühren)" % results(p, last, cfg),
        "",
        "So geht's (nur wenn du %s heute gekauft hast):" % firm,
        "1. Trade Republic öffnen → Depot → %s." % firm,
        "2. Ist sie nicht mehr da? Dann hat dein Sicherheitsnetz schon verkauft und alles ist erledigt.",
        "3. Sonst: Verkaufen → alles → sofort verkaufen (Market).",
        "4. Danach den Stop-Loss löschen, falls er noch offen ist (unter „Orders“).",
    ]
    return title, "\n".join(msg)


def watch_once(cfg, now_et, seen, today, name_of, done):
    """One pass over today's list. Returns the pushes to send."""
    out, still_open = [], []
    for p in today["plays"]:
        if p["symbol"] in done:
            continue
        brk = datetime.datetime.combine(now_et.date(), datetime.datetime.strptime(p["bar_start"], "%H:%M").time())
        try:
            bars, last = MINUTE_BARS(p["symbol"], now_et.date(), now_et, brk + datetime.timedelta(minutes=5))
        except Exception as e:
            print("%s: no chart (%s)" % (p["symbol"], e), file=sys.stderr)
            continue
        if last is None:
            continue
        hit = check(p, bars, cfg)
        if not hit:
            still_open.append((p, last))
            continue
        done.add(p["symbol"])
        title, msg = sell_message(p, *hit, last=last, cfg=cfg, name_of=name_of)
        if title not in seen:
            out.append((title, msg, 5))
            seen.add(title)
    # Closing reminder from 25 minutes before flat_by, once a day.
    flat = scan.parse_hhmm(cfg.get(FLAT_KEY, "15:55"), FLAT_KEY)
    flat_dt = datetime.datetime.combine(now_et.date(), flat)
    title = "Bis %s Uhr alles verkaufen (%s)" % (clock(flat_dt), now_et.strftime("%d.%m."))
    if (flat_dt - datetime.timedelta(minutes=25)).time() <= now_et.time() < CLOSE \
            and still_open and title not in seen:
        lines = ["Die Börse schließt bald. Beim Day Trading behält man nichts über Nacht: Am nächsten Morgen "
                 "kann der Kurs stark springen, und dann schützt auch der Stop-Loss nicht.",
                 "",
                 "Diese Aktien von heute laufen noch:"]
        for p, last in still_open:
            lines.append("• %s: jetzt %s, dein Ergebnis %s" % (
                head(p["symbol"], name_of), money(last), results(p, last, cfg)))
        lines += ["",
                  "So geht's (nur für Aktien, die du heute gekauft hast):",
                  "Trade Republic → Depot → Aktie → Verkaufen → alles → Market, bis %s Uhr. "
                  "Danach den Stop-Loss löschen." % clock(flat_dt)]
        out.append((title, "\n".join(lines), 5))
        seen.add(title)
    return out


def watch(cfg, until_et, name_of=None):
    """Sell checks every minute until `until_et` (local market time) or the close."""
    seen, done = sent_titles(), set()
    name_of = name_of or names()
    while True:
        now_et = NOW()
        if now_et.time() >= min(until_et, CLOSE):
            print("Watch ends at %s market time." % now_et.strftime("%H:%M"))
            return
        try:
            with open(TODAY, encoding="utf-8") as fh:
                today = json.load(fh)
        except (OSError, ValueError):
            print("No %s: nothing to watch." % os.path.basename(TODAY))
            return
        if today.get("date") != now_et.date().isoformat() or not today.get("plays"):
            print("Nothing to watch today.")
            return
        for title, msg, prio in watch_once(cfg, now_et, seen, today, name_of, done):
            send(title, msg, prio)
        if len(done) == len(today["plays"]):
            print("Every stock of today's list got its sell message: watch ends.")
            return
        time.sleep(max(5, 60 - datetime.datetime.now().second))   # about once a minute


# The market the sell checks run on. europe.py switches these to Xetra / Vienna time.
CLOSE = datetime.time(16, 0)
FLAT_KEY = "flat_by"
NOW = lambda: scan.to_eastern(datetime.datetime.now(datetime.timezone.utc))  # noqa: E731
MINUTE_BARS = minute_bars


# ---------------------------------------------------------------- sending and main

def split_message(message, limit=3600):
    """Parts of at most `limit` bytes, cut at blank lines (ntfy takes about 4,000 bytes)."""
    parts, cur = [], ""
    for block in message.split("\n\n"):
        cand = (cur + "\n\n" + block) if cur else block
        if len(cand.encode("utf-8")) <= limit:
            cur = cand
            continue
        if cur:
            parts.append(cur)
        while len(block.encode("utf-8")) > limit:      # one block alone too long: hard cut
            cut = limit // 2
            parts.append(block[:cut])
            block = block[cut:]
        cur = block
    if cur:
        parts.append(cur)
    return parts


def send(title, message, priority=3):
    parts = split_message(message)
    if len(parts) > 1:
        for k, part in enumerate(parts, 1):
            send("%s (%d/%d)" % (title, k, len(parts)), part, priority)
            time.sleep(1)
        return
    print("== %s ==\n%s" % (title, message))
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("(NTFY_TOPIC not set: nothing sent)")
        return
    tags = ["rotating_light"] if priority >= 5 else ["chart_with_upwards_trend"]
    body = json.dumps({"topic": topic, "title": title, "message": message,
                       "priority": priority, "tags": tags}).encode()
    req = urllib.request.Request(os.environ.get("NTFY_SERVER", "https://ntfy.sh"), data=body,
                                 headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                print("sent, HTTP", r.status)
                return
        except Exception as e:
            print("send failed (%s), retrying" % e, file=sys.stderr)
            time.sleep(5)


def pick_mode(now_et):
    t = now_et.time()
    # Wide windows: GitHub often starts scheduled runs 45 minutes late or more.
    # A second run in the same window is skipped by already_sent().
    if datetime.time(7, 30) <= t < datetime.time(9, 30):
        return "premarket"
    if datetime.time(9, 30) <= t < datetime.time(11, 45):
        return "open"
    return None


def already_sent(mode):
    """True when today's pre-market list or breakout list already went out."""
    prefix, failed = {"premarket": ("Vorbörse:", "fehlgeschlagen"),
                      "open": ("Eröffnung:", "kein Scan")}.get(mode, (None, None))
    if not prefix:
        return False
    return any(t.startswith(prefix) and failed not in t for t in sent_titles())


def main():
    global RATE
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "premarket", "open", "watch", "growth", "test"], default="auto")
    ap.add_argument("--until", default="16:00", help="watch: stop at this New York time")
    ap.add_argument("--start-between", help="watch: only start inside this New York window, like 12:45-13:30")
    a = ap.parse_args()
    now_et = scan.to_eastern(datetime.datetime.now(datetime.timezone.utc))
    if a.mode == "test":
        send("Test: Trading-Alarm", "Alles klappt: Meine Nachrichten kommen bei dir an. Die nächsten echten "
                                    "kommen am nächsten Börsentag ab ca. 14:15 Uhr.")
        return 0
    open_today, why = scan.trading_day(now_et.date())
    if not open_today:
        print("No NYSE session today (%s, %s): nothing to do." % (now_et.date(), why))
        return 0
    cfg = load_config()
    if a.mode == "watch":
        if a.start_between:
            lo, hi = (scan.parse_hhmm(x, "--start-between") for x in a.start_between.split("-"))
            if not lo <= now_et.time() < hi:
                print("Watch start window %s missed (%s ET): nothing to do." % (
                    a.start_between, now_et.strftime("%H:%M")))
                return 0
        RATE = eur_rate()
        watch(cfg, scan.parse_hhmm(a.until, "--until"))
        return 0
    mode = a.mode if a.mode != "auto" else pick_mode(now_et)
    if mode is None:
        print("Outside the windows at %s ET: nothing to do." % now_et.strftime("%H:%M"))
        return 0
    if a.mode == "auto" and already_sent(mode):
        print("The %s list already went out today: nothing to do." % mode)
        return 0
    if mode == "open":
        # Started early: wait for the 15-minute range plus one complete 5-minute bar.
        ready = now_et.replace(hour=9, minute=50, second=30, microsecond=0)
        if now_et < ready:
            print("Waiting until 9:50 ET for the opening range.")
            time.sleep((ready - now_et).total_seconds())
    RATE = eur_rate()
    if mode != "growth":
        send(*(premarket(cfg) if mode == "premarket" else opening(cfg)))
    if mode in ("premarket", "growth"):
        try:
            send(*growth_list(cfg))
        except Exception as e:  # the day-trading push already went out
            send("Wachstums-Liste: Fehler", "Die Liste konnte heute nicht erstellt werden (%s)." % e)
        try:
            for title, msg in pelosi():
                send(title, msg, 4)
        except Exception as e:
            print("pelosi check failed: %s" % e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
