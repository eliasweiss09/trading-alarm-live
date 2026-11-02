"""Daily stock alerts to your phone via ntfy, sized for Trade Republic (EUR).

Runs the scanner and the news check, keeps what fits your rules in
trader/config.json, and sends messages to the ntfy topic in the NTFY_TOPIC
environment variable. Without NTFY_TOPIC it only prints.

    python alert.py --mode premarket          # day-trading list + growth list
    python alert.py --mode open               # breakouts with stop, target, shares
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

CONFIG = os.path.join(HERE, "trader", "config.json")
TODAY = os.path.join(HERE, "trader", "today.json")      # the 15:55 list, for the sell checks
OPENED = os.path.join(HERE, "trader", "opened.flag")    # this run did the 15:55 scan
EXPLAIN = {
    "breakout_20d": "schloss so hoch wie seit 20 Tagen nicht",
    "bull_flag": "erst stark gestiegen, pendelt jetzt seitwärts",
    "reclaim_ma20": "wieder über dem Durchschnitt der letzten 20 Tage",
    "oversold": "stark gefallen in kurzer Zeit",
    "oversold_bounce": "nach starkem Fall zuletzt wieder im Plus",
    "orb": "nach Börsenstart über die Spanne der ersten 15 Minuten gestiegen",
}
TREND = ("breakout_20d", "bull_flag", "reclaim_ma20", "orb")
NAME_JUNK = re.compile(r"\s*(-\s*)?(Class [A-Z] )?(Common Stock|Common Shares|Ordinary Shares?|"
                       r"American Depositary Shares?.*|Depositary Shares?.*)\s*$", re.I)
MONTHS = ["Jan", "Feb", "März", "April", "Mai", "Juni", "Juli", "Aug", "Sep", "Okt", "Nov", "Dez"]
MAX_ITEMS = 6
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


def head(sym, name_of):
    return "%s (%s)" % (name_of.get(sym, sym), sym)


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


def money(usd):
    """'304,00 $ (≈ 271,02 €)'"""
    return "%s $ (≈ %s €)" % (de(usd), de(eur(usd)))


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
    return (usd >= MIN_PRICE_USD and cfg.get("min_share_price_eur", 6) <= eur(usd)
            <= cfg.get("max_share_price_eur", 500))


def news_for(tickers):
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
    name_of = names()
    for it in doc.get("items", []):
        title = it.get("title", "")
        for t in it.get("touches") or [it.get("ticker")]:
            if t not in out or out[t]["news"]:
                continue
            # Only headlines that name the company or its ticker: Yahoo's feed mixes in others.
            word = (name_of.get(t, "").split() or [""])[0].strip(",.").lower()
            if not (re.search(r"\b%s\b" % re.escape(t), title) or (len(word) >= 4 and word in title.lower())):
                continue
            flag = "! " if it.get("important") else ""
            out[t]["news"].append(flag + title[:90])
    return out


def size(price, stop, cfg):
    """(shares, max loss in EUR incl. fees). The loss at the stop plus one fee
    to buy and one to sell stays within max_loss_per_trade_eur."""
    fees = 2 * cfg.get("fee_per_order_eur", 1)
    risk_eur = eur(price - stop)
    room = cfg.get("max_loss_per_trade_eur", 20) - fees
    if risk_eur <= 0 or room <= 0:
        return 0, 0.0
    by_loss = math.floor(room / risk_eur)
    by_cap = math.floor(cfg.get("budget_eur", 1000) * cfg.get("max_position_pct", 75) / 100 / eur(price))
    n = max(0, min(by_loss, by_cap))
    return n, n * risk_eur + fees


# ---------------------------------------------------------------- the daily lists

def premarket(cfg):
    run(["scan.py", "universe", "--json"])
    doc = run(["scan.py", "daily", "--json"])
    if not doc:
        return "Vorbörse: Scan fehlgeschlagen", "Der Tagesscan lieferte keine Daten. Morgen neuer Versuch."
    c = doc.get("counts", {})
    print("daily counts: %s" % json.dumps(c), file=sys.stderr)   # diagnostics for the run log
    hits = [h for h in doc.get("hits", []) if price_ok(h["price"], cfg)
            and h["symbol"] not in cfg.get("blocked", [])]
    trend = [h for h in hits if set(h["setups"]) & set(TREND)][:4]
    weak = [h for h in hits if not set(h["setups"]) & set(TREND)][:2]
    pick = trend + weak
    info = news_for([h["symbol"] for h in pick])
    lines = ["Was das ist: Aktien, deren Kursverlauf gestern ein Muster zeigte. Noch kein Kaufsignal. "
             "Ob eine davon heute ausbricht, kommt um ca. 15:55.",
             "%s von %s viel gehandelten Aktien passen (Schlusskurse bis %s, %g-%g € pro Aktie)." % (
                 c.get("hits", len(doc.get("hits", []))), c.get("scanned", "?"),
                 (doc.get("hits") or [{}])[0].get("close_date", "letzter Schluss"),
                 cfg.get("min_share_price_eur", 6), cfg.get("max_share_price_eur", 500))]
    name_of = names()
    for h in pick:
        i = info[h["symbol"]]
        pre = "" if i["pct"] is None else ", letzter Tag " + pct(i["pct"])
        lines.append("")
        lines.append("%s: %s%s" % (head(h["symbol"], name_of), money(h["price"]), pre))
        lines.append("  Muster: " + "; ".join(EXPLAIN.get(s, s) for s in h["setups"]))
        if i["news"]:
            lines.append("  News: " + i["news"][0])
    lines.append("")
    lines.append("Fakten, keine Empfehlung.")
    return "Vorbörse: %d Aktien zum Beobachten" % len(pick), "\n".join(lines)


def growth_list(cfg):
    """10 stocks with long, steady profit growth (SEC filings)."""
    doc = run(["../growth.py", "--top", "25"], timeout=600)
    if not doc or not doc.get("top"):
        return "Wachstums-Liste: keine Daten", "Die SEC-Daten kamen heute nicht an. Morgen neuer Versuch."
    info = news_for([r["symbol"] for r in doc["top"]])
    top = [r for r in doc["top"] if info[r["symbol"]]["price"] is not None
           and price_ok(info[r["symbol"]]["price"], cfg)
           and r["symbol"] not in cfg.get("blocked", [])][:10]
    if not top:
        return "Wachstums-Liste: keine passt", "Keine Wachstums-Aktie zwischen %g und %g € heute." % (
            cfg.get("min_share_price_eur", 6), cfg.get("max_share_price_eur", 500))
    name_of = names()
    month_now = datetime.date.today().strftime("%Y-%m")
    fee = cfg.get("fee_per_order_eur", 1)
    lines = ["Was das ist: Firmen, deren Gewinn 6 Jahre lang im Plus war und stark und stetig gewachsen ist "
             "(offizielle Meldungen bei der US-Börsenaufsicht SEC). Für langfristiges Anlegen, nicht Day Trading.",
             "%s von %s Firmen erfüllen das (Jahre %s), hier die 10 stärksten." % (
                 doc["matched"], doc["screened"], top[0]["years"])]
    for r in top:
        i = info[r["symbol"]]
        day = "" if i["pct"] is None else ", letzter Tag " + pct(i["pct"])
        lines.append("")
        lines.append("%s: %s%s" % ("%s (%s)" % (name_of.get(r["symbol"], r["sec_name"].title()), r["symbol"]),
                                    money(i["price"]), day))
        lines.append("  Gewinn %+.0f %%/Jahr, Umsatz %+.0f %%/Jahr, Gewinn in %d von 5 Jahren gestiegen" % (
            r["ni_cagr"], r["rev_cagr"], r["up_years"]))
        lines.append("  Vom Umsatz bleiben als Gewinn: früher %.0f %%, jetzt %.0f %%" % (
            r["margin_first"], r["margin_last"]))
        if r.get("quarter"):
            q = "Verlust" if r.get("q_loss") else (
                "Gewinn %+.0f %% ggü. Vorjahresquartal" % r["q_growth"] if r.get("q_growth") is not None else "")
            warn = " ACHTUNG: Wachstum stockt" if r.get("warning") else ""
            y, m = r["next_report"].split("-")
            due = "in Kürze" if r["next_report"] <= month_now else "ca. %s %s" % (MONTHS[int(m) - 1], y)
            lines.append("  Letztes Quartal (%s): %s%s. Nächste Zahlen: %s" % (r["quarter"], q, warn, due))
        else:
            lines.append("  Letztes Quartal: kein vergleichbarer Wert in den SEC-Daten")
        if i["news"]:
            lines.append("  News: " + i["news"][0][:80])
    lines.append("")
    lines.append("Gute Vergangenheit heißt nicht, dass die Aktie steigt. Keine Empfehlung. "
                 "Gebühr: %g € Kauf + %g € Verkauf." % (fee, fee))
    return "Wachstums-Liste: 10 Aktien", "\n".join(lines)


def opening(cfg):
    uni = run(["scan.py", "universe", "--json"])  # each cloud run starts empty
    doc = run(["scan.py", "orb", "--json", "--max-confirm", "150"])
    if not doc:
        return "Eröffnung: kein Scan", "Der Ausbruch-Scan lief nicht (Markt zu, zu früh oder keine Daten)."
    # Diagnostics for the run log: how much of the market the data source answered for.
    failed = doc.get("failed") or {}
    print("universe: %s" % ((uni or {}).get("count")), file=sys.stderr)
    print("orb counts: %s" % json.dumps(doc.get("counts", {})), file=sys.stderr)
    print("orb failed: %d, e.g. %s" % (len(failed), json.dumps(dict(list(failed.items())[:5]))
                                        if isinstance(failed, dict) else str(failed)[:300]), file=sys.stderr)
    coverage = doc.get("counts", {}).get("scanned", 0)
    c = doc.get("counts", {})
    r_mult = cfg.get("target_r", 2)
    rows = []
    for h in doc.get("hits", []):
        if not price_ok(h["price"], cfg) or h["symbol"] in cfg.get("blocked", []):
            continue
        stop = math.floor((h["or_high"] + h["or_low"]) / 2 * 100) / 100   # range midpoint, rounded down
        target = math.ceil((h["price"] + r_mult * (h["price"] - stop)) * 100) / 100
        n, loss = size(h["price"], stop, cfg)
        if n < 1:
            continue
        gain = n * eur(target - h["price"]) - 2 * cfg.get("fee_per_order_eur", 1)
        if gain < loss:
            continue   # the fees eat the edge: the gain at the target is smaller than the loss at the stop
        rows.append(dict(h, stop=stop, target=target, shares=n, max_loss_eur=round(loss, 2), gain_eur=gain))
    rows.sort(key=lambda r: -r["gain_eur"])   # the biggest possible gain within your loss limit first
    rows = rows[:MAX_ITEMS]
    info = news_for([r["symbol"] for r in rows])
    name_of = names()
    day = scan.to_eastern(datetime.datetime.now(datetime.timezone.utc)).date().isoformat()
    with open(TODAY, "w", encoding="utf-8") as fh:
        json.dump({"date": day, "rate": RATE, "plays": [
            {k: r[k] for k in ("symbol", "price", "stop", "target", "shares", "max_loss_eur", "at", "bar_start")}
            for r in rows]}, fh, indent=2)
    with open(OPENED, "w") as fh:
        fh.write(day)
    fee = cfg.get("fee_per_order_eur", 1)
    lines = ["Was das ist: Aktien, die nach Börsenstart über ihre Spanne der ersten 15 Minuten gestiegen "
             "sind (Ausbruch). Die Stückzahl ist so gewählt, dass du beim Stop höchstens %g € verlierst, "
             "inkl. %g € Gebühren." % (cfg.get("max_loss_per_trade_eur", 20), 2 * fee),
             "%s von %s Aktien sind ausgebrochen, %d %s zu deinen Regeln. 1 € = %s $." % (
                 c.get("hits", len(doc.get("hits", []))), c.get("scanned", "?"), len(rows),
                 "passt" if len(rows) == 1 else "passen", ("%.4f" % RATE).replace(".", ","))]
    if coverage < 1000:
        lines.append("ACHTUNG: Heute konnten nur %s Aktien geprüft werden (die kostenlose Datenquelle hat "
                     "geblockt). Die Liste ist deshalb unvollständig." % coverage)
    if not rows:
        lines.append("Heute passt keine (max %g € Verlust, %g-%g € pro Aktie). Kein Trade ist auch ok." % (
            cfg.get("max_loss_per_trade_eur", 20), cfg.get("min_share_price_eur", 6),
            cfg.get("max_share_price_eur", 500)))
    for r in rows:
        i = info[r["symbol"]]
        today_pct = "" if i["pct"] is None else ", heute " + pct(i["pct"])
        brk = datetime.datetime.combine(datetime.date.fromisoformat(day),
                                        datetime.datetime.strptime(r["bar_start"], "%H:%M").time())
        lines.append("")
        lines.append("%s" % head(r["symbol"], name_of))
        lines.append("  Kurs %s, Ausbruch %s Uhr%s" % (money(r["price"]), vienna(brk + datetime.timedelta(minutes=5)),
                                                       today_pct))
        lines.append("  Stückzahl: %d (≈ %s € einsetzen)" % (r["shares"], de(r["shares"] * eur(r["price"]))))
        lines.append("  Stop-Loss: %s  -> sofort in Trade Republic setzen" % money(r["stop"]))
        lines.append("  Ziel: %s" % money(r["target"]))
        lines.append("  Höchstens %s € Verlust, beim Ziel ca. +%s € (beides nach Gebühren)" % (
            de(r["max_loss_eur"]), de(r["shares"] * eur(r["target"] - r["price"]) - 2 * fee)))
        if i["news"]:
            lines.append("  News: " + i["news"][0])
    if rows:
        lines += ["",
                  "So gehst du vor, wenn du kaufen willst:",
                  "1. Trade Republic: nach dem Firmennamen suchen -> Kaufen -> Stückzahl wie oben.",
                  "2. Gleich danach Verkaufen -> Stop-Loss -> Stop-Kurs in € von oben. Der verkauft sofort "
                  "und automatisch, wenn es schiefgeht.",
                  "3. Höchstens %d Trades am Tag. Ich prüfe jede Minute und melde Stop, Ziel und "
                  "'Gewinn sichern'." % cfg.get("max_trades_per_day", 3),
                  "€-Werte sind umgerechnet: in der App den €-Kurs prüfen. Fakten, keine Empfehlung."]
    return ("Eröffnung: 1 Aktie bricht aus" if len(rows) == 1 else
            "Eröffnung: %d Aktien brechen aus" % len(rows)), "\n".join(lines)


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
    fees = 2 * cfg.get("fee_per_order_eur", 1)
    n = p["shares"]
    when = vienna(b["start"] + (datetime.timedelta(0) if b.get("live") else datetime.timedelta(minutes=1)))
    pnl_now = n * eur(last - p["price"]) - fees
    name = head(p["symbol"], name_of)
    if kind == "Stop":
        title = "VERKAUFEN: %s - Stop erreicht" % name
        what = ["Was passiert ist: Um %s Uhr fiel der Kurs auf %s." % (when, money(b["l"])),
                "Dein Stop war %s. Der Ausbruch hat nicht gehalten." % money(p["stop"])]
        why = "Warum verkaufen: Damit der Verlust klein bleibt, so wie du es vorher geplant hast."
    elif kind == "Ziel":
        title = "VERKAUFEN: %s - Ziel erreicht" % name
        what = ["Was passiert ist: Um %s Uhr stieg der Kurs auf %s." % (when, money(b["h"])),
                "Dein Ziel war %s (doppelt so viel wie das Risiko)." % money(p["target"])]
        why = "Warum verkaufen: Der geplante Gewinn ist da. Wer wartet, riskiert, dass er wieder schrumpft."
    else:
        title = "VERKAUFEN: %s - Gewinn sichern" % name
        what = ["Was passiert ist: Die Aktie stieg bis %s und fiel um %s Uhr zurück auf %s." % (
                    money(peak), when, money(b["l"])),
                "Damit ist die Hälfte des besten Gewinns schon wieder weg."]
        why = "Warum verkaufen: Damit aus dem Gewinn kein Verlust wird."
    msg = [name, ""] + what + [
        "Kurs jetzt: %s" % money(last),
        "Ergebnis bei %d Stück jetzt: %s%s € nach Gebühren" % (n, "+" if pnl_now >= 0 else "", de(pnl_now)),
        "",
        why,
        "",
        "Was du tun sollst (nur wenn du %s heute gekauft hast):" % p["symbol"],
        "1. Trade Republic öffnen -> Depot -> %s" % name_of.get(p["symbol"], p["symbol"]),
        "2. Ist die Aktie nicht mehr im Depot, hat dein Stop-Loss schon verkauft. Dann ist alles erledigt.",
        "3. Sonst: Verkaufen -> alle %d Stück -> Market (sofort zum aktuellen Kurs)." % n,
        "4. Danach den offenen Stop-Loss löschen, falls er noch da ist.",
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
            bars, last = minute_bars(p["symbol"], now_et.date(), now_et, brk + datetime.timedelta(minutes=5))
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
    flat = scan.parse_hhmm(cfg.get("flat_by", "15:55"), "flat_by")
    flat_dt = datetime.datetime.combine(now_et.date(), flat)
    title = "Bis %s Uhr alles verkaufen (%s)" % (vienna(flat_dt), now_et.strftime("%d.%m."))
    if (flat_dt - datetime.timedelta(minutes=25)).time() <= now_et.time() < datetime.time(16, 0) \
            and still_open and title not in seen:
        fees = 2 * cfg.get("fee_per_order_eur", 1)
        lines = ["Day Trading heißt: nichts über Nacht halten. Über Nacht kann eine Aktie mit großem "
                 "Abstand öffnen, und dann hilft auch kein Stop-Loss.",
                 "",
                 "Noch ohne Stop, Ziel oder 'Gewinn sichern':"]
        for p, last in still_open:
            pnl = p["shares"] * eur(last - p["price"]) - fees
            lines.append("- %s: jetzt %s, bei %d Stück %s%s € nach Gebühren" % (
                head(p["symbol"], name_of), money(last), p["shares"], "+" if pnl >= 0 else "", de(pnl)))
        lines += ["",
                  "Was du tun sollst (nur für Aktien, die du heute gekauft hast):",
                  "Trade Republic -> Depot -> Aktie -> Verkaufen -> alle Stück -> Market, bis %s Uhr." % vienna(flat_dt),
                  "Danach offene Stop-Loss-Orders löschen."]
        out.append((title, "\n".join(lines), 5))
        seen.add(title)
    return out


def watch(cfg, until_et):
    """Sell checks every minute until `until_et` (New York time) or 16:00."""
    seen, done = sent_titles(), set()
    name_of = names()
    while True:
        now_et = scan.to_eastern(datetime.datetime.now(datetime.timezone.utc))
        if now_et.time() >= min(until_et, datetime.time(16, 0)):
            print("Watch ends at %s ET." % now_et.strftime("%H:%M"))
            return
        try:
            with open(TODAY, encoding="utf-8") as fh:
                today = json.load(fh)
        except (OSError, ValueError):
            print("No trader/today.json: nothing to watch.")
            return
        if today.get("date") != now_et.date().isoformat() or not today.get("plays"):
            print("Nothing to watch today.")
            return
        for title, msg, prio in watch_once(cfg, now_et, seen, today, name_of, done):
            send(title, msg, prio)
        if len(done) == len(today["plays"]) and now_et.time() >= datetime.time(15, 50):
            return
        time.sleep(max(5, 60 - datetime.datetime.now().second))   # about once a minute


# ---------------------------------------------------------------- sending and main

def send(title, message, priority=3):
    message = message[:3900]
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
        send("Test: Trading-Alarm", "Wenn du das liest, kommen die Push-Nachrichten an.")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
