#!/usr/bin/env python3
"""
Live Chronos / AutoGluon reading for the CURRENT Kalshi BTC 15-minute and 1-hour windows.

For each window it shows the market's price, the cushion model, Chronos-2, Chronos-Bolt (zero-shot on the last 512
one-minute BRTI-proxy closes, forecasting the settlement) and AutoGluon (tabular models fit by ml_test_minute.py for the
15-minute marks 1/3/4/5 and by study.py for the 1-hour). Each probability is P(BTC finishes ABOVE the strike).

These are READINGS, not recommendations: in every test (results/ml-test-minute-*, kalshi-btc-1h-agent/results/study-*) the market
price was at least as accurate as every model at every minute, and no model had positive expected value after fees.

    ~/.venvs/market-ml/bin/python research/live_reading.py

The AutoGluon models are too big for git (about 1.5 GB). They live in research/models/ (git-ignored) and are rebuilt by:
    MLTEST_WORK=research/models ~/.venvs/market-ml/bin/python ml_test_minute.py 1 3 4 5      (15-minute marks)
    MLTEST_WORK=research/models ~/.venvs/market-ml/bin/python ~/kalshi-btc-1h-agent/study.py   (1-hour)
Without them the AutoGluon rows show n/a and Chronos still runs.
"""
import importlib.util, json, math, os, sys, time, warnings
from datetime import datetime, timezone
from statistics import NormalDist
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HOME = os.path.expanduser("~")
QL = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
MODELS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")   # local only (1.5 GB, git-ignored)
AG15 = {m: os.path.join(MODELS, f"agtab-minute-{m}-%s") for m in (1, 3, 4, 5)}
AG1H = os.path.join(MODELS, "ag-%s")
CTX, SIGMA_PROXY = 512, 8.61


def mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

feeds = mod("btcfeeds", f"{HOME}/kalshi-btc-1h-agent/feeds.py")
model = mod("btcmodel", f"{HOME}/kalshi-btc-1h-agent/model.py")


def ts(s):
    return int(datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def closes(now):
    end = now - now % 60
    cb, bs = feeds.coinbase_1m(end - CTX * 60 - 60, end), feeds.bitstamp_1m(end - CTX * 60 - 60, end)
    both = sorted(set(cb) & set(bs))
    return both, [(cb[t][3] + bs[t][3]) / 2 for t in both]


_pipes = {}
def chronos(name, ctx, horizon, strike):
    import torch
    from chronos import BaseChronosPipeline
    if name not in _pipes:
        _pipes[name] = BaseChronosPipeline.from_pretrained(name, device_map="cpu", torch_dtype=torch.float32)
    q, _ = _pipes[name].predict_quantiles([torch.tensor(ctx[-CTX:], dtype=torch.float32)], prediction_length=horizon, quantile_levels=QL)
    q = q.numpy() if hasattr(q, "numpy") else np.asarray(q)
    v = np.maximum.accumulate(q[0].reshape(horizon, len(QL))[-1].astype(float))
    cdf = QL[0] * .5 if strike <= v[0] else (1 - (1 - QL[-1]) * .5 if strike >= v[-1] else float(np.interp(strike, v, QL)))
    return 1 - cdf, v


def ag(path, row):
    from autogluon.tabular import TabularPredictor
    if not os.path.isdir(path):
        return None
    try:
        return float(TabularPredictor.load(path, verbosity=0).predict_proba(pd.DataFrame([row]))[1].iloc[0])
    except Exception as e:
        return None


def pct(p):
    return "n/a" if p is None else f"{p * 100:5.1f}%"


def read(label, strike, mid, minute, left, ctx, features_by_marks, ag_paths, horizon_label):
    px = ctx[-1]
    d = np.diff(ctx[-61:])
    sig = float(np.std(d, ddof=1))
    gap = px - strike
    p_c, sd = model.p_yes(gap, left, sig, SIGMA_PROXY)
    out = {"market": mid, "cushion": p_c}
    h = max(1, int(round(left)))
    for name, mdl in (("chronos-2", "amazon/chronos-2"), ("chronos-bolt", "amazon/chronos-bolt-base")):
        out[name], _ = chronos(mdl, ctx, h, strike)
    row = features_by_marks(ctx, gap, sd, sig)
    mm = min(ag_paths, key=lambda k: abs(k - minute)) if isinstance(ag_paths, dict) else None
    if isinstance(ag_paths, dict):
        note = "" if abs(mm - minute) < 1 else f" (trained at minute {mm}; current minute {minute:.0f})"
        out["autogluon"] = ag(ag_paths[mm] % "ag-tab", row)
        out["autogluon+market"] = ag(ag_paths[mm] % "ag-tab+mkt", {**row, "market": mid})
        out["_note"] = note
    else:
        out["autogluon"] = ag(ag_paths % "ag-tab", {**row, "m": int(round(minute))})
        out["autogluon+market"] = ag(ag_paths % "ag-tab+mkt", {**row, "m": int(round(minute)), "market": mid})
        out["_note"] = ""
    print(f"\n{label}")
    print(f"  strike ${strike:,.2f} · BTC ${px:,.0f} ({gap:+,.0f}) · minute {minute:.0f}, {left:.0f} min left · ±${sd:,.0f} typical move to close")
    print(f"  {'reader':<18}{'P(above)':>9}   call")
    best = None
    for k in ("market", "cushion", "chronos-2", "chronos-bolt", "autogluon", "autogluon+market"):
        p = out.get(k)
        call = "-" if p is None else ("UP" if p >= .5 else "DOWN")
        print(f"  {k:<18}{pct(p):>9}   {call}")
    votes = [out[k] >= .5 for k in ("chronos-2", "chronos-bolt", "autogluon") if out.get(k) is not None]
    mk = out["market"] >= .5
    agree = sum(v == mk for v in votes)
    print(f"  models agreeing with the market's side: {agree} of {len(votes)}{out['_note']}")
    return out


def features15(ctx, gap, sd, sig):
    r = lambda k: (ctx[-1] / ctx[-1 - k] - 1) * 1e4
    now = datetime.now(timezone.utc)
    return dict(gap=gap, z=gap / sd, sig=sig, r1=r(1), r2=r(2), r4=r(4), r8=r(8), r16=r(16),
                rv16=float(np.std(np.diff(ctx[-17:]))), hour=now.hour + now.minute / 60, dow=now.weekday())


def features1h(ctx, gap, sd, sig, left=None):
    r = lambda k: (ctx[-1] / ctx[-1 - k] - 1) * 1e4
    now = datetime.now(timezone.utc)
    return dict(gap=gap, z=gap / sd, sig=sig, left=left, r1=r(1), r5=r(5), r15=r(15), r60=r(60),
                rv16=float(np.std(np.diff(ctx[-17:]))), hour=now.hour, dow=now.weekday())


def main():
    now = int(time.time())
    t, ctx = closes(now)
    if len(ctx) < 200:
        sys.exit("not enough minute data")
    age = (now - t[-1]) / 60
    print(f"BTC proxy (Coinbase+Bitstamp) ${ctx[-1]:,.0f}, last 1-minute bar {age:.1f} min old · {datetime.now():%a %b %d %-I:%M %p} ET")

    # 15-minute
    m = feeds.get(f"{feeds.KALSHI}/markets?series_ticker=KXBTC15M&status=open&limit=5")["markets"]
    m = sorted(m, key=lambda x: x["close_time"])[0]
    o_ts, c_ts = ts(m["open_time"]), ts(m["close_time"])
    mid = (float(m.get("yes_bid_dollars") or 0) + float(m.get("yes_ask_dollars") or 0)) / 2
    minute, left = (now - o_ts) / 60, (c_ts - now) / 60
    read("BTC 15-MINUTE (KXBTC15M)", float(m["floor_strike"]), mid, minute, left, ctx, features15, AG15, "15m")

    # 1-hour
    ev = feeds.get(f"{feeds.KALSHI}/markets?series_ticker=KXBTCD&status=open&limit=1000")["markets"]
    close = min(x["close_time"] for x in ev)
    event = next(x["event_ticker"] for x in ev if x["close_time"] == close)
    c_ts, o_ts = ts(close), ts(close) - 3600
    s0 = dict(zip(t, ctx)).get(o_ts - 60) or ctx[0]
    tkr, K = feeds.strike_ticker(event, s0)
    mk = feeds.kalshi_market(tkr)
    mid = (float(mk.get("yes_bid_dollars") or 0) + float(mk.get("yes_ask_dollars") or 0)) / 2
    minute, left = (now - o_ts) / 60, (c_ts - now) / 60
    f = lambda c, g, sd, sg: features1h(c, g, sd, sg, left=left)
    read("BTC 1-HOUR (KXBTCD, strike nearest the hour's open)", K, mid, minute, left, ctx, f, AG1H, "1h")


if __name__ == "__main__":
    main()
