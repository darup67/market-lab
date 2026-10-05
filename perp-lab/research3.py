"""Existing detector (flip / rally / gap) mirrored long+short, with ATR stop/target, net of costs+funding. 60/40 split by time per coin."""
import json, numpy as np
import detector, history, setups
cfg = json.load(open("config.json")); COINS = ["BTC", "ETH", "SOL", "HYPE", "ADA"]
rng = np.random.default_rng(1)
data = {}
for c in COINS:
    cs = detector.bars15(c); fd = history.get(c)[1]
    f15 = {b["t"]: fd.get(b["t"] // 3600000 * 3600000, 1.25e-5) / 4 for b in cs}
    data[c] = (cs, f15, detector.signals(cs))
    print(c, len(cs), "bars", len(data[c][2]), "signals", flush=True)
def run(kind, dirn, am, rr, part):
    tr = []
    for c, (cs, fd, sg) in data.items():
        n = len(cs); cut = int(n * 0.6); lo, hi = (0, cut) if part == "IS" else (cut, n)
        s = np.zeros(n)
        for i, k, d in sg:
            if (kind == "any" or k == kind) and (dirn == "both" or d == dirn): s[i] = d
        if kind == "random":
            for i in range(lo, hi, 12): s[i] = rng.choice([1, -1]) if dirn == "both" else dirn
        t, _ = setups.simulate(cs, fd, s, am, rr, cfg, lo, hi, max_bars=96); tr += t
    r = np.array([x["r"] for x in tr]); return len(r), (r.mean() if len(r) else 0), ((r > 0).mean() * 100 if len(r) else 0)
print(f"\n{'kind':7}{'dir':>6}{'stop':>5}{'rr':>4} | IS n   expR  win% | OOS n  expR  win%")
rows = []
for kind in ("flip", "rally", "gap", "random"):
    for dirn in ("both", 1, -1):
        for am, rr in ((1.0, 2.0), (2.0, 2.0), (2.0, 3.0)):
            i, o = run(kind, dirn, am, rr, "IS"), run(kind, dirn, am, rr, "OOS")
            rows.append((kind, dirn, am, rr, i, o))
            print(f"{kind:7}{str({1:'long',-1:'short'}.get(dirn,dirn)):>6}{am:>5}{rr:>4} | {i[0]:4d} {i[1]:+6.3f} {i[2]:4.0f} | {o[0]:4d} {o[1]:+6.3f} {o[2]:4.0f}")
json.dump(rows, open("results/research3.json", "w"), default=float)
