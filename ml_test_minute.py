#!/usr/bin/env python3
"""
Chronos and AutoGluon vs. the Kalshi market price at minutes 1, 3 and 4 of a BTC 15-minute window.

A follow-up to ml_test.py (which tests only what is known at the window's open). Here every
contender sees what is known at minute m of the window and must call the settlement:
"close >= target" (target = the window's strike).

    market        Kalshi YES mid at the end of minute m (clock-aligned)      (the baseline)
    cushion       the kalshi-btc-agent model: Phi(gap / sqrt(sigma_1m^2 * (15 - m - 2/3) + 8.61^2))
    chronos-bolt  amazon/chronos-bolt-base, zero-shot on the last 512 one-minute closes, horizon 15 - m
    chronos-2     amazon/chronos-2, same
    ag-tab        AutoGluon-Tabular on gap, recent returns and volatility (fit on the first half)
    ag-tab+mkt    the same features plus the market's minute-m mid

Spot = Coinbase BTC-USD 1-minute closes (a BRTI constituent; ~$9 RMS from Kalshi's settlement value).
Split: first half of the windows (chronological) to fit, second half to test. Not pre-registered:
treat any "win" as a hypothesis for the live scorecard.

    ~/.venvs/market-ml/bin/python ml_test_minute.py [MINUTE ...]     (default: 1 3 4)
"""
import json, math, os, sys, time, warnings
from datetime import datetime, timezone
from statistics import NormalDist
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/kalshi-btc-agent"))
from ml_test import load, metrics, mcnemar_p, brier_diff_ci, QL, WORK   # same scoring as the open-time test
import feeds                                                             # kalshi-btc-agent's Coinbase reader

SIGMA_PROXY = 8.61   # $, kalshi-btc-agent gate.json: proxy error vs Kalshi's settlement value
CTX = 512
MAX_SPREAD = 10      # cents; a wider minute-m quote is an empty book, not a price


def coinbase_closes(rows):
    start = int(rows[0]["t"].timestamp()) - CTX * 60 - 3600
    end = int(rows[-1]["t"].timestamp()) + 16 * 60
    cache = os.path.join(WORK, f"coinbase-1m-{start}-{end}.json")
    if os.path.exists(cache):
        return {int(k): v for k, v in json.load(open(cache)).items()}
    print(f"fetching Coinbase 1m {feeds.iso(start)} -> {feeds.iso(end)} ...", file=sys.stderr)
    c = {t: v[3] for t, v in feeds.coinbase_1m(start, end).items()}
    os.makedirs(WORK, exist_ok=True)
    json.dump(c, open(cache, "w"))
    return c


def minute_mid(r, m):
    q = (r.get("minute_quotes") or [])
    if len(q) <= m or not q[m] or q[m][0] is None or q[m][1] is None or q[m][1] - q[m][0] > MAX_SPREAD:
        return None
    return (q[m][0] + q[m][1]) / 200.0


def build(rows, C, m):
    """One observation per window: everything known at the end of minute m."""
    obs = []
    for r in rows:
        o_ts = int(r["t"].timestamp())
        now_bar = o_ts + (m - 1) * 60            # bar that closes at open + m minutes
        hist = [C.get(t) for t in range(now_bar - (CTX - 1) * 60, now_bar + 60, 60)]
        if any(v is None for v in hist[-61:]):
            continue
        ctx = [v for v in hist if v is not None]
        mid = minute_mid(r, m)
        if mid is None or len(ctx) < 128:
            continue
        spot = ctx[-1]
        d60 = np.diff(ctx[-61:])
        sig = float(np.std(d60))
        if sig <= 0:
            continue
        gap = spot - r["target"]
        left = 15 - m
        sd = math.sqrt(sig ** 2 * max(left - 2 / 3, 1 / 3) + SIGMA_PROXY ** 2)
        rets = lambda k: (ctx[-1] - ctx[-1 - k]) / ctx[-1 - k] * 1e4
        t_et = r["t"].astimezone(timezone.utc)
        obs.append(dict(
            ticker=r["ticker"], y=int(r["settled_yes"]), market=mid, ctx=ctx, target=r["target"],
            cushion=NormalDist().cdf(gap / sd),
            f=dict(gap=gap, z=gap / sd, sig=sig, r1=rets(1), r2=rets(2), r4=rets(4), r8=rets(8), r16=rets(16),
                   rv16=float(np.std(np.diff(ctx[-17:]))), hour=t_et.hour + t_et.minute / 60, dow=t_et.weekday())))
    return obs


_pipes = {}
def chronos(name, obs, horizon):
    import torch
    from chronos import BaseChronosPipeline
    if name not in _pipes:
        _pipes[name] = BaseChronosPipeline.from_pretrained(name, device_map="cpu", torch_dtype=torch.float32)
    pipe, out = _pipes[name], []
    for s in range(0, len(obs), 64):
        batch = [torch.tensor(o["ctx"][-CTX:], dtype=torch.float32) for o in obs[s:s + 64]]
        q, _ = pipe.predict_quantiles(batch, prediction_length=horizon, quantile_levels=QL)
        q = q.numpy() if hasattr(q, "numpy") else np.asarray(q)
        for k in range(len(batch)):
            v = np.maximum.accumulate(q[k].reshape(horizon, len(QL))[-1].astype(float))
            tgt = obs[s + k]["target"]
            cdf = QL[0] * 0.5 if tgt <= v[0] else (1 - (1 - QL[-1]) * 0.5 if tgt >= v[-1] else float(np.interp(tgt, v, QL)))
            out.append(1.0 - cdf)
    return out


def ag_tab(train, test, with_market, tag):
    from autogluon.tabular import TabularPredictor
    def frame(os_):
        return pd.DataFrame([{**o["f"], **({"market": o["market"]} if with_market else {}), "y": o["y"]} for o in os_])
    tr, te = frame(train), frame(test)
    pred = TabularPredictor(label="y", problem_type="binary", eval_metric="log_loss",
                            path=os.path.join(WORK, f"agtab-minute-{tag}"), verbosity=0)
    pred.fit(tr, presets="good_quality", time_limit=int(os.environ.get("AGTAB_LIMIT", 180)),
             excluded_model_types=["FASTAI"])
    proba = pred.predict_proba(te.drop(columns=["y"]))[1].to_numpy()
    try:
        imp = pred.feature_importance(te, silent=True)["importance"].head(5)
        imp = ", ".join(f"{k} {v:+.4f}" for k, v in imp.items())
    except Exception:
        imp = ""
    return list(proba), imp


def run_minute(rows, C, m, lines, out):
    obs = build(rows, C, m)
    half = len(obs) // 2
    train, test = obs[:half], obs[half:]
    y = [o["y"] for o in test]
    P = {"market": [o["market"] for o in test], "cushion": [o["cushion"] for o in test]}
    for name, model in (("chronos-bolt", "amazon/chronos-bolt-base"), ("chronos-2", "amazon/chronos-2")):
        t0 = time.time()
        P[name] = chronos(model, test, 15 - m)
        print(f"  minute {m}: {name} done in {time.time() - t0:.0f}s", file=sys.stderr)
    notes = {}
    for name, wm in (("ag-tab", False), ("ag-tab+mkt", True)):
        t0 = time.time()
        P[name], notes[name] = ag_tab(train, test, wm, f"{m}-{name}")
        print(f"  minute {m}: {name} done in {time.time() - t0:.0f}s", file=sys.stderr)

    lines += [f"## Minute {m}: {len(obs)} windows · fit {len(train)} ({test[0]['ticker'] if False else train[0]['ticker']} …) · test {len(test)}", "",
              "| model | acc | 95% CI | Brier | vs market (acc) | ΔBrier vs market [95% CI] |", "|---|---|---|---|---|---|"]
    base = metrics(P["market"], y)
    res = {}
    for name, p in P.items():
        mt = metrics(p, y)
        if name == "market":
            vs, db = "—", "—"
        else:
            pv, b, c = mcnemar_p(mt["hit"], base["hit"])
            d, lo, hi = brier_diff_ci(p, P["market"], y)
            vs, db = f"+{b}/−{c}, p={pv:.3f}", f"{d:+.4f} [{lo:+.4f}, {hi:+.4f}]"
        lines.append(f"| {name} | {mt['acc']:.1%} | {mt['ci'][0]:.0%}–{mt['ci'][1]:.0%} | {mt['brier']:.4f} | {vs} | {db} |")
        res[name] = {"acc": float(mt["acc"]), "brier": float(mt["brier"]), "ci": [float(x) for x in mt["ci"]]}
    for k, v in notes.items():
        if v:
            lines.append(f"  {k} top features: {v}")
    lines.append("")
    out[str(m)] = {"n": len(obs), "fit": len(train), "test": len(test), "results": res}


def main():
    minutes = [int(a) for a in sys.argv[1:] if a.isdigit()] or [1, 3, 4]
    rows = load("KXBTC15M")
    C = coinbase_closes(rows)
    stamp = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# Chronos + AutoGluon vs. the Kalshi price at minutes {', '.join(map(str, minutes))} — {stamp}", "",
             "Not pre-registered. Each contender sees what is known at minute m and calls the settlement; "
             "the baseline is Kalshi's own YES mid at the same minute. Brier: lower is better. "
             "'vs market' = windows only this model got right / only the market got right (exact McNemar p).", ""]
    out = {}
    for m in minutes:
        run_minute(rows, C, m, lines, out)
    md = os.path.join(HERE, "results", f"ml-test-minute-{stamp}-m{'-'.join(map(str, minutes))}.md")
    open(md, "w").write("\n".join(lines) + "\n")
    json.dump(out, open(md.replace(".md", ".json"), "w"), indent=2)
    print("\n".join(lines))
    print(f"\nwrote {md}", file=sys.stderr)


if __name__ == "__main__":
    main()
