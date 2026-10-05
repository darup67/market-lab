"""Long/short mirror of the existing coin detector (flip-notifier/crypto-scan.js): SuperTrend flip (15m), FVG 'rally' (30m, >=2.5x volume), 1h FVG gap.
Bull signals -> LONG, mirrored bear signals -> SHORT. Entry = next 15m open, stop = k*ATR, target = rr*stop distance (the ledger uses 1 ATR / 2 ATR)."""
import json, os, time, urllib.request
import numpy as np
import feeds, history
HERE = os.path.dirname(os.path.abspath(__file__))
FACTOR, ATRLEN, MINATR, RALLYX = 3.0, 10, 0.2, 2.5

def bars15(coin, days=120):
    p = os.path.join(HERE, "data", f"{coin}-15m.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 43200: return json.load(open(p))
    if coin == "HYPE":
        end = int(time.time() * 1000)
        r = feeds.post({"type": "candleSnapshot", "req": {"coin": "HYPE", "interval": "15m", "startTime": end - 5000 * 900000, "endTime": end}})
        cs = [{"t": c["t"], "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])} for c in r]
    else:
        out, end = {}, int(time.time()) // 900 * 900; t = end - days * 86400
        while t < end:
            e = min(t + 300 * 900, end); r = []
            for k in range(6):
                try:
                    u = f"https://api.exchange.coinbase.com/products/{coin}-USD/candles?granularity=900&start={history.iso(t)}&end={history.iso(e)}"
                    r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "perp-lab"}), timeout=30)); break
                except Exception: time.sleep(2 * (k + 1))
            for c in r: out[c[0] * 1000] = {"t": c[0] * 1000, "o": c[3], "h": c[2], "l": c[1], "c": c[4], "v": c[5]}
            t = e; time.sleep(0.12)
        cs = [out[k] for k in sorted(out)]
    json.dump(cs, open(p, "w")); return cs

def agg(cs, k):
    return [{"t": cs[i]["t"], "o": cs[i]["o"], "h": max(x["h"] for x in cs[i:i + k]), "l": min(x["l"] for x in cs[i:i + k]), "c": cs[i + k - 1]["c"], "v": sum(x["v"] for x in cs[i:i + k])} for i in range(0, len(cs) - k + 1, k)]

def atr_series(cs, n=14):
    out, a, pc = [], None, None
    for b in cs:
        c0 = b["c"] if pc is None else pc; tr = max(b["h"] - b["l"], abs(b["h"] - c0), abs(b["l"] - c0)); a = tr if a is None else (a * (n - 1) + tr) / n; out.append(a); pc = b["c"]
    return np.array(out)

def supertrend(cs, factor=FACTOR, n=ATRLEN):
    """+1 = BUY regime, -1 = SELL regime (port of headless-flip.js supertrendRegimes)."""
    out, atr, trsum, pu, pl, pst, res = [], None, 0.0, None, None, None, []
    for i, b in enumerate(cs):
        pc = cs[i - 1]["c"] if i else None
        tr = b["h"] - b["l"] if pc is None else max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)); prev_atr = atr
        if i < n:
            trsum += tr; atr = trsum / n if i == n - 1 else None
        else: atr = (atr * (n - 1) + tr) / n
        if atr is None: res.append(0); continue
        hl2 = (b["h"] + b["l"]) / 2; up, lo = hl2 + factor * atr, hl2 - factor * atr
        if pl is not None: lo = lo if (lo > pl or pc < pl) else pl
        if pu is not None: up = up if (up < pu or pc > pu) else pu
        if prev_atr is None: d = 1
        elif pst == pu: d = -1 if b["c"] > up else 1        # (JS: dir -1 means price above band = BUY)
        else: d = 1 if b["c"] < lo else -1
        pst = lo if d == -1 else up; pu, pl = up, lo; res.append(1 if d == -1 else -1)
    return res

def signals(cs15):
    """Returns list of (bar_index_of_15m_close, kind, direction +1/-1)."""
    sigs, reg = [], supertrend(cs15)
    for i in range(1, len(cs15)):
        if reg[i] == 1 and reg[i - 1] == -1: sigs.append((i, "flip", 1))
        if reg[i] == -1 and reg[i - 1] == 1: sigs.append((i, "flip", -1))
    for tf, kind in ((2, "rally"), (4, "gap")):
        f = agg(cs15, tf); a = atr_series(f)
        for k in range(22, len(f)):
            b, a2 = f[k], f[k - 2]; side = 0
            if b["l"] > a2["h"] and b["l"] - a2["h"] >= MINATR * a[k]: side = 1
            elif b["h"] < a2["l"] and a2["l"] - b["h"] >= MINATR * a[k]: side = -1
            if not side: continue
            if kind == "rally":
                pr = [x["v"] for x in f[max(0, k - 20):k]]; volx = b["v"] / (np.mean(pr) or 1)
                if volx < RALLYX: continue
            sigs.append((k * tf + tf - 1, kind, side))
    return sorted(sigs)
