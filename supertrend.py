#!/usr/bin/env python3
"""
SuperTrend flip validation — faithful Pine v6 port + forward-return harness.

Ports the LuxAlgo Library "SuperTrend" (luxalgo.com/library/indicator/supertrend/)
line-for-line, then asks one question: does a SuperTrend flip predict anything
that a randomly chosen bar does not?

The honest baseline is the UNCONDITIONAL forward return over the same bars, not
a random walk. If flip returns land inside the baseline's spread, the signal is
noise dressed as a rule.

Usage:  python3 supertrend.py <bars_dir> [--atr 10] [--factor 3.0] [--sweep]
Bars:   one <SYM>.jsonl per symbol, rows {"t":unix,"o":,"h":,"l":,"c":,"v":}
"""
import json, sys, glob, os, math, argparse
from statistics import mean, stdev

# ---------------------------------------------------------------- Pine port

def true_range(h, l, c_prev):
    """Pine ta.tr — on the first bar (no prior close) it degrades to high-low."""
    if c_prev is None:
        return h - l
    return max(h - l, abs(h - c_prev), abs(l - c_prev))

def rma(vals, length):
    """Pine ta.rma (Wilder). Seeded with the SMA of the first `length` values;
    na before that, which is why the seed index is length-1, not 0."""
    out = [None] * len(vals)
    if len(vals) < length:
        return out
    seed = sum(vals[:length]) / length
    out[length - 1] = seed
    prev = seed
    for i in range(length, len(vals)):
        prev = (prev * (length - 1) + vals[i]) / length
        out[i] = prev
    return out

TREND_UP, TREND_DN = 1, -1

def supertrend(bars, atr_length=10, factor=3.0):
    """Returns per-bar dicts with trend dir, the trailing stop, and flip flags.
    Mirrors the Pine exactly: bands ratchet only while the PRIOR close holds
    inside them, and trendDir persists when price sits between the bands."""
    trs = []
    for i, b in enumerate(bars):
        trs.append(true_range(b['h'], b['l'], bars[i-1]['c'] if i > 0 else None))
    atrs = rma(trs, atr_length)

    final_upper = final_lower = None
    trend_dir = TREND_DN          # Pine: var int trendDir = TREND_DN
    out = []
    for i, b in enumerate(bars):
        atr = atrs[i]
        hl2 = (b['h'] + b['l']) / 2
        prev_dir = trend_dir
        if atr is not None:
            basic_upper = hl2 + factor * atr
            basic_lower = hl2 - factor * atr
            prev_upper = final_upper if final_upper is not None else basic_upper
            prev_lower = final_lower if final_lower is not None else basic_lower
            prev_close = bars[i-1]['c'] if i > 0 else b['c']

            final_upper = min(basic_upper, prev_upper) if prev_close <= prev_upper else basic_upper
            final_lower = max(basic_lower, prev_lower) if prev_close >= prev_lower else basic_lower

            if b['c'] > final_upper:
                trend_dir = TREND_UP
            elif b['c'] < final_lower:
                trend_dir = TREND_DN

        st = (final_lower if trend_dir == TREND_UP else final_upper) if atr is not None else None
        out.append({
            **b,
            'atr': atr, 'st': st, 'dir': trend_dir,
            'flip_up': atr is not None and trend_dir == TREND_UP and prev_dir == TREND_DN,
            'flip_dn': atr is not None and trend_dir == TREND_DN and prev_dir == TREND_UP,
            'warm': atr is not None,
        })
    return out

# ------------------------------------------------------------- statistics

def fwd_return(bars, entry_i, horizon):
    """Enter at the NEXT bar's open (no same-bar-close lookahead), exit at the
    close `horizon` bars later. Returns percent, or None if it runs off the end."""
    e = entry_i + 1
    x = e + horizon - 1
    if x >= len(bars):
        return None
    entry, exit_ = bars[e]['o'], bars[x]['c']
    if not entry:
        return None
    return (exit_ - entry) / entry * 100.0

def ci95(xs):
    """Normal-approx 95% CI on the mean. Wide CIs here are the point, not a flaw."""
    n = len(xs)
    if n < 2:
        return (float('nan'), float('nan'))
    se = stdev(xs) / math.sqrt(n)
    m = mean(xs)
    return (m - 1.96 * se, m + 1.96 * se)

def analyse(bars, horizons):
    warm = [b for b in bars if b['warm']]
    idx = {id(b): i for i, b in enumerate(bars)}
    res = {'n_bars': len(warm), 'flips_up': 0, 'flips_dn': 0, 'horizons': {}}
    res['flips_up'] = sum(1 for b in warm if b['flip_up'])
    res['flips_dn'] = sum(1 for b in warm if b['flip_dn'])

    for h in horizons:
        up, dn, base = [], [], []
        for b in warm:
            i = idx[id(b)]
            r = fwd_return(bars, i, h)
            if r is None:
                continue
            base.append(r)
            if b['flip_up']:
                up.append(r)
            elif b['flip_dn']:
                dn.append(-r)      # short: sign-flip so ">0 = correct"
        entry = {}
        for name, xs in (('flip_up', up), ('flip_dn_short', dn), ('baseline_all_bars', base)):
            if xs:
                lo, hi = ci95(xs)
                entry[name] = {
                    'n': len(xs), 'mean_pct': mean(xs), 'ci95': (lo, hi),
                    'hit_rate': sum(1 for x in xs if x > 0) / len(xs),
                }
            else:
                entry[name] = {'n': 0}
        res['horizons'][h] = entry
    return res

# ------------------------------------------------------------------ report

def fmt(e):
    if not e.get('n'):
        return 'n=0'
    lo, hi = e['ci95']
    return (f"n={e['n']:<4} mean={e['mean_pct']:+.3f}%  "
            f"CI[{lo:+.3f},{hi:+.3f}]  hit={e['hit_rate']*100:.0f}%")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('bars_dir')
    ap.add_argument('--atr', type=int, default=10)
    ap.add_argument('--factor', type=float, default=3.0)
    ap.add_argument('--horizons', default='1,2,4,8,13')
    ap.add_argument('--sweep', action='store_true')
    a = ap.parse_args()
    horizons = [int(x) for x in a.horizons.split(',')]

    files = sorted(glob.glob(os.path.join(a.bars_dir, '*.jsonl')))
    if not files:
        sys.exit(f'no .jsonl in {a.bars_dir}')

    if a.sweep:
        print(f'SENSITIVITY SWEEP — horizon {horizons[0]} bars')
        print('Do NOT read the max as "the best setting" — with this little data '
              'the max is where the noise happens to peak.\n')
        print(f"{'symbol':<8} {'atr':>4} {'fac':>5} {'flips':>6} {'up mean%':>10} {'short mean%':>12} {'base mean%':>11}")
        for f in files:
            sym = os.path.basename(f).replace('.jsonl', '')
            bars = [json.loads(l) for l in open(f) if l.strip()]
            for at in (7, 10, 14, 21):
                for fac in (1.5, 2.0, 3.0, 4.0):
                    r = analyse(supertrend(bars, at, fac), [horizons[0]])
                    e = r['horizons'][horizons[0]]
                    u = e['flip_up'].get('mean_pct'); d = e['flip_dn_short'].get('mean_pct')
                    b = e['baseline_all_bars'].get('mean_pct')
                    print(f"{sym:<8} {at:>4} {fac:>5.1f} {r['flips_up']+r['flips_dn']:>6} "
                          f"{(f'{u:+.3f}' if u is not None else '—'):>10} "
                          f"{(f'{d:+.3f}' if d is not None else '—'):>12} "
                          f"{(f'{b:+.3f}' if b is not None else '—'):>11}")
        return

    print(f'SuperTrend flip validation — ATR({a.atr}), factor {a.factor}')
    print('Entry = next bar open, exit = close H bars later. Short returns sign-flipped '
          '(>0 means the signal was right).\n')
    for f in files:
        sym = os.path.basename(f).replace('.jsonl', '')
        bars = [json.loads(l) for l in open(f) if l.strip()]
        r = analyse(supertrend(bars, a.atr, a.factor), horizons)
        print(f"── {sym}   {r['n_bars']} warm bars   "
              f"{r['flips_up']} up-flips / {r['flips_dn']} down-flips")
        for h in horizons:
            e = r['horizons'][h]
            print(f"   H={h:<3} up    {fmt(e['flip_up'])}")
            print(f"        short {fmt(e['flip_dn_short'])}")
            print(f"        base  {fmt(e['baseline_all_bars'])}")
        print()

if __name__ == '__main__':
    main()
