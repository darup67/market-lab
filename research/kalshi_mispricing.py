#!/usr/bin/env python3
"""Kalshi 15-minute crypto: does the market misprice vs. a spot-and-volatility fair value? (2026-09-28)

For every recorded window (market-lab data/KX*15M.jsonl) and every minute with a real two-sided quote:
  fair P(yes) = Phi( ln(spot / strike) / (sigma_1m * sqrt(minutes_left)) )
  sigma_1m    = realized std of 1-minute log returns over the previous 60 minutes (Binance 1m bars)
Trade rule: the first minute in a window where fair value beats the ask (YES) or 1 - bid (NO) by more
than theta after Kalshi's taker fee -> buy 1 contract there; hold to settlement. One trade per window.
Reported per theta and per half of history (older / newer), so a lucky stretch can't pass as an edge.
Caveats: settlement uses CF Benchmarks' averaged index, not Binance's last trade; fills assume the
recorded ask was available for 1 contract; minute_quotes are snapshots.
"""
import calendar, json, math, os, statistics as st, time
from bisect import bisect_right
import requests

DATA = os.path.expanduser("~/market-lab/data")
CACHE = os.path.expanduser("~/market-lab/research/cache")
COINS = {"KXBTC15M": "BTCUSDT", "KXETH15M": "ETHUSDT", "KXSOL15M": "SOLUSDT", "KXXRP15M": "XRPUSDT"}
fee = lambda p: math.ceil(0.07 * p * (1 - p) * 100) / 100
Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))


def minutes(sym, start_ms, end_ms):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{sym}-1m.json")
    have = json.load(open(path)) if os.path.exists(path) else []
    t = have[-1][0] + 60000 if have else start_ms
    s = requests.Session()
    while t < end_ms:
        k = s.get("https://data-api.binance.vision/api/v3/klines", params={"symbol": sym, "interval": "1m", "startTime": t, "limit": 1000}, timeout=20).json()
        if not k: break
        have += [[x[0], float(x[4])] for x in k]
        t = k[-1][0] + 60000
    json.dump(have, open(path, "w"))
    return have


def main():
    rows = {}
    for series in COINS:
        rows[series] = [json.loads(l) for l in open(os.path.join(DATA, f"{series}.jsonl"))]
    t_all = [calendar.timegm(time.strptime(r["open_time"], "%Y-%m-%dT%H:%M:%SZ")) * 1000 for rs in rows.values() for r in rs]
    lo, hi = min(t_all) - 3 * 3600e3, max(t_all) + 20 * 60e3
    trades = {th: [] for th in (0.02, 0.05, 0.10, 0.15)}
    checked = 0
    for series, sym in COINS.items():
        m = minutes(sym, int(lo), int(hi))
        ts = [x[0] for x in m]; px = [x[1] for x in m]
        for r in rows[series]:
            mq = r.get("minute_quotes") or []
            if not mq or r.get("target") is None: continue
            t_open = calendar.timegm(time.strptime(r["open_time"], "%Y-%m-%dT%H:%M:%SZ")) * 1000
            for th in trades:
                for i, q in enumerate(mq[:15]):
                    bid, ask = q[0] / 100, q[1] / 100
                    if not (0.01 < bid < ask < 0.99): continue
                    # quote i = close of Kalshi's 1-minute candle ENDING at open + i min (record.js
                    # minuteQuotes). Spot known then = close of the Binance bar that ended at that
                    # moment (bar opening one minute earlier). Using the bar opening AT t looks ahead.
                    t = t_open + i * 60000
                    j = bisect_right(ts, t - 60000) - 1
                    if j < 0 or ts[j] != t - 60000: continue
                    if j < 61: continue
                    rets = [math.log(px[k] / px[k - 1]) for k in range(j - 59, j + 1)]
                    sig = st.pstdev(rets)
                    left = 15 - i
                    if sig <= 0: continue
                    p = Phi(math.log(px[j] / r["target"]) / (sig * math.sqrt(left)))
                    if th == 0.02: checked += 1
                    e_yes = p - ask - fee(ask); e_no = (1 - p) - (1 - bid) - fee(1 - bid)
                    if e_yes > th or e_no > th:
                        yes = e_yes >= e_no
                        price = ask if yes else 1 - bid
                        won = (r["settled_yes"] is True) == yes
                        trades[th].append({"t": t, "coin": series[2:5], "net": won - price - fee(price), "won": won, "price": price, "minute": i, "edge": max(e_yes, e_no)})
                        break
    print(f"minutes checked {checked:,} across {sum(len(v) for v in rows.values()):,} windows (4 coins, {(hi - lo) / 864e5:.0f} days)\n")
    print("theta  trades   win   avg price  net/contract   older half  newer half   by coin (net)")
    for th, tr in trades.items():
        if not tr: print(f" {th:.2f}  none"); continue
        tr.sort(key=lambda x: x["t"]); mid = tr[len(tr) // 2]["t"]
        h1 = [x["net"] for x in tr if x["t"] < mid]; h2 = [x["net"] for x in tr if x["t"] >= mid]
        coins = {c: st.mean([x["net"] for x in tr if x["coin"] == c]) for c in sorted({x["coin"] for x in tr})}
        print(f" {th:.2f}  {len(tr):6}  {st.mean(x['won'] for x in tr):5.1%}   {st.mean(x['price'] for x in tr):6.2f}     {100 * st.mean(x['net'] for x in tr):+6.1f}c       "
              f"{100 * st.mean(h1):+5.1f}c     {100 * st.mean(h2):+5.1f}c    " + "  ".join(f"{c} {100 * v:+.1f}c" for c, v in coins.items()))


def favorites():
    """Cross-check of kalshi-btc-agent's live result: buy the FAVORITE side (price 0.65-0.95) at the first
    minute from `start_min` on where the fair-value model agrees it is at least fairly priced."""
    print("\nFAVORITES (the kalshi-btc style): first minute >= start where favorite price in [lo, hi] and model p >= price + fee")
    print("start  band        trades   win   avg price  net/contract   older   newer    by coin")
    rows = {s_: [json.loads(l) for l in open(os.path.join(DATA, f"{s_}.jsonl"))] for s_ in COINS}
    for start in (0, 5, 10):
        for lo_p, hi_p in ((0.65, 0.80), (0.80, 0.95)):
            tr = []
            for series, sym in COINS.items():
                m = json.load(open(os.path.join(CACHE, f"{sym}-1m.json"))); ts = [x[0] for x in m]; px = [x[1] for x in m]
                for r in rows[series]:
                    mq = r.get("minute_quotes") or []
                    if not mq or r.get("target") is None: continue
                    t_open = calendar.timegm(time.strptime(r["open_time"], "%Y-%m-%dT%H:%M:%SZ")) * 1000
                    for i, q in enumerate(mq[:15]):
                        if i < start: continue
                        bid, ask = q[0] / 100, q[1] / 100
                        if not (0.01 < bid < ask < 0.99): continue
                        t = t_open + i * 60000; j = bisect_right(ts, t - 60000) - 1
                        if j < 61 or ts[j] != t - 60000: continue
                        sig = st.pstdev([math.log(px[k] / px[k - 1]) for k in range(j - 59, j + 1)])
                        if sig <= 0: continue
                        p = Phi(math.log(px[j] / r["target"]) / (sig * math.sqrt(15 - i)))
                        yes = ask <= 1 - bid if False else (ask >= 0.5)      # favorite = side priced above 50c
                        price = ask if yes else 1 - bid; pm = p if yes else 1 - p
                        if lo_p <= price <= hi_p and pm >= price + fee(price):
                            won = (r["settled_yes"] is True) == yes
                            tr.append({"t": t, "coin": series[2:5], "net": won - price - fee(price), "won": won, "price": price}); break
            if not tr: continue
            tr.sort(key=lambda x: x["t"]); mid = tr[len(tr) // 2]["t"]
            h1 = [x["net"] for x in tr if x["t"] < mid]; h2 = [x["net"] for x in tr if x["t"] >= mid]
            coins = {c: st.mean([x["net"] for x in tr if x["coin"] == c]) for c in sorted({x["coin"] for x in tr})}
            print(f"  {start:>2}m  {lo_p:.2f}-{hi_p:.2f}  {len(tr):7}  {st.mean(x['won'] for x in tr):5.1%}   {st.mean(x['price'] for x in tr):5.2f}    {100 * st.mean(x['net'] for x in tr):+6.1f}c     "
                  f"{100 * st.mean(h1):+5.1f}c {100 * st.mean(h2):+5.1f}c   " + " ".join(f"{c} {100 * v:+.1f}c" for c, v in coins.items()))


if __name__ == "__main__":
    main()
    favorites()
