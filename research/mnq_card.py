#!/usr/bin/env python3
"""
/MNQ card (Micro E-mini Nasdaq-100 futures): where price is, today's and yesterday's levels, volatility, and a lean.

Levels come from Yahoo's MNQ=F 5-minute bars (CME data, ~10 minutes delayed); the LIVE price is Hyperliquid's real-time Nasdaq-100 perp
(xyz:XYZ100), shifted by its recent basis to MNQ terms. The S&P comparison uses xyz:SP500 vs ES=F the same way.
Informational only: nothing here places or suggests an order, and no edge has been shown for any of these signals.

    ~/.venvs/market-ml/bin/python research/mnq_card.py
"""
import json, math, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import numpy as np

ET = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25))


def post(url, body):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={**UA, "Content-Type": "application/json"}), timeout=25))


def yahoo(sym, interval="5m", rng="7d"):
    j = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym, safe='')}?interval={interval}&range={rng}&includePrePost=true")["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    out = [(t, o, h, l, c, v or 0) for t, o, h, l, c, v in zip(j["timestamp"], q["open"], q["high"], q["low"], q["close"], q["volume"]) if c is not None]
    return out


def hl_mid(coin):
    return float(post("https://api.hyperliquid.xyz/info", {"type": "allMids", "dex": "xyz"})[coin])


def hl_candles(coin, start, end):
    r = post("https://api.hyperliquid.xyz/info", {"type": "candleSnapshot", "req": {"coin": coin, "interval": "5m", "startTime": start * 1000, "endTime": end * 1000}})
    return {c["t"] // 1000: float(c["c"]) for c in r}


def et(t):
    return datetime.fromtimestamp(t, timezone.utc).astimezone(ET)


def session_split(bars, now_et):
    """Group bars into: RTH (9:30-16:00 ET) per date, and the overnight Globex session before each RTH open."""
    rth, on = {}, {}
    for b in bars:
        d = et(b[0])
        mins = d.hour * 60 + d.minute
        day = d.date()
        if 570 <= mins < 960:
            rth.setdefault(day, []).append(b)
        else:
            key = day if mins < 570 else day + timedelta(days=1)   # evening belongs to the next trading date
            on.setdefault(key, []).append(b)
    return rth, on


def hi(bs): return max(b[2] for b in bs)
def lo(bs): return min(b[3] for b in bs)


def nearest_levels(price, step_sets=(100, 250, 500, 1000)):
    out = {}
    for s in step_sets:
        out[s] = (math.floor(price / s) * s, math.ceil(price / s) * s)
    return out


def main():
    now = int(time.time())
    now_et = et(now)
    bars = yahoo("MNQ=F")
    es = yahoo("ES=F", "5m", "7d")
    last_bar_t = bars[-1][0]
    delay = (now - last_bar_t) / 60
    rth, on = session_split(bars, now_et)
    today = now_et.date()
    days = sorted(rth)
    prior = [d for d in days if d < today][-1]
    prior_rth = rth[prior]
    pc, ph, pl = prior_rth[-1][4], hi(prior_rth), lo(prior_rth)
    po = prior_rth[0][1]

    # live price in MNQ terms
    live_hl = hl_mid("xyz:XYZ100")
    hlc = hl_candles("xyz:XYZ100", now - 36 * 3600, now)
    pairs = [(hlc[b[0]], b[4]) for b in bars if b[0] in hlc and now - b[0] < 30 * 3600]
    basis = float(np.median([m - h for h, m in pairs])) if len(pairs) > 20 else float("nan")
    live = live_hl + basis if basis == basis else bars[-1][4]
    drift = float(np.std([m - h - basis for h, m in pairs])) if len(pairs) > 20 else float("nan")

    # today
    t_rth = rth.get(today, [])
    t_on = on.get(today, [])
    open_px = t_rth[0][1] if t_rth else None
    day_hi = hi(t_rth) if t_rth else None
    day_lo = lo(t_rth) if t_rth else None
    on_hi, on_lo = (hi(t_on), lo(t_on)) if t_on else (None, None)
    orb = [b for b in t_rth if et(b[0]).hour * 60 + et(b[0]).minute < 600]       # 9:30-10:00 opening range
    or_hi, or_lo = (hi(orb), lo(orb)) if orb else (None, None)
    vwap = None
    if t_rth:
        tp = np.array([(b[2] + b[3] + b[4]) / 3 for b in t_rth]); vv = np.array([b[5] for b in t_rth], float)
        vwap = float((tp * vv).sum() / vv.sum()) if vv.sum() > 0 else None

    # volatility
    rng5 = np.array([b[2] - b[3] for b in bars[-120:]])
    ranges_day = [hi(rth[d]) - lo(rth[d]) for d in days[-6:-1]]
    atr5 = float(rng5[-12:].mean())
    chg_1h = live - [b[4] for b in bars if b[0] <= now - 3600 - delay * 60][-1] if bars else float("nan")

    # S&P context
    es_rth, _ = session_split(es, now_et)
    es_prior = [d for d in sorted(es_rth) if d < today][-1]
    es_pc = es_rth[es_prior][-1][4]
    sp_live = hl_mid("xyz:SP500")
    sp_hlc = hl_candles("xyz:SP500", now - 36 * 3600, now)
    sp_pairs = [(sp_hlc[b[0]], b[4]) for b in es if b[0] in sp_hlc and now - b[0] < 30 * 3600]
    sp_basis = float(np.median([m - h for h, m in sp_pairs])) if len(sp_pairs) > 20 else 0
    sp_eq = sp_live + sp_basis

    f = lambda x: "n/a" if x is None else f"{x:,.2f}"
    pct = lambda a, b: f"{(a / b - 1) * 100:+.2f}%"
    print(f"/MNQ CARD · {now_et:%a %b %d, %-I:%M %p} ET\n")
    print(f"  Live (MNQ-equivalent)  {live:,.0f}   ({pct(live, pc)} vs yesterday's close {pc:,.0f}; Hyperliquid Nasdaq-100 {live_hl:,.1f} shifted by basis {basis:+.0f})")
    print(f"  Data freshness         CME bars {delay:.0f} min delayed; live price is real-time; basis wobble ±{drift:.0f} pts")
    print()
    print("  TODAY")
    if open_px:
        print(f"    Open {open_px:,.0f} ({pct(open_px, pc)} gap)   RTH high {day_hi:,.0f}   RTH low {day_lo:,.0f}   range {day_hi - day_lo:,.0f} pts")
        pos = (live - day_lo) / max(day_hi - day_lo, 1)
        print(f"    Price sits at {pos * 100:.0f}% of today's range" + (f"   VWAP {vwap:,.0f} ({'above' if live > vwap else 'below'} by {abs(live - vwap):.0f})" if vwap else ""))
    if or_hi:
        st = "ABOVE the opening range" if live > or_hi else ("BELOW the opening range" if live < or_lo else "inside the opening range")
        print(f"    Opening range 9:30-10:00: {or_lo:,.0f} – {or_hi:,.0f}  → price is {st}")
    if on_hi:
        print(f"    Overnight (Globex): high {on_hi:,.0f}   low {on_lo:,.0f}")
    print()
    print("  YESTERDAY (RTH)")
    print(f"    Open {po:,.0f}   High {ph:,.0f}   Low {pl:,.0f}   Close {pc:,.0f}   range {ph - pl:,.0f} pts")
    print()
    print("  KEY LEVELS (nearest above / below live)")
    cands = {"yesterday high": ph, "yesterday low": pl, "yesterday close": pc, "today high": day_hi, "today low": day_lo, "opening-range high": or_hi,
             "opening-range low": or_lo, "overnight high": on_hi, "overnight low": on_lo, "VWAP": vwap}
    for k, s in ((250, "250"), (1000, "1,000")):
        cands[f"round {s}↑"] = math.ceil(live / k) * k
        cands[f"round {s}↓"] = math.floor(live / k) * k
    cands = {k: v for k, v in cands.items() if v}
    above = sorted([(v, k) for k, v in cands.items() if v > live])[:3]
    below = sorted([(v, k) for k, v in cands.items() if v <= live], reverse=True)[:3]
    for v, k in reversed(above):
        print(f"    {v:>9,.0f}  {k}   (+{v - live:,.0f})")
    print(f"    {live:>9,.0f}  ← live")
    for v, k in below:
        print(f"    {v:>9,.0f}  {k}   (-{live - v:,.0f})")
    print()
    print("  VOLATILITY")
    print(f"    Last hour {chg_1h:+,.0f} pts   typical 5-min bar range {atr5:,.0f} pts   today's range {('%.0f' % (day_hi - day_lo)) if day_hi else 'n/a'} vs 5-day average {np.mean(ranges_day):,.0f}")
    print(f"    $2 per point per contract: a typical 5-min bar ≈ ${atr5 * 2:,.0f}, a 100-pt move = $200 per contract")
    print()
    print("  CONTEXT")
    print(f"    S&P 500 (ES-equivalent) {sp_eq:,.0f}  {pct(sp_eq, es_pc)} vs yesterday's close   |   Nasdaq {pct(live, pc)}  → Nasdaq is {'leading' if (live / pc) > (sp_eq / es_pc) else 'lagging'} by {abs((live / pc) - (sp_eq / es_pc)) * 100:.2f} pts")
    print()
    bias = []
    if vwap:
        bias.append("above VWAP" if live > vwap else "below VWAP")
    if or_hi:
        bias.append("above the opening range" if live > or_hi else ("below the opening range" if live < or_lo else "inside the opening range"))
    bias.append("above yesterday's close" if live > pc else "below yesterday's close")
    print(f"  LEAN: {', '.join(bias)}.")
    print("  Not a forecast: no edge has been demonstrated for these levels, and this card places no orders. Informational only.")


if __name__ == "__main__":
    main()
