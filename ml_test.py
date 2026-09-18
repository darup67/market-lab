#!/usr/bin/env python3
"""
Chronos and AutoGluon vs. the opening print, on the Kalshi 15-minute windows.

The pre-registered test (report.py, 2026-09-07):
    fit on the first half of the windows, chronologically; predict the second;
    beat "just use the opening price". Stated in advance: Chronos LOSES,
    because the market prices ~8,000 trades per window.

Why a price forecaster can answer a binary question here: each window's
`target` is the spot price at its open, and `settled_yes` is exactly
"next window's target > this target" (1215/1215 consecutive BTC pairs). So the
targets form a 15-minute spot series, and P(up) is readable off a forecast's
quantiles at the current price.

Contenders, all restricted to what is known at the window's open:
    print         market's first trade, P = first/100          (the baseline)
    chronos-bolt  amazon/chronos-bolt-base, zero-shot on the spot series
    chronos-2     amazon/chronos-2, zero-shot on the spot series
    ag-ts         AutoGluon-TimeSeries fitted on the first half
    ag-tab        AutoGluon-Tabular, history features only
    ag-tab+print  AutoGluon-Tabular, history features + the opening print

Run with the ML venv (Python 3.11):
    ~/.venvs/market-ml/bin/python ml_test.py [SERIES ...]
"""
import json, os, sys, math, glob, time, random, warnings
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("MLTEST_WORK", "/tmp/market-lab-ml")
QL = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
CTX_MAX, CTX_MIN, LAGS = 512, 32, 16
STEP = timedelta(minutes=15)
SEED = 7


# ------------------------------------------------------------------ data
def load(series):
    rows = []
    for line in open(os.path.join(HERE, "data", f"{series}.jsonl")):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("settled_yes") is None or r.get("target") is None:
            continue
        r["t"] = datetime.fromisoformat(r["open_time"].replace("Z", "+00:00"))
        rows.append(r)
    rows.sort(key=lambda r: r["t"])
    return rows


def contiguous_context(rows, i, cap):
    """Targets of window i and the windows before it, stopping at any gap."""
    out = [rows[i]["target"]]
    j = i
    while j > 0 and len(out) < cap and rows[j]["t"] - rows[j - 1]["t"] == STEP:
        j -= 1
        out.append(rows[j]["target"])
    return out[::-1], j


# --------------------------------------------------------------- scoring
def p_up_from_quantiles(qvals, current):
    """P(next > current) by linear interpolation of the forecast CDF."""
    v = np.maximum.accumulate(np.asarray(qvals, float))
    if current <= v[0]:
        cdf = QL[0] * 0.5
    elif current >= v[-1]:
        cdf = 1 - (1 - QL[-1]) * 0.5
    else:
        cdf = float(np.interp(current, v, QL))
    return 1.0 - cdf


def wilson(k, n, z=1.96):
    if n == 0:
        return (0, 0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def metrics(p, y):
    p = np.clip(np.asarray(p, float), 1e-3, 1 - 1e-3)
    y = np.asarray(y, int)
    hit = ((p > 0.5) == (y == 1))
    return dict(acc=hit.mean(), ci=wilson(hit.sum(), len(y)),
                brier=np.mean((p - y) ** 2),
                logloss=-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)), hit=hit)


def mcnemar_p(hit_a, hit_b):
    """Exact two-sided binomial test on the discordant pairs."""
    b = int(np.sum(hit_a & ~hit_b)); c = int(np.sum(~hit_a & hit_b))
    n = b + c
    if n == 0:
        return 1.0, b, c
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail), b, c


def brier_diff_ci(p_a, p_b, y, reps=4000):
    rng = np.random.default_rng(SEED)
    pa, pb, y = map(np.asarray, (p_a, p_b, y))
    d = (pa - y) ** 2 - (pb - y) ** 2
    boots = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(reps)]
    return d.mean(), np.percentile(boots, 2.5), np.percentile(boots, 97.5)


# ---------------------------------------------------------------- models
_pipes = {}
def chronos(name, contexts, currents):
    import torch
    from chronos import BaseChronosPipeline
    if name not in _pipes:
        _pipes[name] = BaseChronosPipeline.from_pretrained(
            name, device_map="cpu", torch_dtype=torch.float32)
    pipe = _pipes[name]
    out = []
    for s in range(0, len(contexts), 64):
        batch = [torch.tensor(c, dtype=torch.float32) for c in contexts[s:s + 64]]
        q, _ = pipe.predict_quantiles(batch, prediction_length=1, quantile_levels=QL)
        q = q.numpy() if hasattr(q, "numpy") else np.asarray(q)
        for k in range(len(batch)):
            out.append(p_up_from_quantiles(q[k].reshape(-1, len(QL))[0], currents[s + k]))
    return out


def ag_timeseries(series, rows, train_idx, test_idx):
    from autogluon.timeseries import TimeSeriesDataFrame, TimeSeriesPredictor
    # Training data: contiguous runs of the first half, one item per run.
    recs, seg = [], 0
    for n, i in enumerate(train_idx):
        if n and rows[i]["t"] - rows[train_idx[n - 1]]["t"] != STEP:
            seg += 1
        recs.append((f"seg{seg}", rows[i]["t"].replace(tzinfo=None), rows[i]["target"]))
    train = pd.DataFrame(recs, columns=["item_id", "timestamp", "target"])
    train = train.groupby("item_id").filter(lambda g: len(g) >= CTX_MIN)
    tsdf = TimeSeriesDataFrame.from_data_frame(train)
    pred = TimeSeriesPredictor(prediction_length=1, freq="15min", quantile_levels=QL,
                               eval_metric="WQL", path=os.path.join(WORK, f"agts-{series}"),
                               verbosity=0)
    pred.fit(tsdf, presets="medium_quality", time_limit=int(os.environ.get("AGTS_LIMIT", 600)),
             random_seed=SEED)
    # One item per test window, holding only its own past (contiguous, capped).
    recs, cur = [], []
    for i in test_idx:
        ctx, j0 = contiguous_context(rows, i, 256)
        for k, v in enumerate(ctx):
            recs.append((f"w{i}", rows[j0 + k]["t"].replace(tzinfo=None), v))
        cur.append(rows[i]["target"])
    ctxdf = TimeSeriesDataFrame.from_data_frame(pd.DataFrame(recs, columns=["item_id", "timestamp", "target"]))
    fc = pred.predict(ctxdf)
    out = []
    for n, i in enumerate(test_idx):
        qv = [float(fc.loc[f"w{i}"][str(q)].iloc[0]) for q in QL]
        out.append(p_up_from_quantiles(qv, cur[n]))
    board = pred.leaderboard(silent=True)
    return out, board


def features(rows, i):
    ctx, j0 = contiguous_context(rows, i, LAGS + 1)
    if len(ctx) < LAGS + 1:
        return None
    lp = np.log(np.asarray(ctx))
    rets = np.diff(lp)
    prev = rows[i - 1]
    t_et = rows[i]["t"] - timedelta(hours=4)
    f = {f"r{k}": lp[-1] - lp[-1 - k] for k in (1, 2, 3, 4, 8, 16)}
    f.update(rv4=rets[-4:].std(), rv16=rets.std(),
             prev_first=prev["first"], prev_last=prev["last"], prev_range=prev["range"],
             prev_settled=int(prev["settled_yes"]), prev_trades=prev.get("trades", 0),
             prev_volume=prev.get("volume_fp", 0) or 0,
             hour_et=t_et.hour + t_et.minute / 60, dow=t_et.weekday())
    return f


def ag_tabular(series, rows, train_idx, test_idx, with_print):
    from autogluon.tabular import TabularPredictor
    def frame(idx):
        recs = []
        for i in idx:
            f = features(rows, i)
            if f is None:
                return None
            if with_print:
                f["first"] = rows[i]["first"]
            f["y"] = int(rows[i]["settled_yes"])
            recs.append(f)
        return pd.DataFrame(recs)
    tr = pd.DataFrame([{**features(rows, i), **({"first": rows[i]["first"]} if with_print else {}),
                        "y": int(rows[i]["settled_yes"])} for i in train_idx if features(rows, i)])
    te = frame(test_idx)
    tag = "print" if with_print else "hist"
    pred = TabularPredictor(label="y", problem_type="binary", eval_metric="log_loss",
                            path=os.path.join(WORK, f"agtab-{series}-{tag}"), verbosity=0)
    pred.fit(tr, presets="good_quality", time_limit=int(os.environ.get("AGTAB_LIMIT", 240)))
    proba = pred.predict_proba(te.drop(columns=["y"]))[1].to_numpy()
    imp = None
    try:
        imp = pred.feature_importance(te, silent=True)["importance"].head(6)
    except Exception:
        pass
    return list(proba), imp, len(tr)


# ------------------------------------------------------------------ run
def run(series):
    rows = load(series)
    half = len(rows) // 2
    usable = [i for i in range(len(rows))
              if len(contiguous_context(rows, i, CTX_MIN)[0]) >= CTX_MIN and features(rows, i)]
    train_idx = [i for i in range(half)]
    test_idx = [i for i in usable if i >= half]
    y = [int(rows[i]["settled_yes"]) for i in test_idx]
    print(f"\n=== {series}: {len(rows)} windows · fit {half} "
          f"({rows[0]['t']:%m-%d %H:%M} → {rows[half-1]['t']:%m-%d %H:%M}Z) · "
          f"test {len(test_idx)} ({rows[test_idx[0]]['t']:%m-%d} → {rows[test_idx[-1]]['t']:%m-%d})",
          flush=True)

    preds, notes = {}, {}
    preds["print"] = [rows[i]["first"] / 100 for i in test_idx]
    preds["coin"] = [0.5] * len(test_idx)   # reference: Brier 0.25, no skill
    ctxs = [contiguous_context(rows, i, CTX_MAX)[0] for i in test_idx]
    cur = [rows[i]["target"] for i in test_idx]
    for name, repo in (("chronos-bolt", "amazon/chronos-bolt-base"), ("chronos-2", "amazon/chronos-2")):
        t0 = time.time()
        try:
            preds[name] = chronos(repo, ctxs, cur)
            print(f"  {name:13s} done in {time.time()-t0:.0f}s", flush=True)
        except Exception as e:
            print(f"  {name:13s} FAILED: {e}", flush=True)
    if "--no-ag" not in sys.argv:
        t0 = time.time()
        try:
            preds["ag-ts"], board = ag_timeseries(series, rows, train_idx, test_idx)
            notes["ag-ts"] = board[["model", "score_val"]].head(5).to_string(index=False)
            print(f"  ag-ts         done in {time.time()-t0:.0f}s", flush=True)
        except Exception as e:
            print(f"  ag-ts         FAILED: {e}", flush=True)
        for wp in (False, True):
            name = "ag-tab+print" if wp else "ag-tab"
            t0 = time.time()
            try:
                preds[name], imp, ntr = ag_tabular(series, rows, train_idx, test_idx, wp)
                notes[name] = f"trained on {ntr} rows; top features: " + (
                    ", ".join(f"{k} {v:+.4f}" for k, v in imp.items()) if imp is not None else "n/a")
                print(f"  {name:13s} done in {time.time()-t0:.0f}s", flush=True)
            except Exception as e:
                print(f"  {name:13s} FAILED: {e}", flush=True)

    base = metrics(preds["print"], y)
    res = []
    for name, p in preds.items():
        m = metrics(p, y)
        pv, b, c = mcnemar_p(m["hit"], base["hit"]) if name != "print" else (None, 0, 0)
        bd = brier_diff_ci(p, preds["print"], y) if name != "print" else None
        res.append(dict(series=series, model=name, n=len(y), acc=m["acc"], ci=m["ci"],
                        brier=m["brier"], logloss=m["logloss"], mcnemar_p=pv,
                        better_than_print=b, worse_than_print=c, brier_diff=bd,
                        mean_p=float(np.mean(p)), sd_p=float(np.std(p))))
    return res, notes, dict(n_total=len(rows), n_fit=half, n_test=len(test_idx),
                            base_rate=float(np.mean(y)))


def fmt(res):
    L = [f"| model | acc | 95% CI | Brier | log loss | vs print (acc) | ΔBrier vs print [95% CI] | spread of P |",
         "|---|---|---|---|---|---|---|---|"]
    for r in res:
        vs = "—" if r["mcnemar_p"] is None else f"+{r['better_than_print']}/−{r['worse_than_print']}, p={r['mcnemar_p']:.3f}"
        bd = "—" if r["brier_diff"] is None else f"{r['brier_diff'][0]:+.4f} [{r['brier_diff'][1]:+.4f}, {r['brier_diff'][2]:+.4f}]"
        L.append(f"| {r['model']} | {r['acc']*100:.1f}% | {r['ci'][0]*100:.0f}–{r['ci'][1]*100:.0f}% | "
                 f"{r['brier']:.4f} | {r['logloss']:.4f} | {vs} | {bd} | {r['sd_p']:.3f} |")
    return "\n".join(L)


if __name__ == "__main__":
    random.seed(SEED); np.random.seed(SEED)
    os.makedirs(WORK, exist_ok=True)
    series = [a for a in sys.argv[1:] if not a.startswith("--")] or \
             ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M"]
    out = {}
    for s in series:
        res, notes, info = run(s)
        out[s] = dict(info=info, results=res, notes=notes)
        print(fmt(res)); [print(f"  {k}: {v}") for k, v in notes.items()]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    path = os.path.join(HERE, "results", f"ml-test-{stamp}.json")
    json.dump(out, open(path, "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print("\nwrote", path)
