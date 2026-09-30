#!/usr/bin/env python3
"""
Sector breadth research: which stock-market sectors have more green (up) days than red (down) days, on average.

RESEARCH ONLY. No trade signals, no orders, nothing here is wired to execution.
NOTHING IS STORED: prices are fetched from Yahoo into memory on every run, the statistics are computed, and the
result goes to the screen or an email. No cache, no data files. (Only launchd's stderr, which stays empty unless a run fails.)

    python3 sector_breadth.py                print the report
    python3 sector_breadth.py --email        send it (subject "Sector research · ...")
    python3 sector_breadth.py --email --test prefix the subject with [TEST]

Universe: the 11 GICS sectors (SPDR Select Sector ETFs) as the primary table, plus a secondary table of industry groups.
Benchmark: SPY. Definitions:
  green day  close > previous close      red day  close < previous close   (unchanged days are dropped)
  candle     close > open (intraday direction), shown separately
  p-value    two-sided test of "green share = 50%" (normal approximation), so a 55% share over 60 days is not over-read
"""
import json, math, os, subprocess, sys, time, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.expanduser("~/flip-notifier"))
import email_ui

SECTORS = [("XLK", "Technology"), ("XLF", "Financials"), ("XLV", "Health Care"), ("XLE", "Energy"),
           ("XLY", "Consumer Discretionary"), ("XLP", "Consumer Staples"), ("XLI", "Industrials"),
           ("XLU", "Utilities"), ("XLB", "Materials"), ("XLRE", "Real Estate"), ("XLC", "Communication Services")]
INDUSTRIES = [("SMH", "Semiconductors"), ("IGV", "Software"), ("XBI", "Biotech"), ("IBB", "Biotech (large cap)"),
              ("KRE", "Regional Banks"), ("KBE", "Banks"), ("XHB", "Homebuilders"), ("XRT", "Retail"),
              ("ITA", "Aerospace & Defense"), ("XOP", "Oil & Gas E&P"), ("OIH", "Oil Services"), ("JETS", "Airlines"),
              ("XME", "Metals & Mining"), ("IYT", "Transportation"), ("IHI", "Medical Devices"), ("GDX", "Gold Miners")]
BENCH = "SPY"
WINDOWS = [("5 years", None), ("1 year", 252), ("6 months", 126), ("3 months", 63)]


def fetch(ticker, tries=3):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=5y" % ticker
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            r = json.load(urllib.request.urlopen(req, timeout=20))["chart"]["result"][0]
            q = r["indicators"]["quote"][0]
            rows = []
            for t, o, c in zip(r["timestamp"], q["open"], q["close"]):
                if o is None or c is None:
                    continue
                rows.append((datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), o, c))
            return rows
        except Exception:
            time.sleep(1.5 * (a + 1))
    return None


def drop_partial_today(rows):
    """Yahoo includes the in-progress session; a research stat must use completed days only."""
    now = datetime.now(timezone.utc)
    if rows and rows[-1][0] == now.strftime("%Y-%m-%d") and now.hour * 60 + now.minute < 21 * 60 + 15:   # before ~16:15 ET
        return rows[:-1]
    return rows


def pval(g, n):
    if n == 0:
        return 1.0
    z = (abs(g - n / 2.0) - 0.5) / math.sqrt(n * 0.25)
    return math.erfc(max(z, 0) / math.sqrt(2))


def stats(rows, spy_red_dates=None):
    """rows: [(date, open, close)] ascending. Returns the breadth statistics for exactly these rows."""
    ups, dns, candle_up, candle_n = [], [], 0, 0
    g_on_spy_red = n_on_spy_red = 0
    for i in range(1, len(rows)):
        d, o, c = rows[i]
        ret = c / rows[i - 1][2] - 1
        if ret > 0:
            ups.append(ret)
        elif ret < 0:
            dns.append(ret)
        if spy_red_dates is not None and d in spy_red_dates and ret != 0:
            n_on_spy_red += 1
            g_on_spy_red += ret > 0
        if c != o:
            candle_n += 1
            candle_up += c > o
    n = len(ups) + len(dns)
    if n == 0:
        return None
    mean = (sum(ups) + sum(dns)) / n
    avg_up = sum(ups) / len(ups) if ups else 0.0
    avg_dn = sum(dns) / len(dns) if dns else 0.0
    return {"n": n, "green": len(ups) / n, "avg_up": avg_up, "avg_dn": avg_dn,
            "size_ratio": (avg_up / abs(avg_dn)) if avg_dn else float("nan"), "mean": mean,
            "p": pval(len(ups), n), "candle": (candle_up / candle_n) if candle_n else float("nan"),
            "g_spy_red": (g_on_spy_red / n_on_spy_red) if n_on_spy_red else float("nan"), "n_spy_red": n_on_spy_red}


def window(rows, n):
    return rows if n is None else rows[-(n + 1):]          # n returns need n+1 closes


def analyse(universe, data, spy):
    spy_ret = {spy[i][0]: spy[i][2] / spy[i - 1][2] - 1 for i in range(1, len(spy))}
    out = {}
    for tk, name in universe:
        rows = data.get(tk)
        if not rows or len(rows) < 100:
            continue
        res = {}
        for label, n in WINDOWS:
            w = window(rows, n)
            red_dates = set(d for d, _, _ in w if spy_ret.get(d, 0) < 0)
            res[label] = stats(w, red_dates)
        # rank a week ago, from the same in-memory data (no stored history needed)
        res["prev"] = stats(rows[:-5])
        out[tk] = {"name": name, "res": res, "first": rows[0][0], "last": rows[-1][0]}
    return out


def rank(table, key):
    order = sorted(table, key=lambda t: -table[t][key])
    return {t: i + 1 for i, t in enumerate(order)}


def pct(x, d=1):
    return "—" if x is None or x != x else "%.*f%%" % (d, x * 100)


def sgn(x, d=2):
    return "—" if x is None or x != x else "%+.*f%%" % (d, x * 100)


def pfmt(p):
    return "<0.001" if p < 0.001 else "%.3f" % p


def tone_for(green, p):
    if p < 0.05:
        return "good" if green > 0.5 else "bad"
    return None


def build(universe_res, industry_res, spy_res):
    five = {t: v["res"]["5 years"] for t, v in universe_res.items() if v["res"]["5 years"]}
    base = spy_res["res"]["5 years"]
    order = sorted(five, key=lambda t: -five[t]["green"])
    first = min(v["first"] for v in universe_res.values())
    last = max(v["last"] for v in universe_res.values())
    sig = [t for t in order if five[t]["p"] < 0.05 and five[t]["green"] > 0.5]

    kpis = [
        {"label": "Most green days (5y)", "value": "%s %s" % (universe_res[order[0]]["name"], pct(five[order[0]]["green"])), "tone": "good"},
        {"label": "Fewest green days (5y)", "value": "%s %s" % (universe_res[order[-1]]["name"], pct(five[order[-1]]["green"])), "tone": "warn"},
        {"label": "S&P 500 (SPY) baseline", "value": pct(base["green"]), "sub": "%d up/down days" % base["n"]},
        {"label": "Sectors clearly above 50%", "value": "%d of %d" % (len(sig), len(five)), "sub": "p < 0.05"},
    ]

    def row5(t):
        s = five[t]
        return {"sector": {"v": "%s (%s)" % (universe_res[t]["name"], t), "bold": True},
                "green": {"v": pct(s["green"]), "tone": tone_for(s["green"], s["p"]), "bold": True},
                "up": sgn(s["avg_up"]), "dn": sgn(s["avg_dn"]), "ratio": "%.2f" % s["size_ratio"],
                "mean": sgn(s["mean"], 3), "vs": "%+.1f pp" % ((s["green"] - base["green"]) * 100), "p": pfmt(s["p"])}
    t5 = {"type": "table", "columns": [
        {"key": "sector", "label": "Sector"}, {"key": "green", "label": "Green days", "align": "right"},
        {"key": "up", "label": "Avg green day", "align": "right"}, {"key": "dn", "label": "Avg red day", "align": "right"},
        {"key": "ratio", "label": "Green/red size", "align": "right"}, {"key": "mean", "label": "Avg daily", "align": "right"},
        {"key": "vs", "label": "vs SPY", "align": "right"}, {"key": "p", "label": "p vs 50%", "align": "right"}],
        "rows": [row5(t) for t in order]}

    # recent windows, with rank change from a week ago on the 5y measure
    r_now = rank(five, "green")
    prev = {t: universe_res[t]["res"]["prev"] for t in five if universe_res[t]["res"]["prev"]}
    prev_g = {t: prev[t] for t in prev}
    r_prev = rank({t: {"green": prev_g[t]["green"]} for t in prev_g}, "green")
    recent_rows = []
    for t in order:
        res = universe_res[t]["res"]
        d = r_prev.get(t, r_now[t]) - r_now[t]
        recent_rows.append({"sector": {"v": universe_res[t]["name"], "bold": True},
                            "y1": pct(res["1 year"]["green"]) if res["1 year"] else "—",
                            "m6": pct(res["6 months"]["green"]) if res["6 months"] else "—",
                            "m3": pct(res["3 months"]["green"]) if res["3 months"] else "—",
                            "rk": "#%d" % r_now[t], "mv": ("▲%d" % d if d > 0 else "▼%d" % -d if d < 0 else "—")})
    recent = {"type": "table", "columns": [
        {"key": "sector", "label": "Sector"}, {"key": "y1", "label": "1 year", "align": "right"},
        {"key": "m6", "label": "6 months", "align": "right"}, {"key": "m3", "label": "3 months", "align": "right"},
        {"key": "rk", "label": "5y rank", "align": "right"}, {"key": "mv", "label": "vs last week", "align": "right"}],
        "rows": recent_rows}

    lens_rows = [{"sector": {"v": universe_res[t]["name"], "bold": True},
                  "candle": pct(five[t]["candle"]),
                  "sr": "%s (%d days)" % (pct(five[t]["g_spy_red"]), five[t]["n_spy_red"])} for t in order]
    lens = {"type": "table", "columns": [
        {"key": "sector", "label": "Sector"}, {"key": "candle", "label": "Green candle (close>open)", "align": "right"},
        {"key": "sr", "label": "Up on days SPY fell", "align": "right"}], "rows": lens_rows}

    ind5 = {t: v["res"]["5 years"] for t, v in industry_res.items() if v["res"]["5 years"]}
    iorder = sorted(ind5, key=lambda t: -ind5[t]["green"])
    ind = {"type": "table", "columns": [
        {"key": "g", "label": "Industry group"}, {"key": "green", "label": "Green days (5y)", "align": "right"},
        {"key": "m3", "label": "3 months", "align": "right"}, {"key": "p", "label": "p vs 50%", "align": "right"}],
        "rows": [{"g": {"v": "%s (%s)" % (industry_res[t]["name"], t), "bold": True},
                  "green": {"v": pct(ind5[t]["green"]), "tone": tone_for(ind5[t]["green"], ind5[t]["p"]), "bold": True},
                  "m3": pct(industry_res[t]["res"]["3 months"]["green"]) if industry_res[t]["res"]["3 months"] else "—",
                  "p": pfmt(ind5[t]["p"])} for t in iorder]}

    top = universe_res[order[0]]["name"]
    spread = (five[order[0]]["green"] - five[order[-1]]["green"]) * 100
    notes = [
        "Green day = close above the prior close; red = below; unchanged days are dropped. Green/red size = average green-day move divided by average red-day move.",
        "Window: %s to %s (about 5 years of completed trading days). Sector = the 11 SPDR Select Sector ETFs; SPY is the benchmark." % (first, last),
        "Stocks tend to drift upward, so sectors sit above 50%% green. The useful comparison is between sectors: the spread here is %.1f points from first to last." % spread,
        "A sector can have fewer green days but bigger green moves; the size ratio and average daily return show that. Count alone is not performance.",
        "p vs 50% tests whether a sector's green share differs from a coin flip. Short windows (3 and 6 months) are too small to separate sectors; treat them as context.",
        "Sectors move together, so the eleven results are not eleven independent findings. XLRE (from Oct 2015) and XLC (from Jun 2018) have shorter histories than the five-year window they are trimmed to.",
    ]
    blocks = [
        ("What this shows", [{"type": "callout", "tone": "info",
                              "text": "Research only. No trade signals, no orders. It counts how often each sector closes up versus down, and how big those days are."},
                             {"type": "kpis", "items": kpis}]),
        ("Five-year ranking: green vs red days by sector", [t5]),
        ("Recent windows and rank change", [recent]),
        ("Other lenses: candle direction and behavior when the market falls", [lens]),
        ("Industry groups (five years)", [ind]),
        ("How to read this", [{"type": "list", "items": notes}]),
    ]
    return {"title": "Sector Research: Which Sectors Have More Green Days Than Red",
            "subtitle": "Five-year history, refreshed weekly · research only, no trading signals",
            "sections": [{"title": t, "blocks": b} for t, b in blocks]}, order, five


def text_report(spec, order, five, universe_res):
    L = ["SECTOR RESEARCH: green vs red days (5y through %s)" % max(v["last"] for v in universe_res.values()), ""]
    L.append("%-26s %7s %9s %9s %6s %8s %7s" % ("Sector", "Green", "AvgGreen", "AvgRed", "Size", "vs SPY", "p"))
    base = None
    for t in order:
        s = five[t]
        L.append("%-26s %7s %9s %9s %6.2f %8s %7s" % ("%s (%s)" % (universe_res[t]["name"], t), pct(s["green"]), sgn(s["avg_up"]),
                                                      sgn(s["avg_dn"]), s["size_ratio"], "", pfmt(s["p"])))
    return "\n".join(L)


def main():
    send = "--email" in sys.argv
    tickers = [t for t, _ in SECTORS + INDUSTRIES] + [BENCH]
    data = {}
    for t in tickers:
        rows = fetch(t)
        if rows:
            data[t] = drop_partial_today(rows)
        time.sleep(0.25)
    if BENCH not in data:
        print("SPY fetch failed; aborting (nothing stored)", file=sys.stderr)
        sys.exit(1)
    spy = data[BENCH]
    sec = analyse(SECTORS, data, spy)
    ind = analyse(INDUSTRIES, data, spy)
    spy_res = analyse([(BENCH, "S&P 500")], data, spy)[BENCH]
    if len(sec) < 8:
        print("only %d sectors fetched; aborting" % len(sec), file=sys.stderr)
        sys.exit(1)
    spec, order, five = build(sec, ind, spy_res)
    print(text_report(spec, order, five, sec))
    if send:
        top = sec[order[0]]["name"]
        subject = ("[TEST] " if "--test" in sys.argv else "") + "Sector research · green vs red days by sector: %s leads at %s" % (top, pct(five[order[0]]["green"]))
        ok = email_ui.send(subject, spec)
        print("email sent" if ok else "email FAILED")
        sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
