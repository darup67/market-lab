#!/usr/bin/env python3
"""perp-lab: SIMULATION ONLY. No broker import, no order path. Tests pre-declared rules on public perp data, in-sample vs out-of-sample, net of costs + funding.
    python3 lab.py            fetch fresh data and print the report (saved to results/)"""
import json, os, sys, time
import numpy as np
import feeds, strategies, sim
HERE = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(HERE, "config.json")))

def signals():
    """Where each rule stands NOW (paper stance only; nothing is ordered)."""
    out = {}
    for coin in cfg["coins"]:
        cs, fd = feeds.load(coin, cfg["candles"], cfg["interval"]); c = np.array([x["c"] for x in cs]); f = np.array([fd.get(x["t"], 0.0) for x in cs])
        out[coin] = {n: int(fn(c, f)[-1]) for n, fn in strategies.STRATS.items() if n != "buy_hold"}
        out[coin]["last"] = c[-1]; out[coin]["funding_apr_%"] = round(f[-24:].mean() * 24 * 365 * 100, 1)
        time.sleep(1)
    json.dump(out, open(os.path.join(HERE, "results", "signals.json"), "w"), indent=1)
    for k, v in out.items(): print(k, v)

def main():
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    rows = []
    for coin in cfg["coins"]:
        time.sleep(1)
        try:
            cs, fd = feeds.load(coin, cfg["candles"], cfg["interval"])
        except Exception as e:
            print(f"{coin}: no data ({e})"); continue
        c = np.array([x["c"] for x in cs]); f = np.array([fd.get(x["t"], 0.0) for x in cs])
        cut = int(len(cs) * cfg["split"])
        for name, fn in strategies.STRATS.items():
            pos = fn(c, f)
            for part, (lo, hi) in (("in-sample", (0, cut)), ("OUT-of-sample", (cut, len(cs)))):
                r = sim.run(cs, fd, pos, cfg, lo, hi); r.update(coin=coin, strat=name, part=part, bars=hi - lo); rows.append(r)
    json.dump(rows, open(os.path.join(HERE, "results", "latest.json"), "w"), indent=1, default=float)
    print(f"{'coin':5}{'strategy':14}{'part':14}{'net':>9}{'maxDD':>9}{'sharpe':>8}{'trades':>8}{'win%':>7}{'liq':>5}")
    for r in rows:
        print(f"{r['coin']:5}{r['strat']:14}{r['part']:14}{r['net_return']*100:8.1f}%{r['max_dd']*100:8.1f}%{r['sharpe']:8.2f}{r['trades']:8d}{r['win_rate']*100:6.0f}%{r['liquidations']:5d}")
    oos = [r for r in rows if r["part"] == "OUT-of-sample" and r["strat"] != "buy_hold"]
    good = [r for r in oos if r["net_return"] > 0]
    print(f"\nOut-of-sample, non-baseline: {len(good)} of {len(oos)} coin/rule combos finished positive after costs and funding.")

if __name__ == "__main__":
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    signals() if "--signals" in sys.argv else main()
