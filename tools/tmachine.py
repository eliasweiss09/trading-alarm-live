"""The Time Machine (trader-timemachine) for New York and for Xetra / Europe.

replay() rebuilds the rest of the day thousands of times from the stock's own
5-minute bars of the last 60 days, and re-runs the same opening-range rule on
every one of those past days. It is a simulation, not a prediction.
"""
import datetime

import scan
import timemachine as tm

EPOCH = datetime.datetime(1970, 1, 1)


def _last_sunday(year, month):
    d = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)


def to_vienna(utc):
    """Naive UTC -> naive Austrian time (CET/CEST; Paris, Amsterdam, Milan... the same)."""
    utc = utc.replace(tzinfo=None)
    start = datetime.datetime.combine(_last_sunday(utc.year, 3), datetime.time(1, 0))
    end = datetime.datetime.combine(_last_sunday(utc.year, 10), datetime.time(1, 0))
    return utc + datetime.timedelta(hours=2 if start <= utc < end else 1)


MARKETS = {
    "us": {"open": 9 * 60 + 30, "close": 16 * 60, "to_local": tm.to_eastern},
    "eu": {"open": 9 * 60, "close": 17 * 60 + 30, "to_local": to_vienna},
}


def _use(market):
    m = MARKETS[market]
    tm.OPEN_MIN, tm.CLOSE_MIN = m["open"], m["close"]
    tm.SESSION_BARS = (m["close"] - m["open"]) // 5
    tm.to_eastern = m["to_local"]          # parse_bars() reads the local clock through this name
    tm.NYSE_EARLY_CLOSE = tm.NYSE_EARLY_CLOSE if market == "us" else {}


def _sessions(days, before, n):
    """Past days with at least 90 % of their bars, oldest first. A missing bar
    (no trade in those 5 minutes) is filled flat at the previous close."""
    out = []
    for d in sorted(days):
        if d >= before:
            continue
        s = days[d]
        if len(s) < 0.9 * n or 0 not in s:
            continue
        row, prev = [], s[0]
        for i in range(n):
            b = s.get(i)
            if b is None:
                c = prev["c"]
                b = dict(prev, o=c, h=c, l=c, slot=i)
            row.append(b)
            prev = b
        out.append((d, row))
    return out


def lean(p_target, p_stop, r):
    """German one-liner: how often the target came first among the replays that
    hit a side, against the share needed to break even at this R (1 in 1 + R)."""
    if p_target + p_stop <= 0.05:
        return "meistens passiert bis zum Abend nichts"
    good = p_target / (p_target + p_stop) > 1.0 / (1.0 + r)
    if p_target + p_stop < 0.4:
        return "meistens passiert bis zum Abend nichts. Wenn doch, dann eher %s" % (
            "gut (Ziel)" if good else "schlecht (Stop)")
    d = p_target / (p_target + p_stop) - 1.0 / (1.0 + r)
    if abs(d) < tm.COIN_FLIP:
        return "ungefähr halb-halb"
    word = "klar" if abs(d) >= tm.STRONG else "eher"
    return "%s %s" % (word, "gut (Ziel)" if d > 0 else "schlecht (Stop)")


def replay(ysym, day, entry, stop, target, entry_bar, market="us", until=None, replays=3000, seed=19):
    """-> dict with the odds and the history, or None when there is too little data.
    ysym is the Yahoo symbol (BRK-B, SAP.DE). day is YYYY-MM-DD in local time.
    entry_bar is the bar number of the entry bar (0 = the first bar of the day)."""
    _use(market)
    m = MARKETS[market]
    text = scan.yahoo_get("/v8/finance/chart/%s?range=60d&interval=5m" % ysym)
    res = tm.chart_result(text, ysym)
    now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    _, bars, _ = tm.parse_bars(res, now_utc)
    n = tm.SESSION_BARS
    sessions = _sessions(tm.split_sessions(bars), day, n)
    if len(sessions) < tm.MIN_SESSIONS:
        return None
    until_min = until if until is not None else m["close"]
    last = (until_min - m["open"]) // 5 - 1
    if last <= entry_bar:
        return None
    mc = tm.simulate(tm.build_pool(sessions), entry, stop, target, entry_bar, last, replays, seed)
    r = mc["r_at_target"]
    hist = tm.history_replay(sessions, {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid",
                                        "target_r": round(r, 2)}, last)
    return {"replays": replays, "seed": seed, "sessions": len(sessions),
            "p_target": mc["p_target"], "p_stop": mc["p_stop"], "p_neither": mc["p_neither"],
            "r": r, "avg_R": mc["avg_R"], "lean": lean(mc["p_target"], mc["p_stop"], r),
            "hist": {k: hist[k] for k in ("triggered", "target", "stop", "close")}}


def german(tmr):
    """Two lines for a push message."""
    if not tmr:
        return ["   Probe: Für diese Aktie gibt es zu wenig Kursdaten der letzten Wochen."]
    n = lambda x: int(round(100 * x))  # noqa: E731
    h = tmr["hist"]
    lines = ["   Probe: Von 100 nachgespielten Tagen kam %d-mal zuerst das Ziel, %d-mal zuerst der Stop und "
             "%d-mal keins von beiden. Einschätzung: %s." % (n(tmr["p_target"]), n(tmr["p_stop"]),
                                                          n(tmr["p_neither"]), tmr["lean"])]
    if h["triggered"]:
        lines.append("   Echt passiert: In den letzten %d Börsentagen gab es %d solche Fälle: %d× Ziel, %d× Stop, "
                     "%d× keins." % (tmr["sessions"], h["triggered"], h["target"], h["stop"], h["close"]))
    return lines
