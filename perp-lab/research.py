import json, itertools, sys
import numpy as np
import history, setups
cfg = json.load(open("config.json")); COINS = ["BTC", "ETH", "SOL", "ADA"]
data = {c: history.get(c) for c in COINS}
split = {c: int(len(data[c][0]) * 0.6) for c in COINS}
grid = [dict(rule=r, n=n, direction=d, atr_mult=a, rr=rr) for r in ("pullback", "breakout") for n in ((48, 120) if r == "breakout" else (48,)) for d in ("both", "long") for a in (1.5, 2.5) for rr in (1.5, 3.0)]
sigs = {(c, g["rule"], g["n"], g["direction"]): setups.signals(data[c][0], g["rule"], g["n"], g["direction"]) for c in COINS for g in grid}
def pooled(g, part):
    tr = []
    for c in COINS:
        cs, fd = data[c]; lo, hi = (0, split[c]) if part == "IS" else (split[c], len(cs))
        t, cv = setups.simulate(cs, fd, sigs[(c, g["rule"], g["n"], g["direction"])], g["atr_mult"], g["rr"], cfg, lo, hi); tr += t
    r = np.array([x["r"] for x in tr]); return r
rows = []
for g in grid:
    ri, ro = pooled(g, "IS"), pooled(g, "OOS")
    rows.append((g, len(ri), ri.mean() if len(ri) else 0, len(ro), ro.mean() if len(ro) else 0))
print(f"{'rule':9}{'n':>4}{'dir':>6}{'atr':>5}{'rr':>5} | IS trades  IS expR | OOS trades OOS expR")
for g, ni, ei, no, eo in sorted(rows, key=lambda x: -x[2]):
    print(f"{g['rule']:9}{g['n']:>4}{g['direction']:>6}{g['atr_mult']:>5}{g['rr']:>5} | {ni:9d} {ei:+8.3f} | {no:10d} {eo:+8.3f}")
json.dump([dict(g, is_n=ni, is_exp=ei, oos_n=no, oos_exp=eo) for g, ni, ei, no, eo in rows], open("results/research.json", "w"), indent=1, default=float)
