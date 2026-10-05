"""Entry/stop/target setups. A setup is decided from bars up to and including the last CLOSED bar i; entry is the NEXT open.
stop = entry -/+ atr_mult*ATR14(i); target = rr * stop distance; time stop = max_bars. Risk-based sizing: risk_pct of equity per trade, leverage capped."""
import numpy as np
from strategies import ema

def atr(h, l, c, n=14):
    tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1)))); tr[0] = h[0] - l[0]
    return ema(tr, 2 * n - 1)

def arrays(cs):
    g = lambda k: np.array([x[k] for x in cs], float)
    return g("o"), g("h"), g("l"), g("c")

def signals(cs, rule, n=48, direction="both"):
    """Returns array s[i] in {-1,0,+1}: setup present at close of bar i."""
    o, h, l, c = arrays(cs); e50, e200 = ema(c, 50), ema(c, 200)
    up, dn = (e50 > e200) & (c > e200), (e50 < e200) & (c < e200)
    s = np.zeros(len(c))
    for i in range(max(n, 210), len(c)):
        if rule == "pullback":
            if up[i] and l[i] <= e50[i] and c[i] > e50[i] and c[i] > o[i]: s[i] = 1
            elif dn[i] and h[i] >= e50[i] and c[i] < e50[i] and c[i] < o[i]: s[i] = -1
        elif rule == "breakout":
            if up[i] and c[i] > h[i - n:i].max(): s[i] = 1
            elif dn[i] and c[i] < l[i - n:i].min(): s[i] = -1
        elif rule == "reversion":                      # fade a stretched hourly move back toward the 50 EMA
            z = (c[i] - e50[i]) / max(atr(h[:i + 1], l[:i + 1], c[:i + 1])[-1], 1e-12) if False else 0
    if direction == "long": s[s < 0] = 0
    if direction == "short": s[s > 0] = 0
    return s

def simulate(cs, fd, sig, atr_mult, rr, cfg, lo=0, hi=None, max_bars=72, risk_pct=0.01):
    o, h, l, c = arrays(cs); a = atr(h, l, c); hi = hi or len(cs)
    cost = cfg["fee_per_side"] + cfg["slippage_per_side"]; levcap = cfg["risk"]["max_leverage"]
    eq, trades, i, curve = 1.0, [], lo + 1, []
    while i < hi - 1:
        d = sig[i - 1]
        if d == 0 or a[i - 1] <= 0: curve.append(eq); i += 1; continue
        entry = o[i]; dist = atr_mult * a[i - 1]; stop = entry - d * dist; tgt = entry + d * rr * dist
        notional = min(risk_pct * eq / (dist / entry), levcap * eq)
        j, exit_px, why = i, None, "time"
        while j < min(i + max_bars, hi):
            if (d > 0 and l[j] <= stop) or (d < 0 and h[j] >= stop): exit_px, why = stop, "stop"; break      # stop wins ties
            if (d > 0 and h[j] >= tgt) or (d < 0 and l[j] <= tgt): exit_px, why = tgt, "target"; break
            j += 1
        if exit_px is None: j = min(j, hi - 1); exit_px = c[j]
        f = sum(fd.get(cs[k]["t"], 1.25e-5) for k in range(i, j + 1))        # funding paid by longs, received by shorts
        pnl = notional * (d * (exit_px / entry - 1) - 2 * cost - d * f)
        eq += pnl; trades.append({"r": pnl / (risk_pct * (eq - pnl)), "why": why, "dir": int(d), "t": cs[i]["t"], "ret": pnl / (eq - pnl)})
        curve += [eq] * (j - i + 1); i = j + 1
    return trades, np.array(curve or [1.0])

def stats(trades, curve):
    if not trades: return {"n": 0}
    r = np.array([t["r"] for t in trades]); w, ls = r[r > 0].sum(), -r[r < 0].sum()
    dd = (curve / np.maximum.accumulate(curve) - 1).min()
    return {"n": len(r), "exp_R": r.mean(), "win%": (r > 0).mean() * 100, "PF": w / ls if ls > 0 else 99, "net%": (curve[-1] - 1) * 100, "maxDD%": dd * 100}
