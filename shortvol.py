#!/usr/bin/env python3
"""
FINRA short-sale volume vs next-day return.

Tests whether the daily short-volume RATIO (shares sold short that day / total
volume on the reporting venue) carries next-day directional information.

The ratio is NOT short interest — it does not measure open short positions. On
leveraged ETFs a large part of it is market-maker and AP hedging against
create/redeem flow, so a high reading is not a bearish vote. That is exactly why
this needs testing rather than assuming.

Two tests, both against the honest baseline (the ticker's unconditional mean):
  1. Pearson correlation of the ratio's z-score with next-day open→close return
  2. Top-vs-bottom-quintile next-day return, with a Welch t-style CI on the gap

Usage: python3 shortvol.py <sv_json> <daily_dir>
"""
import json, sys, math, statistics as st
from collections import defaultdict

def pearson(xs, ys):
    n = len(xs)
    if n < 3: return float('nan')
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x-mx)**2 for x in xs))
    dy = math.sqrt(sum((y-my)**2 for y in ys))
    return num/(dx*dy) if dx and dy else float('nan')

def corr_ci(r, n):
    """Fisher z 95% CI — the width is the point when n is only ~170."""
    if n < 4 or abs(r) >= 1: return (float('nan'),)*2
    z = 0.5*math.log((1+r)/(1-r)); se = 1/math.sqrt(n-3)
    lo, hi = z-1.96*se, z+1.96*se
    return (math.tanh(lo), math.tanh(hi))

def main():
    sv_path, daily_dir = sys.argv[1], sys.argv[2]
    rows = json.load(open(sv_path))
    sv = defaultdict(dict)
    for r in rows:
        sv[r['ticker']][r['date']] = r['shortRatio']

    print('FINRA short-volume ratio vs NEXT-day open→close return')
    print('Ratio is same-day short SELLING volume, not open short interest.\n')
    print(f"{'sym':<6}{'n':>5}{'corr':>8}{'corr 95% CI':>20}{'lowQ ret%':>11}{'highQ ret%':>12}{'gap pp':>9}{'base%':>8}")

    for sym in sorted(sv):
        try:
            bars = json.load(open(f'{daily_dir}/{sym}.json'))
        except FileNotFoundError:
            continue
        # next-day open->close return, keyed by the SIGNAL date (prior session)
        pairs = []
        for i in range(len(bars)-1):
            d = bars[i]['d']
            if d not in sv[sym]: continue
            nxt = bars[i+1]
            if not nxt['o']: continue
            ret = (nxt['c']-nxt['o'])/nxt['o']*100
            pairs.append((sv[sym][d], ret))
        if len(pairs) < 30:
            print(f'{sym:<6}{len(pairs):>5}  too few overlapping days')
            continue
        xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
        r = pearson(xs, ys); lo, hi = corr_ci(r, len(pairs))
        srt = sorted(pairs); q = max(1, len(srt)//5)
        lowq = st.mean([p[1] for p in srt[:q]])
        highq = st.mean([p[1] for p in srt[-q:]])
        base = st.mean(ys)
        print(f'{sym:<6}{len(pairs):>5}{r:>8.3f}{f"[{lo:+.2f},{hi:+.2f}]":>20}'
              f'{lowq:>11.3f}{highq:>12.3f}{highq-lowq:>9.3f}{base:>8.3f}')

    print('\nA corr CI spanning zero means no linear relationship was detected.')
    print('A quintile gap only matters if it is large next to the ticker\'s own daily noise.')

if __name__ == '__main__':
    main()
