"""Long-term growth list from SEC filings (XBRL frames, free, official).

Finds US common stocks whose net income was positive in each of the last
six calendar years and grew strongly and steadily, with growing revenue,
and checks the latest quarter against the same quarter a year earlier.

    python growth.py            # prints the top 10 as JSON
    python growth.py --top 20

The SEC wants a contact in the User-Agent: sec_user_agent in
trader/config.json or the SEC_USER_AGENT environment variable.
Facts from filings, not a recommendation.
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES = "https://data.sec.gov/api/xbrl/frames/us-gaap/%s/USD/%s.json"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
REVENUE_TAGS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax")
MIN_REVENUE = 1e9        # 1 billion $ revenue in the last year
MIN_NI_CAGR = 0.10       # net income +10 % a year or more
MIN_REV_CAGR = 0.05      # revenue +5 % a year or more
MIN_UP_YEARS = 4         # net income up in at least 4 of 5 years


def user_agent():
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        try:
            with open(os.path.join(HERE, "trader", "config.json"), encoding="utf-8") as fh:
                ua = json.load(fh).get("sec_user_agent", "").strip()
        except (OSError, ValueError):
            pass
    if not ua or "example.com" in ua:
        raise SystemExit("growth: set sec_user_agent in trader/config.json")
    return ua


def get(url, ua):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 2:
                raise
            time.sleep(3)
        finally:
            time.sleep(0.2)  # SEC allows 10 a second; stay far below


def frame(tag, period, ua):
    """{cik: value} for one tag and one period (CY2025 or CY2026Q2)."""
    doc = get(FRAMES % (tag, period), ua) or {}
    return {d["cik"]: d["val"] for d in doc.get("data", [])}


def revenue(period, ua):
    out = {}
    for tag in REVENUE_TAGS:
        for cik, v in frame(tag, period, ua).items():
            out.setdefault(cik, v)
    return out


def cagr(first, last, years):
    if first <= 0 or last <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1


def universe_symbols():
    path = os.path.join(HERE, "trader", "universe.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            return {line.split("|", 1)[0] for line in fh if line.strip()}
    except OSError:
        return None


def latest_quarters(today):
    """Quarter labels to try, newest first: the last two finished quarters."""
    q = (today.month - 1) // 3  # quarters fully finished this year
    y = today.year
    out = []
    for _ in range(3):
        if q == 0:
            y, q = y - 1, 4
        out.append((y, q))
        q -= 1
    return out


def build(top=10, today=None):
    ua = user_agent()
    today = today or datetime.date.today()
    last = today.year - 1 if today.month >= 4 else today.year - 2   # last full year mostly filed
    years = list(range(last - 5, last + 1))                          # six years
    ni = {y: frame("NetIncomeLoss", "CY%d" % y, ua) for y in years}
    rev_first, rev_last = revenue("CY%d" % years[0], ua), revenue("CY%d" % years[-1], ua)

    tick = get(TICKERS, ua) or {}
    by_cik, names = {}, {}
    allowed = universe_symbols()
    for row in tick.values():
        sym = row["ticker"].upper().replace("-", ".")
        if allowed is not None and sym not in allowed:
            continue
        by_cik.setdefault(row["cik_str"], sym)
        names.setdefault(row["cik_str"], row["title"])

    rows = []
    for cik, sym in by_cik.items():
        vals = [ni[y].get(cik) for y in years]
        if any(v is None or v <= 0 for v in vals):
            continue
        r0, r1 = rev_first.get(cik), rev_last.get(cik)
        if not r0 or not r1 or r1 < MIN_REVENUE:
            continue
        up = sum(1 for a, b in zip(vals, vals[1:]) if b > a)
        g_ni, g_rev = cagr(vals[0], vals[-1], 5), cagr(r0, r1, 5)
        if up < MIN_UP_YEARS or g_ni is None or g_ni < MIN_NI_CAGR or g_rev is None or g_rev < MIN_REV_CAGR:
            continue
        rows.append({"cik": cik, "symbol": sym, "sec_name": names[cik], "years": "%d-%d" % (years[0], years[-1]),
                     "ni_cagr": round(g_ni * 100, 1), "rev_cagr": round(g_rev * 100, 1), "up_years": up,
                     "margin_first": round(vals[0] / r0 * 100, 1), "margin_last": round(vals[-1] / r1 * 100, 1),
                     "score": min(g_ni, 0.40) + min(g_rev, 0.30) + 0.03 * up})

    # Latest quarter against the same quarter a year earlier, for the candidates.
    rows.sort(key=lambda r: -r["score"])
    cand = rows[:top * 3]
    qcache = {}
    for (y, q) in latest_quarters(today):
        now_q = frame("NetIncomeLoss", "CY%dQ%d" % (y, q), ua)
        ago_q = frame("NetIncomeLoss", "CY%dQ%d" % (y - 1, q), ua)
        for r in cand:
            if r["cik"] in qcache:
                continue
            a, b = ago_q.get(r["cik"]), now_q.get(r["cik"])
            if a is not None and b is not None:
                qcache[r["cik"]] = (y, q, a, b)
    picked = []
    for r in cand:
        hit = qcache.get(r["cik"])
        if hit:
            y, q, a, b = hit
            r["quarter"] = "Q%d %d" % (q, y)
            r["q_growth"] = round((b / a - 1) * 100, 1) if a > 0 else None
            r["q_loss"] = b <= 0
            m = q * 3 + 4   # next quarter ends 3 months later, its report comes about a month after
            r["next_report"] = "%d-%02d" % (y + (m - 1) // 12, (m - 1) % 12 + 1)
            if r["q_loss"] or (r["q_growth"] is not None and r["q_growth"] < -10):
                r["warning"] = True
        picked.append(r)
    good = [r for r in picked if not r.get("warning")]
    warn = [r for r in picked if r.get("warning")]
    for r in good + warn:
        del r["score"]
    return {"as_of": today.isoformat(), "screened": len(by_cik), "matched": len(rows),
            "rules": "net income positive 6 years, up in >= %d of 5, >= %d%%/yr; revenue >= %d%%/yr and >= 1 bn $"
                     % (MIN_UP_YEARS, MIN_NI_CAGR * 100, MIN_REV_CAGR * 100),
            "top": (good + warn)[:top]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    print(json.dumps(build(a.top), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
