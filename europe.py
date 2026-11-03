"""Push alerts for European stocks on Trade Republic: DAX, MDAX and EURO STOXX 50.

Times are Austrian time (Paris, Amsterdam, Milan, Madrid and Brussels are the
same). Xetra trades 9:00 to 17:30. Prices are euros, so nothing is converted.

    python europe.py --mode auto          # picks premarket (7:00-9:16) or open (9:16-11:30)
    python europe.py --mode premarket     # stocks that showed a pattern on the last day
    python europe.py --mode open          # opening-range breakouts with size, stop, target, time machine
    python europe.py --mode watch --until 13:00
"""
import argparse
import datetime
import json
import math
import os
import sys
import time

import alert
import scan       # alert.py put tools/ on the path
import tmachine

HERE = alert.HERE
LIST = os.path.join(HERE, "trader", "europe.txt")
TODAY = os.path.join(HERE, "trader", "today-eu.json")
OPENED = os.path.join(HERE, "trader", "opened-eu.flag")
OPEN, CLOSE = datetime.time(9, 0), datetime.time(17, 30)
OPEN_MIN, BARS = 9 * 60, 102
# Xetra holidays (deutsche-boerse.com trading calendar). Euronext closes on fewer days;
# on those days its stocks simply have no data and are skipped.
CLOSED = {"2026-01-01", "2026-04-03", "2026-04-06", "2026-05-01", "2026-12-24", "2026-12-25", "2026-12-31",
          "2027-01-01", "2027-03-26", "2027-03-29", "2027-12-24", "2027-12-31"}
COUNTRY = {"DE": "Deutschland", "PA": "Frankreich", "AS": "Niederlande", "MI": "Italien", "MC": "Spanien",
           "BR": "Belgien", "HE": "Finnland"}
INDEX = {"DAX": "DAX", "MDAX": "MDAX", "EURO STOXX 50": "Euro Stoxx 50"}


# ---------------------------------------------------------------- basics

def now_local():
    return tmachine.to_vienna(datetime.datetime.now(datetime.timezone.utc))


def ts_local(ts):
    return tmachine.to_vienna(datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)))


def trading_day(d):
    return d.weekday() < 5 and d.isoformat() not in CLOSED


def stocks():
    """{SYMBOL: {"name", "sector", "index"}} from trader/europe.txt."""
    out = {}
    with open(LIST, encoding="utf-8") as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                sym, name, sector, index = (line.rstrip("\n").split("|") + ["", "", ""])[:4]
                out[sym] = {"name": name, "sector": sector, "index": index}
    return out


def where(sym, st):
    """'Software · DAX, Deutschland'"""
    s = st[sym]
    parts = [alert.branche(s["sector"])]
    place = INDEX.get(s["index"], s["index"])
    country = COUNTRY.get(sym.rsplit(".", 1)[-1], "")
    parts.append(", ".join(x for x in (place, country) if x))
    return " · ".join(x for x in parts if x)


def use_europe():
    """Point the shared sell checks in alert.py at Xetra and Austrian time."""
    alert.EUR_ONLY, alert.RATE = True, 1.0
    alert.CLOCK = lambda dt: dt.strftime("%H:%M")
    alert.TODAY, alert.CLOSE, alert.FLAT_KEY = TODAY, CLOSE, "eu_flat_by"
    alert.NOW = now_local
    alert.MINUTE_BARS = minute_bars


def spark(symbols, rng, interval):
    data = {}
    for i in range(0, len(symbols), scan.SPARK_MAX):
        batch = symbols[i:i + scan.SPARK_MAX]
        try:
            data.update(scan.parse_spark(scan.yahoo_get(scan.spark_path(batch, rng, interval)),
                                         {s.upper(): s for s in batch}))
        except Exception as e:
            print("spark failed for %s...: %s" % (batch[0], e), file=sys.stderr)
        time.sleep(0.3)
    return data


def day_bars(sym, day, now, interval="5m"):
    """{slot: bar} of today's complete bars (slot 0 = 9:00) plus the series."""
    ser = scan.parse_chart(scan.yahoo_get(scan.chart_path(sym, "1d", interval)), sym)
    step = 5 if interval == "5m" else 1
    out = {}
    for i, ts in enumerate(ser["t"]):
        h = ser["h"][i] if ser["h"] else None
        lo = ser["l"][i] if ser["l"] else None
        c = ser["c"][i]
        if None in (h, lo, c) or ts % (60 * step):
            continue
        t = ts_local(ts)
        mins = t.hour * 60 + t.minute - OPEN_MIN
        if t.date() != day or mins < 0 or mins >= BARS * 5 or t + datetime.timedelta(minutes=step) > now:
            continue
        v = ser["v"][i] if ser["v"] else 0
        out[mins // step] = {"start": t, "h": float(h), "l": float(lo), "c": float(c), "v": v or 0}
    return out, ser


def minute_bars(sym, day, now, since):
    """For alert.watch_once: 1-minute bars from `since`, plus the live price."""
    bars, ser = day_bars(sym, day, now, "1m")
    out = [b for k, b in sorted(bars.items()) if b["start"] >= since]
    live = ser["meta"].get("regularMarketPrice")
    if live:
        out.append({"start": now, "h": float(live), "l": float(live), "c": float(live), "live": True})
    return out, (out[-1]["c"] if out else None)


# ---------------------------------------------------------------- the two lists

def premarket(cfg):
    st = stocks()
    now = now_local()
    data = spark(sorted(st), "6mo", "1d")
    hits, checked = [], 0
    for sym in sorted(st):
        ser = data.get(sym)
        if not ser:
            continue
        closes = {}
        for ts, c in zip(ser["t"], ser["c"]):
            d = ts_local(ts).date()
            if c is None or d > now.date() or (d == now.date() and now.time() < datetime.time(17, 35)):
                continue
            closes[d] = float(c)
        closes = [c for _, c in sorted(closes.items())]
        if len(closes) < scan.MIN_DAILY_BARS:
            continue
        checked += 1
        price = closes[-1]
        if not alert.price_ok(price, cfg) or sym in cfg.get("blocked", []):
            continue
        tags, _ = scan.find_setups(closes)
        if tags:
            vol = ser["meta"].get("regularMarketVolume") or 0
            hits.append({"symbol": sym, "setups": tags, "price": price, "turnover": price * vol})
    print("europe premarket: %d of %d checked, %d hits" % (checked, len(st), len(hits)), file=sys.stderr)
    if not checked:
        return ("Europa Vorbörse: Scan fehlgeschlagen",
                "Heute sind leider keine Kursdaten für Europa angekommen. Die Ausbrüche um ca. 9:20 Uhr kommen "
                "trotzdem, wenn die Daten bis dahin da sind.")
    hits.sort(key=lambda h: -h["turnover"])
    pick = alert.pick_five(hits)
    name_of = {s: v["name"] for s, v in st.items()}
    info = alert.news_for([h["symbol"] for h in pick], name_of)
    lines = ["Guten Morgen! Trade Republic ist ab 7:30 Uhr offen. Richtig viel gehandelt wird aber erst ab "
             "9:00 Uhr, wenn die Börse in Frankfurt öffnet. Vorher sind die Preise oft ungünstig.",
             "",
             "Diese Aktien sind mir am letzten Börsentag aufgefallen. Noch nicht kaufen, nur anschauen. "
             "Um ca. 9:20 Uhr schreibe ich dir, ob eine davon wirklich steigt.",
             "",
             "(Ich habe %d Firmen aus DAX, MDAX und Euro Stoxx 50 geprüft, alle bei Trade Republic.)" % checked]
    for k, h in enumerate(pick, 1):
        i = info[h["symbol"]]
        lines += ["", "%d. %s – %s" % (k, alert.head(h["symbol"], name_of), where(h["symbol"], st)),
                  "   1 Aktie kostet: %s%s" % (alert.money(h["price"]),
                                              "" if i["pct"] is None else ", zuletzt " + alert.pct(i["pct"])),
                  "   Was auffällt: " + " ".join(alert.EXPLAIN.get(s, s) for s in h["setups"])]
        lines += alert.about_lines(h["symbol"], i, name_of)
    if not pick:
        lines += ["", "Heute ist keine europäische Aktie aufgefallen, die zu deinen Regeln passt."]
    lines += ["", "Das sind Fakten aus den Kursdaten, keine Empfehlung. Du entscheidest selbst."]
    return "Europa Vorbörse: %d Aktien für heute im Blick" % len(pick), "\n".join(lines)


def opening(cfg):
    st = stocks()
    now = now_local()
    day = now.date()
    found, checked, failed = [], 0, 0
    for sym in sorted(st):
        try:
            bars, _ = day_bars(sym, day, now)
        except Exception as e:
            failed += 1
            print("%s: %s" % (sym, e), file=sys.stderr)
            continue
        time.sleep(0.2)
        if not all(k in bars for k in (0, 1, 2)):
            continue
        checked += 1
        oh = max(bars[k]["h"] for k in (0, 1, 2))
        ol = min(bars[k]["l"] for k in (0, 1, 2))
        hit = next((k for k in sorted(bars) if k >= 3 and bars[k]["c"] > oh), None)
        if hit is None:
            continue
        b = bars[hit]
        found.append({"symbol": sym, "price": round(b["c"], 2), "or_high": oh, "or_low": ol, "bar": hit,
                      "bar_start": b["start"].strftime("%H:%M"),
                      "at": (b["start"] + datetime.timedelta(minutes=5)).strftime("%H:%M")})
    print("europe open: %d of %d checked (%d failed), %d broke out" % (checked, len(st), failed, len(found)),
          file=sys.stderr)
    if not checked:
        return ("Europa Eröffnung: kein Scan",
                "Heute sind leider keine Kursdaten für die europäischen Ausbrüche angekommen. Kein Trade ist heute "
                "die sichere Wahl.")
    rows = alert.plan_rows(found, cfg)
    flat = scan.parse_hhmm(cfg.get("eu_flat_by", "17:25"), "eu_flat_by")
    rows = alert.rank_by_probe(rows, lambda r: tmachine.replay(
        r["symbol"], day.isoformat(), r["price"], r["stop"], r["target"], r["bar"], "eu",
        until=flat.hour * 60 + flat.minute), cfg)
    with open(TODAY, "w", encoding="utf-8") as fh:
        json.dump({"date": day.isoformat(), "plays": [
            {k: r[k] for k in ("symbol", "price", "stop", "target", "stakes", "max_loss_eur", "at", "bar_start")}
            for r in rows]}, fh, indent=2)
    with open(OPENED, "w") as fh:
        fh.write(day.isoformat())
    name_of = {s: v["name"] for s, v in st.items()}
    info = alert.news_for([r["symbol"] for r in rows], name_of)
    lines = ["Die Börse in Frankfurt ist seit 9:00 Uhr offen. Hier sind Aktien, die gerade stark steigen: "
             "höher als in der ersten Viertelstunde des Tages. Manchmal geht so ein Anstieg weiter, manchmal "
             "nicht.", ""]
    if rows:
        lines += ["Alles ist so ausgerechnet, dass du pro Aktie höchstens %s verlieren kannst, Gebühren "
                  "schon dabei." % alert.limits_text(cfg), ""]
    lines.append("(%d von %d Firmen aus DAX, MDAX und Euro Stoxx 50 sind über ihre erste Viertelstunde gestiegen, "
                 "%d %s zu deinen Regeln.)" % (
        len(found), checked, len(rows), "passt" if len(rows) == 1 else "passen"))
    if checked < 0.5 * len(st):
        lines += ["", "Achtung: Heute kamen nur Kursdaten für %d Aktien an. Es kann also gute Kandidaten geben, die "
                      "hier fehlen." % checked]
    if not rows:
        lines += ["", "Heute passt keine europäische Aktie zu deinen Regeln (höchstens %s Verlust pro Trade). Das "
                      "ist völlig in Ordnung: Kein Trade ist besser als ein schlechter." % alert.limits_text(cfg)]
    lines += alert.best_line(rows, name_of)
    for k, r in enumerate(rows, 1):
        lines += alert.play_lines(k, r, info[r["symbol"]], alert.head(r["symbol"], name_of),
                                  where(r["symbol"], st), r["at"], r.get("tm"), cfg, name_of)
    if rows:
        lines += alert.howto_lines(cfg, flat.strftime("%H:%M"))
    return alert.open_title("Europa Eröffnung", rows, name_of), "\n".join(lines)


# ---------------------------------------------------------------- main

def pick_mode(now):
    t = now.time()
    if datetime.time(7, 0) <= t < datetime.time(9, 16):
        return "premarket"
    if datetime.time(9, 16) <= t < datetime.time(11, 30):
        return "open"
    return None


def already_sent(mode):
    prefix, failed = {"premarket": ("Europa Vorbörse:", "fehlgeschlagen"),
                      "open": ("Europa Eröffnung:", "kein Scan")}[mode]
    return any(t.startswith(prefix) and failed not in t for t in alert.sent_titles())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "premarket", "open", "watch"], default="auto")
    ap.add_argument("--until", default="17:30", help="watch: stop at this Austrian time")
    ap.add_argument("--start-between", help="watch: only start inside this window, like 12:00-17:25")
    a = ap.parse_args()
    now = now_local()
    if not trading_day(now.date()):
        print("No Xetra session today (%s): nothing to do." % now.date())
        return 0
    cfg = alert.load_config()
    use_europe()
    if a.mode == "watch":
        if a.start_between:
            lo, hi = (scan.parse_hhmm(x, "--start-between") for x in a.start_between.split("-"))
            if not lo <= now.time() < hi:
                print("Watch start window %s missed (%s): nothing to do." % (a.start_between, now.strftime("%H:%M")))
                return 0
        alert.watch(cfg, scan.parse_hhmm(a.until, "--until"), name_of={s: v["name"] for s, v in stocks().items()})
        return 0
    mode = a.mode if a.mode != "auto" else pick_mode(now)
    if mode is None:
        print("Outside the windows at %s: nothing to do." % now.strftime("%H:%M"))
        return 0
    if a.mode == "auto" and already_sent(mode):
        print("The Europe %s list already went out today: nothing to do." % mode)
        return 0
    if mode == "open":
        ready = now.replace(hour=9, minute=20, second=30, microsecond=0)
        if now < ready:
            print("Waiting until 9:20 for the opening range.")
            time.sleep((ready - now).total_seconds())
    alert.send(*(premarket(cfg) if mode == "premarket" else opening(cfg)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
