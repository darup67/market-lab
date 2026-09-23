#!/usr/bin/env python3
"""
Hypothesis tests over the recorded 15-minute windows.

Every test here answers a question that has to be true before an idea is
tradeable. None of them is a strategy, and a passing test is not an edge —
fees, slippage and the fact that you cannot trade the open print all sit
between a statistic and a P&L.

Time-based questions read `minute_quotes` (clock-aligned). The legacy `first`,
`range` and `path` fields are built from a truncated, trade-indexed sample and
are shown only for comparison.

    python3 analyze.py              all series
    python3 analyze.py KXBTC15M     one series
"""
import json, sys, glob, os, statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

def load(series=None):
    rows = []
    for f in sorted(glob.glob(os.path.join(HERE, "data", "*.jsonl"))):
        s = os.path.basename(f)[:-6]
        if series and s != series:
            continue
        for line in open(f):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue          # a torn append is one row, not a dead run
            if r.get("settled_yes") is not None:
                rows.append(r)
    rows.sort(key=lambda r: (r["series"], r.get("window") or ""))
    return rows

MAX_SPREAD = 10   # c; a wider quote at minute 1 is an empty or one-sided book, not a price

def opening(r):
    """Clock-aligned opening print: YES bid/ask mid at the end of minute 1, cents.

    NOT r["first"]: that is the oldest of the newest 10,000 trades, and every
    BTC window hits that cap, so it is a mid-window price (found 2026-09-23).
    """
    q = (r.get("minute_quotes") or [None, None])[1]
    if not q or q[1] - q[0] > MAX_SPREAD:
        return None
    return (q[0] + q[1]) / 2


def clock_range(r):
    """High-low of the per-minute mids, minutes 1-15, cents. None if too sparse."""
    mids = [(q[0] + q[1]) / 2 for q in (r.get("minute_quotes") or [])[1:]
            if q and q[1] - q[0] <= MAX_SPREAD]
    return max(mids) - min(mids) if len(mids) >= 10 else None


def pearson(pairs):
    if len(pairs) < 10:
        return None
    xs = [a for a, _ in pairs]; ys = [b for _, b in pairs]
    mx, my = st.mean(xs), st.mean(ys)
    den = (sum((x-mx)**2 for x in xs) * sum((y-my)**2 for y in ys)) ** 0.5
    return None if den == 0 else sum((x-mx)*(y-my) for x, y in pairs) / den

def wilson(k, n):
    """95% CI for a proportion. Small samples lie confidently without one."""
    if n == 0:
        return (0.0, 1.0)
    p, z = k / n, 1.96
    d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    h = z * ((p*(1-p)/n + z*z/(4*n*n)) ** 0.5) / d
    return (max(0, c-h), min(1, c+h))

def hdr(t):
    print(f"\n{t}\n" + "-" * len(t))

def report(rows, series):
    n = len(rows)
    print(f"\n{'='*66}\n{series}   {n} settled windows\n{'='*66}")
    if n < 20:
        print("  too few windows to say anything — keep recording")
        return

    yes = sum(1 for r in rows if r["settled_yes"])
    lo, hi = wilson(yes, n)
    hdr("Base rate")
    print(f"  settled YES {yes}/{n} = {yes/n*100:.1f}%   95% CI [{lo*100:.0f}%, {hi*100:.0f}%]")
    print("  a market this liquid should sit near 50% — a persistent skew would")
    print("  be the first thing worth understanding")

    op = [(r, opening(r)) for r in rows]
    op = [(r, o) for r, o in op if o is not None]
    hdr("Calibration — does the opening print mean what it says")
    print(f"  opening print = YES mid at minute 1 (clock-aligned); {len(op)}/{n} windows have one")
    print(f"  {'minute-1 mid':<14}{'n':>5}{'settled YES':>13}{'95% CI':>18}")
    b = defaultdict(list)
    for r, o in op:
        b[min(9, int(o // 10))].append(r["settled_yes"])
    for k in sorted(b):
        g = b[k]; y = sum(g)
        l, h = wilson(y, len(g))
        print(f"  {k*10:>3}-{k*10+9:<10}{len(g):>5}{y/len(g)*100:>12.0f}%   [{l*100:>3.0f}%, {h*100:>3.0f}%]")

    hdr("How often the opening print is wrong")
    ties = [r for r, o in op if o == 50]
    dec = [(r, o) for r, o in op if o != 50]
    wrong = [r for r, o in dec if (o > 50) != r["settled_yes"]]
    if dec:
        l, h = wilson(len(wrong), len(dec))
        print(f"  overall            {len(wrong):>4}/{len(dec)} = {len(wrong)/len(dec)*100:.0f}%   [{l*100:.0f}%, {h*100:.0f}%]"
              f"   ({len(ties)} at exactly 50c excluded)")
    for lbl, sel in (("opens <10c or >90c", lambda o: o < 10 or o > 90),
                     ("opens 35-65c",       lambda o: 35 <= o <= 65)):
        g = [(r, o) for r, o in dec if sel(o)]
        if not g:
            continue
        w = sum(1 for r, o in g if (o > 50) != r["settled_yes"])
        l, h = wilson(w, len(g))
        print(f"  {lbl:<19}{w:>4}/{len(g)} = {w/len(g)*100:.0f}%   [{l*100:.0f}%, {h*100:.0f}%]")
    legacy = sum(1 for r in rows if (r["first"] > 50) != r["settled_yes"])
    print(f"  legacy `first` (trade-truncated, mid-window): wrong {legacy}/{n} = {legacy/n*100:.0f}% — do not use")

    hdr("Volatility persistence — the assumption a vol filter depends on")
    W = 4
    legacy = [(sum(r["range"] for r in rows[i-W:i]) / W, rows[i]["range"]) for i in range(W, len(rows))]
    cr = [clock_range(r) for r in rows]
    pairs = [(sum(cr[i-W:i]) / W, cr[i]) for i in range(W, len(rows))
             if all(x is not None for x in cr[i-W:i + 1])]
    c, cl = pearson(pairs), pearson(legacy)
    print(f"  corr(prior {W}-window mean range, next range) = "
          f"{'n/a' if c is None else f'{c:+.2f}'}   n={len(pairs)}   (range of clock-aligned minute mids)")
    print(f"  legacy trade-truncated `range`: {'n/a' if cl is None else f'{cl:+.2f}'} — do not use")
    print("  near zero means a calm hour tells you little about the next window,")
    print("  which is what a LOW/NORMAL/HIGH filter would have to rely on")
    for lbl, sel in (("after LOW  (<28c)", lambda x: x < 28),
                     ("after NORM (28-52)", lambda x: 28 <= x <= 52),
                     ("after HIGH (>52c)", lambda x: x > 52)):
        g = [y for x, y in pairs if sel(x)]
        if g:
            print(f"    {lbl:<20} next range mean {st.mean(g):5.1f}c   n={len(g)}")

    hdr("Where the uncertainty lives")
    decided = [o for _, o in op if o < 10 or o > 90]
    coin    = [o for _, o in op if 35 <= o <= 65]
    m = max(len(op), 1)
    print(f"  decided by minute 1 (<10c/>90c)   {len(decided):>4}/{len(op)} = {len(decided)/m*100:.0f}%")
    print(f"  genuinely uncertain (35-65c)      {len(coin):>4}/{len(op)} = {len(coin)/m*100:.0f}%")
    crs = [x for x in cr if x is not None]
    if crs:
        print(f"  mean intra-window range           {st.mean(crs):.1f}c   (clock-aligned mids)")
        print(f"  median                            {st.median(crs):.1f}c")

def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    rows = load(only)
    if not rows:
        print("no data yet — run: node record.js")
        return
    by = defaultdict(list)
    for r in rows:
        by[r["series"]].append(r)
    for s in sorted(by):
        report(by[s], s)
    print(f"\n{'='*66}")
    print("A passing statistic is not an edge. Kalshi charges fees, you cannot")
    print("trade the opening print, and every number above is in-sample.")
    print(f"{'='*66}")

if __name__ == "__main__":
    main()
