"""Write trader/tradable.txt: the stocks Trade Republic offers that these alerts use.

Trade Republic publishes no list a program may read, and Lang & Schwarz (its
exchange) forbids automatic queries. Lang & Schwarz says it lists every stock of
the S&P 500, the Nasdaq-100 and the Dow Jones, so the alerts stay inside the
S&P 500 and the S&P MidCap 400 (constituents from Wikipedia).

Run now and then by hand:  py -3 tools/indexlist.py trader/tradable.txt
"""
import datetime
import html
import re
import sys
import urllib.request

PAGES = {
    "S&P 500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "S&P 400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
}
UA = {"User-Agent": "trading-alarm index list (personal use)"}


def tickers(url):
    page = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8")
    best = []
    # The constituents table, or else the biggest table with a ticker column.
    for table in re.findall(r"<table[^>]*wikitable.*?</table>", page, re.S):
        head = re.search(r"<tr.*?</tr>", table, re.S).group(0)
        cols = [re.sub(r"<.*?>", "", c).strip().lower() for c in re.findall(r"<th.*?</th>", head, re.S)]
        idx = next((i for i, c in enumerate(cols) if c in ("symbol", "ticker", "ticker symbol")), None)
        if idx is None:
            continue
        out = []
        for row in re.findall(r"<tr.*?</tr>", table, re.S)[1:]:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
            if len(cells) > idx:
                t = html.unescape(re.sub(r"<.*?>", "", cells[idx])).strip().upper()
                if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", t):
                    out.append(t)
        if 'id="constituents"' in table[:300]:
            return out
        if len(out) > len(best):
            best = out
    return best


def main():
    found = set()
    for name, url in PAGES.items():
        ts = tickers(url)
        print(name, len(ts), file=sys.stderr)
        if len(ts) < 90:
            raise SystemExit("too few tickers from %s: page layout changed?" % name)
        found.update(ts)
    with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Stocks Trade Republic offers: S&P 500 and S&P MidCap 400.\n")
        fh.write("# Source: Wikipedia constituent lists, read %s. One ticker a line.\n" % datetime.date.today())
        for t in sorted(found):
            fh.write(t + "\n")
    print("total", len(found), file=sys.stderr)


if __name__ == "__main__":
    main()
