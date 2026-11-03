"""Write the stock lists the alerts use, from Wikipedia's index pages.

  trader/tradable.txt  US: S&P 500 and S&P MidCap 400.   TICKER|sector
  trader/europe.txt    Europe: DAX, MDAX, EURO STOXX 50.  SYMBOL|name|sector|index

Trade Republic publishes no list a program may read, and Lang & Schwarz (its
exchange) forbids automatic queries. These indexes are large, well-known
companies that Trade Republic offers.

Run now and then by hand:  py -3 tools/indexlist.py
"""
import datetime
import html
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "trading-alarm index list (personal use)"}
US_PAGES = {
    "S&P 500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "S&P 400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
}
EU_PAGES = {   # name: (url, suffix added to a bare ticker)
    "DAX": ("https://en.wikipedia.org/wiki/DAX", ".DE"),
    "MDAX": ("https://en.wikipedia.org/wiki/MDAX", ".DE"),
    "EURO STOXX 50": ("https://en.wikipedia.org/wiki/EURO_STOXX_50", ""),
}
TICKER_COLS = ("symbol", "ticker", "ticker symbol")
NAME_COLS = ("security", "company", "name")
SECTOR_COLS = ("gics sector", "sector", "prime standard sector", "industry")


def text(cell):
    return html.unescape(re.sub(r"<.*?>", "", cell)).replace("\xa0", " ").strip()


def tables(url):
    page = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8")
    for table in re.findall(r"<table[^>]*wikitable.*?</table>", page, re.S):
        rows = re.findall(r"<tr.*?</tr>", table, re.S)
        if len(rows) < 20:
            continue
        cols = [text(c).lower() for c in re.findall(r"<th.*?</th>", rows[0], re.S)]
        body = [[text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)] for r in rows[1:]]
        yield cols, body


def col(cols, wanted):
    return next((i for i, c in enumerate(cols) if c in wanted), None)


def read_index(url, pattern, suffix=""):
    """[(ticker, name, sector)] from the biggest table with a ticker column."""
    best = []
    for cols, body in tables(url):
        it, iname, isec = col(cols, TICKER_COLS), col(cols, NAME_COLS), col(cols, SECTOR_COLS)
        if it is None:
            continue
        out = []
        for cells in body:
            if len(cells) != len(cols):
                continue
            t = cells[it].upper().split()[0] if cells[it] else ""
            if suffix and "." not in t:
                t += suffix
            if re.fullmatch(pattern, t):
                out.append((t, cells[iname] if iname is not None else "",
                            cells[isec] if isec is not None else ""))
        if len(out) > len(best):
            best = out
    return best


def main():
    today = datetime.date.today()
    us = {}
    for name, url in US_PAGES.items():
        rows = read_index(url, r"[A-Z]{1,5}(\.[A-Z])?")
        print(name, len(rows), file=sys.stderr)
        if len(rows) < 300:
            raise SystemExit("too few tickers from %s: page layout changed?" % name)
        for t, _, sec in rows:
            us.setdefault(t, sec)
    eu = {}
    for name, (url, suffix) in EU_PAGES.items():
        rows = read_index(url, r"[A-Z0-9]{1,6}\.[A-Z]{1,3}", suffix)
        print(name, len(rows), file=sys.stderr)
        if len(rows) < 30:
            raise SystemExit("too few tickers from %s: page layout changed?" % name)
        for t, nm, sec in rows:
            eu.setdefault(t, (nm, sec, name))
    with open(os.path.join(HERE, "trader", "tradable.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# US stocks Trade Republic offers: S&P 500 and S&P MidCap 400. TICKER|sector\n")
        fh.write("# Source: Wikipedia constituent lists, read %s. Rebuild: py -3 tools/indexlist.py\n" % today)
        for t in sorted(us):
            fh.write("%s|%s\n" % (t, us[t]))
    with open(os.path.join(HERE, "trader", "europe.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# European stocks Trade Republic offers: DAX, MDAX, EURO STOXX 50. SYMBOL|name|sector|index\n")
        fh.write("# Source: Wikipedia, read %s. Rebuild: py -3 tools/indexlist.py\n" % today)
        for t in sorted(eu):
            fh.write("%s|%s|%s|%s\n" % ((t,) + eu[t]))
    print("US %d, Europe %d" % (len(us), len(eu)), file=sys.stderr)


if __name__ == "__main__":
    main()
