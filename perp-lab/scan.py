#!/usr/bin/env python3
"""Live entry/stop/target scanner for perps (BTC, ETH, SOL, ADA, HYPE). PAPER/INFORMATION ONLY: it prints levels, it never orders.
Rules = the two daily long/short trend breakouts that survived out-of-sample (research2.py), each with its own ATR stop and R target.
Context lines come from the existing detector (15m SuperTrend regime, latest flip/rally/gap).
    python3 scan.py          python3 scan.py --json"""
import json, os, sys, time, urllib.request
import numpy as np
import detector, history, setups, feeds
from strategies import ema
HERE = os.path.dirname(os.path.abspath(__file__)); cfg = json.load(open(os.path.join(HERE, "config.json")))
COINS = ["BTC", "ETH", "SOL", "HYPE", "ADA"]
RULES = [dict(name="TREND-55", n=55, trend=100, atr=3.0, rr=5.0, hold=40, oos_expR=0.83, oos_trades=24),
         dict(name="TREND-20", n=20, trend=50, atr=2.5, rr=3.0, hold=40, oos_expR=0.28, oos_trades=52)]
RISK = 0.01; LEVCAP = cfg["risk"]["max_leverage"]

def refresh(coin):
    """Incrementally extend the cached 1h history to now."""
    if coin == "HYPE":
        return feeds.load("HYPE", 5000, "1h", refresh=True)[0]
    cs = history.coinbase(coin); last = cs[-1]["t"] // 1000; end = int(time.time()) // 3600 * 3600
    if end - last > 3600:
        got = {}
        t = last
        while t < end:
            e = min(t + 300 * 3600, end)
            try:
                r = json.load(urllib.request.urlopen(urllib.request.Request(history.CB % (coin, history.iso(t), history.iso(e)), headers={"User-Agent": "perp-lab"}), timeout=30))
            except Exception: r = []
            for c in r: got[c[0] * 1000] = {"t": c[0] * 1000, "o": c[3], "h": c[2], "l": c[1], "c": c[4], "v": c[5]}
            t = e; time.sleep(0.12)
        m = {x["t"]: x for x in cs}; m.update(got); cs = [m[k] for k in sorted(m)]
        json.dump(cs, open(os.path.join(HERE, "data", f"{coin}-cb-1h.json"), "w"))
    return cs

def daily(cs):
    days = {}
    for b in cs: days.setdefault(b["t"] // 86400000, []).append(b)
    keys = sorted(days); out = []
    for k in keys:
        g = days[k]; out.append({"day": k, "n": len(g), "o": g[0]["o"], "h": max(x["h"] for x in g), "l": min(x["l"] for x in g), "c": g[-1]["c"]})
    return out[:-1] if out and out[-1]["n"] < 24 else out          # drop the still-forming UTC day

def px(x):
    """Plain price formatting, never scientific: 87,400 / 2,807 / 124.90 / 0.2652."""
    a = abs(x)
    return f"{x:,.0f}" if a >= 1000 else f"{x:,.1f}" if a >= 100 else f"{x:,.2f}" if a >= 1 else f"{x:.4f}"

def setup(d, rule, last_px):
    c = np.array([x["c"] for x in d]); h = np.array([x["h"] for x in d]); l = np.array([x["l"] for x in d])
    e = ema(c, rule["trend"]); a = setups.atr(h, l, c); i = len(d) - 1; n = rule["n"]
    hi, lo = h[i - n:i].max(), l[i - n:i].min()
    sig = 1 if (c[i] > hi and c[i] > e[i]) else -1 if (c[i] < lo and c[i] < e[i]) else 0
    dist = rule["atr"] * a[i]
    out = {"rule": rule["name"], "signal": {1: "LONG", -1: "SHORT", 0: "none"}[sig], "atr_d": a[i], "trend_ok_long": bool(c[i] > e[i]), "long_trigger": hi, "short_trigger": lo}
    if sig:
        entry = last_px; stop = entry - sig * dist; tgt = entry + sig * rule["rr"] * dist
        notional_x = min(RISK / (dist / entry), LEVCAP)
        out.update(entry=entry, stop=stop, target=tgt, stop_pct=dist / entry * 100, size_x_equity=round(notional_x, 2), risk_pct_equity=RISK * 100,
                   max_hold_days=rule["hold"], signal_day=time.strftime("%Y-%m-%d", time.gmtime(d[i]["day"] * 86400)))
    else:
        out.update(long_stop_if_triggered=hi - rule["atr"] * a[i], short_stop_if_triggered=lo + rule["atr"] * a[i])
    return out

def context(coin):
    try:
        cs = detector.bars15(coin); reg = detector.supertrend(cs); sg = detector.signals(cs)[-3:]
        names = {1: "bull", -1: "bear"}
        return {"supertrend_15m": names.get(reg[-1], "?"), "recent": [f"{k}:{names[d]} {(len(cs)-1-i)*15//60}h ago" for i, k, d in sg]}
    except Exception as e:
        return {"error": str(e)}

def main():
    res = {}
    for c in COINS:
        cs = refresh(c); d = daily(cs); last = cs[-1]["c"]
        res[c] = {"price": last, "days_of_history": len(d), "setups": [setup(d, r, last) for r in RULES], "context": context(c)}
        if c == "HYPE": res[c]["warning"] = "only ~200 days of history: rules not validated on this coin"
    json.dump(res, open(os.path.join(HERE, "results", "setups.json"), "w"), indent=1, default=float)
    if "--json" in sys.argv: print(json.dumps(res, indent=1, default=float)); return
    print(f"PERP SETUPS (paper/information only) · {time.strftime('%a %b %d %H:%M', time.localtime())} · risk {RISK*100:.0f}% of equity per trade, leverage cap {LEVCAP:g}x\n")
    for c, r in res.items():
        print(f"{c}  ${px(r['price'])}   15m SuperTrend {r['context'].get('supertrend_15m','?')}   recent detector: {', '.join(r['context'].get('recent', [])) or 'none'}")
        for s in r["setups"]:
            if s["signal"] != "none":
                print(f"   {s['rule']}: {s['signal']}  entry ~{px(s['entry'])}  stop {px(s['stop'])} ({s['stop_pct']:.1f}%)  target {px(s['target'])}  size {s['size_x_equity']}x equity  hold<={s['max_hold_days']}d  (signal {s['signal_day']})")
            else:
                print(f"   {s['rule']}: no setup. LONG if a daily close > {px(s['long_trigger'])} (stop then ~{px(s['long_stop_if_triggered'])}); SHORT if a daily close < {px(s['short_trigger'])} (stop then ~{px(s['short_stop_if_triggered'])}). Trend filter {'up' if s['trend_ok_long'] else 'down'}.")
        if r.get("warning"): print("   !", r["warning"])
    print("\nProven? TREND-55 and TREND-20 were positive out-of-sample in 4 of 4 coins in a ~3-year test (24 and 52 OOS trades): promising, small sample. The 15m detector long/short mirrors were NOT better than random after costs.")

if __name__ == "__main__":
    main()
