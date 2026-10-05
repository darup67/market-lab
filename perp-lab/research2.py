"""Second pass: daily/4h trend following + funding carry stats. Same cost model, same 60/40 split, nothing tuned on the 40%."""
import json, numpy as np
import history, setups
from strategies import ema
cfg = json.load(open("config.json")); COINS = ["BTC", "ETH", "SOL", "ADA"]
def resample(cs, fd, k):
    out, f2 = [], {}
    for i in range(0, len(cs) - k + 1, k):
        b = cs[i:i + k]; out.append({"t": b[0]["t"], "o": b[0]["o"], "h": max(x["h"] for x in b), "l": min(x["l"] for x in b), "c": b[-1]["c"], "v": sum(x["v"] for x in b)})
        f2[b[0]["t"]] = sum(fd.get(x["t"], 1.25e-5) for x in b)
    return out, f2
def sig(cs, n, trend):
    o, h, l, c = setups.arrays(cs); e = ema(c, trend); s = np.zeros(len(c))
    for i in range(max(n, trend), len(c)):
        if c[i] > h[i - n:i].max() and c[i] > e[i]: s[i] = 1
        elif c[i] < l[i - n:i].min() and c[i] < e[i]: s[i] = -1
    return s
res = {}
for tf, k, maxb in (("1d", 24, 40), ("4h", 4, 60)):
    for n, trend in ((20, 50), (55, 100)) if tf == "1d" else ((60, 200), (120, 300)):
        for am, rr in ((2.5, 3.0), (3.0, 5.0)):
            for dire in ("both", "long"):
                IS, OOS, per = [], [], {}
                for c in COINS:
                    cs0, fd0 = history.get(c); cs, fd = resample(cs0, fd0, k); s = sig(cs, n, trend)
                    if dire == "long": s[s < 0] = 0
                    cut = int(len(cs) * 0.6)
                    ti, _ = setups.simulate(cs, fd, s, am, rr, cfg, 0, cut, max_bars=maxb); to, cv = setups.simulate(cs, fd, s, am, rr, cfg, cut, len(cs), max_bars=maxb)
                    IS += ti; OOS += to; per[c] = (len(to), np.mean([t["r"] for t in to]) if to else 0)
                ri, ro = np.array([t["r"] for t in IS]), np.array([t["r"] for t in OOS])
                res[(tf, n, trend, am, rr, dire)] = (len(ri), ri.mean() if len(ri) else 0, len(ro), ro.mean() if len(ro) else 0, per)
print(f"{'tf':3}{'n':>4}{'trend':>6}{'atr':>5}{'rr':>4}{'dir':>6} | ISn  ISexpR | OOSn OOSexpR | per-coin OOS expR")
for key, (ni, ei, no, eo, per) in sorted(res.items(), key=lambda x: -x[1][1]):
    print(f"{key[0]:3}{key[1]:>4}{key[2]:>6}{key[3]:>5}{key[4]:>4}{key[5]:>6} | {ni:3d} {ei:+7.3f} | {no:4d} {eo:+7.3f} | " + " ".join(f"{c}:{v[0]}/{v[1]:+.2f}" for c, v in per.items()))
print("\nFunding carry (real Hyperliquid funding, annualized, all history):")
for c in COINS:
    cs, fd = history.get(c); v = np.array(list(fd.values())); print(f"  {c}: mean {v.mean()*24*365*100:5.1f}%/yr, share of hours positive {np.mean(v>0)*100:3.0f}%, worst 7d avg {min(np.convolve(v,np.ones(168)/168,'valid'))*24*365*100:6.1f}%/yr")
