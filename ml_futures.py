#!/usr/bin/env python3
"""
Chronos and AutoGluon on the CME futures bars (NQ, ES, YM, GC, CL; 15-minute).

Two questions per symbol, each asked at a bar's close about the NEXT bar:

  direction   will the next bar close higher?
              baselines: best constant (fit-half base rate), momentum (last bar's sign)
  volatility  how large is the next bar's range? (log of (high-low)/close, bp)
              baselines: time-of-day average (expanding, same info as the models),
              last bar, EWMA, and time-of-day + EWMA of the deviation from it
              README: "the honest baseline for volatility is not a random walk
              but average volatility by hour of day"

Split as in ml_test.py: fit on the first half chronologically, test on the
second. Bars sit on a real 15-minute grid with NaN for halts and weekends, so
daily seasonality keeps its 96-bar period. A bar is only scored if the next
bar follows it directly (no forecasting across a halt).

Data caveat: Yahoo, ~10 min delayed; research data, not a signal source.
Live-quote snapshot rows (unaligned timestamps) are dropped on load.

    ~/.venvs/market-ml/bin/python ml_futures.py [NQ_F ES_F ...] [--no-ag]
"""
import json, os, sys, math, time, warnings
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd
from ml_test import QL, wilson, mcnemar_p, p_up_from_quantiles, SEED

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("MLTEST_WORK", "/tmp/market-lab-ml")
BAR = 900
CTX = 1024          # grid points of context (NaN included)
FLOOR_BP = 0.5      # log of a zero range is undefined; floor it


def load(sym):
    rows = {}
    for line in open(os.path.join(HERE, "data", "futures", f"{sym}.jsonl")):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("t", 1) % BAR == 0 and r.get("c"):
            rows[r["t"]] = r
    t0, t1 = min(rows), max(rows)
    grid = np.arange(t0, t1 + BAR, BAR)
    df = pd.DataFrame({"t": grid})
    for k in ("o", "h", "l", "c", "v"):
        df[k] = [rows[t][k] if t in rows else np.nan for t in grid]
    df["have"] = df["c"].notna()
    df["lr"] = np.log(np.maximum((df["h"] - df["l"]) / df["c"] * 1e4, FLOOR_BP))
    et = pd.to_datetime(df["t"], unit="s", utc=True).dt.tz_convert("America/New_York")
    df["slot"] = et.dt.hour * 4 + et.dt.minute // 15
    df["dow"] = et.dt.weekday
    df["ts"] = pd.to_datetime(df["t"], unit="s")
    return df


def scored_points(df):
    """Bars with a direct successor; the split is by count of real bars."""
    have = df["have"].to_numpy()
    real = np.flatnonzero(have)
    half_t = df["t"].iloc[real[len(real) // 2]]
    pts = [i for i in real[:-1] if have[i + 1]]
    fit = [i for i in pts if df["t"].iloc[i] < half_t]
    test = [i for i in pts if df["t"].iloc[i] >= half_t]
    return fit, test, half_t


def expanding_slot_means(df):
    """sm_next[i]: mean log range of slot(i+1) over bars <= i; sm_at[j]: of slot(j) over bars < j."""
    lr = df["lr"].to_numpy()
    slots = df["slot"].to_numpy()
    ssum, scnt, run_sum, run_n = {}, {}, 0.0, 0
    sm_next = np.full(len(lr), np.nan); sm_at = np.full(len(lr), np.nan)
    for j in range(len(lr)):
        glob = run_sum / run_n if run_n else np.nan
        if scnt.get(slots[j]):
            sm_at[j] = ssum[slots[j]] / scnt[slots[j]]
        else:
            sm_at[j] = glob
        if not np.isnan(lr[j]):
            ssum[slots[j]] = ssum.get(slots[j], 0) + lr[j]; scnt[slots[j]] = scnt.get(slots[j], 0) + 1
            run_sum += lr[j]; run_n += 1
        if j + 1 < len(lr):
            k = slots[j + 1]
            sm_next[j] = ssum[k] / scnt[k] if scnt.get(k) else run_sum / max(run_n, 1)
    return sm_next, sm_at


# ---------------------------------------------------------------- models
_pipes = {}
def chronos_q(repo, series_list):
    import torch
    from chronos import BaseChronosPipeline
    if repo not in _pipes:
        _pipes[repo] = BaseChronosPipeline.from_pretrained(repo, device_map="cpu",
                                                           torch_dtype=torch.float32)
    out = []
    for s in range(0, len(series_list), 64):
        batch = [torch.tensor(x, dtype=torch.float32) for x in series_list[s:s + 64]]
        q, _ = _pipes[repo].predict_quantiles(batch, prediction_length=1, quantile_levels=QL)
        q = q.numpy() if hasattr(q, "numpy") else np.asarray(q)
        out.extend(q[k].reshape(-1, len(QL))[0] for k in range(len(batch)))
    return out


def ag_ts_q(tag, df, col, fit_idx, test_idx):
    """AutoGluon-TS fitted on the fit half; one item per test point."""
    from autogluon.timeseries import TimeSeriesDataFrame, TimeSeriesPredictor
    last_fit = fit_idx[-1]
    tr = pd.DataFrame({"item_id": "fit", "timestamp": df["ts"].iloc[:last_fit + 1],
                       "target": df[col].iloc[:last_fit + 1]})
    pred = TimeSeriesPredictor(prediction_length=1, freq="15min", quantile_levels=QL,
                               eval_metric="WQL", path=os.path.join(WORK, f"fut-{tag}"), verbosity=0)
    pred.fit(TimeSeriesDataFrame.from_data_frame(tr), presets="medium_quality",
             time_limit=int(os.environ.get("AGTS_LIMIT", 300)), random_seed=SEED)
    parts = []
    for i in test_idx:
        lo = max(0, i + 1 - 512)
        parts.append(pd.DataFrame({"item_id": f"p{i}", "timestamp": df["ts"].iloc[lo:i + 1],
                                   "target": df[col].iloc[lo:i + 1]}))
    fc = pred.predict(TimeSeriesDataFrame.from_data_frame(pd.concat(parts)))
    board = pred.leaderboard(silent=True)[["model", "score_val"]].head(4)
    return [np.array([fc.loc[f"p{i}"][str(q)].iloc[0] for q in QL]) for i in test_idx], board


def tab_features(df, i, sm_next):
    c, lr = df["c"].to_numpy(), df["lr"].to_numpy()
    # Last 17 real bars, however far back (a weekend is ~200 empty grid slots).
    prev = [j for j in range(i, max(-1, i - 600), -1) if not np.isnan(c[j])][:17]
    if len(prev) < 17:
        return None
    cc, ll = c[prev], lr[prev]            # newest first
    f = {f"r{k}": math.log(cc[0] / cc[k]) for k in (1, 2, 4, 8, 16)}
    f.update({f"lr{k}": ll[k] for k in (0, 1, 2, 3)})
    f.update(lr_m4=ll[:4].mean(), lr_m16=ll[:16].mean(),
             absr1=abs(f["r1"]), vol_rel=(df["v"].iloc[i] or 0) / (np.nanmean(df["v"].iloc[max(0, i - 96):i + 1]) or 1),
             slot=df["slot"].iloc[i], next_slot=df["slot"].iloc[i + 1], dow=df["dow"].iloc[i],
             slot_mean_next=sm_next[i],
             after_gap=int(i == 0 or np.isnan(c[i - 1])))
    return f


def ag_tab(tag, X_fit, y_fit, X_test, problem):
    from autogluon.tabular import TabularPredictor
    tr = X_fit.assign(y=y_fit)
    pred = TabularPredictor(label="y", problem_type=problem,
                            eval_metric="log_loss" if problem == "binary" else "mean_absolute_error",
                            path=os.path.join(WORK, f"futtab-{tag}"), verbosity=0)
    pred.fit(tr, presets="good_quality", time_limit=int(os.environ.get("AGTAB_LIMIT", 150)))
    if problem == "binary":
        return pred.predict_proba(X_test)[1].to_numpy()
    return pred.predict(X_test).to_numpy()


# --------------------------------------------------------------- scoring
def dir_metrics(p, y):
    p = np.clip(np.asarray(p, float), 1e-3, 1 - 1e-3); y = np.asarray(y, int)
    hit = (p > 0.5) == (y == 1)
    return dict(acc=hit.mean(), ci=wilson(hit.sum(), len(y)), brier=np.mean((p - y) ** 2),
                logloss=-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)), hit=hit)


def boot_ci(d, reps=4000):
    rng = np.random.default_rng(SEED)
    b = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(reps)]
    return float(d.mean()), float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def spearman(a, b):
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


# ------------------------------------------------------------------ run
def run(sym):
    df = load(sym)
    fit, test, half_t = scored_points(df)
    c, lr = df["c"].to_numpy(), df["lr"].to_numpy()
    sm_next, sm_at = expanding_slot_means(df)
    glob_mean = float(np.nanmean(lr[:fit[-1] + 2]))
    ctx = lambda col, i: df[col].to_numpy()[max(0, i + 1 - CTX):i + 1]
    fmt_t = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%m-%d")
    print(f"\n=== {sym}: {int(df['have'].sum())} bars · fit {len(fit)} "
          f"({fmt_t(df['t'].iloc[fit[0]])} → {fmt_t(df['t'].iloc[fit[-1]])}) · test {len(test)} "
          f"({fmt_t(half_t)} → {fmt_t(df['t'].iloc[test[-1]])})", flush=True)
    ag = "--no-ag" not in sys.argv

    # ---- direction
    keep = [i for i in test if c[i + 1] != c[i]]
    y = np.array([int(c[i + 1] > c[i]) for i in keep])
    base = np.mean([c[i + 1] > c[i] for i in fit if c[i + 1] != c[i]])
    P = {"constant": np.full(len(keep), base),
         "momentum": None}
    # Momentum as a probability: P(up next | last bar up/down), measured in the fit half.
    up_last = lambda i: i > 0 and not np.isnan(c[i - 1]) and c[i] > c[i - 1]
    fk = [i for i in fit if c[i + 1] != c[i]]
    pu = np.mean([c[i + 1] > c[i] for i in fk if up_last(i)])
    pd_ = np.mean([c[i + 1] > c[i] for i in fk if not up_last(i)])
    P["momentum"] = np.array([pu if up_last(i) else pd_ for i in keep])
    logc = lambda i: np.log(ctx("c", i))
    for name, repo in (("chronos-bolt", "amazon/chronos-bolt-base"), ("chronos-2", "amazon/chronos-2")):
        qs = chronos_q(repo, [logc(i) for i in keep])
        P[name] = np.array([p_up_from_quantiles(q, math.log(c[i])) for q, i in zip(qs, keep)])
    notes = {}
    if ag:
        df["logc"] = np.log(df["c"])
        qs, board = ag_ts_q(f"{sym}-dir", df, "logc", fit, keep)
        P["ag-ts"] = np.array([p_up_from_quantiles(q, math.log(c[i])) for q, i in zip(qs, keep)])
        notes["dir ag-ts best"] = board.iloc[0]["model"]
        feats = {i: tab_features(df, i, sm_next) for i in fit + keep}
        fi = [i for i in fit if feats[i] and c[i + 1] != c[i]]
        ti_ok = all(feats[i] for i in keep)
        if not ti_ok:
            print(f"  ag-tab direction SKIPPED: {sum(not feats[i] for i in keep)} test bars lack features", flush=True)
        else:
            P["ag-tab"] = ag_tab(f"{sym}-dir", pd.DataFrame([feats[i] for i in fi]),
                                 [int(c[i + 1] > c[i]) for i in fi],
                                 pd.DataFrame([feats[i] for i in keep]), "binary")
    dres = {}
    ref = dir_metrics(P["constant"], y)
    for k, p in P.items():
        m = dir_metrics(p, y)
        pv, b, w = mcnemar_p(m["hit"], ref["hit"]) if k != "constant" else (None, 0, 0)
        dres[k] = dict(acc=m["acc"], ci=m["ci"], brier=m["brier"], logloss=m["logloss"],
                       vs_const=(pv, b, w),
                       dbrier=boot_ci((np.clip(p, 1e-3, 1 - 1e-3) - y) ** 2 - (P["constant"] - y) ** 2)
                       if k != "constant" else None)

    # ---- volatility
    yv = lr[[i + 1 for i in test]]
    V = {"time-of-day": sm_next[test], "last bar": lr[test]}
    ew, out = glob_mean, {}
    for j in range(len(lr)):
        if not np.isnan(lr[j]):
            ew = 0.1 * lr[j] + 0.9 * ew
        out[j] = ew
    V["ewma"] = np.array([out[i] for i in test])
    # Strongest simple baseline: time-of-day shape + persistence of the recent
    # deviation from it (residual vs. the means known before each bar).
    def tod_ew(alpha):
        e, res = 0.0, np.zeros(len(lr))
        for j in range(len(lr)):
            if not np.isnan(lr[j]) and not np.isnan(sm_at[j]):
                e = alpha * (lr[j] - sm_at[j]) + (1 - alpha) * e
            res[j] = e
        return res
    warm = [i for i in fit if i > fit[0] + 96]          # skip the cold start
    fit_err = {}
    for a_ in (0.05, 0.1, 0.2, 0.3, 0.5):
        r_ = tod_ew(a_)
        fit_err[a_] = np.mean([(sm_next[i] + r_[i] - lr[i + 1]) ** 2 for i in warm])
    a_best = min(fit_err, key=fit_err.get)
    te = tod_ew(a_best)
    V["tod+ewma"] = sm_next[test] + te[test]
    notes["tod+ewma alpha"] = a_best
    for name, repo in (("chronos-bolt", "amazon/chronos-bolt-base"), ("chronos-2", "amazon/chronos-2")):
        qs = chronos_q(repo, [ctx("lr", i) for i in test])
        V[name] = np.array([q[QL.index(0.5)] for q in qs])
    if ag:
        qs, board = ag_ts_q(f"{sym}-vol", df, "lr", fit, test)
        V["ag-ts"] = np.array([q[QL.index(0.5)] for q in qs])
        notes["vol ag-ts best"] = board.iloc[0]["model"]
        feats = {i: tab_features(df, i, sm_next) for i in fit + test}
        fi = [i for i in fit if feats[i]]
        if not all(feats[i] for i in test):
            print(f"  ag-tab volatility SKIPPED: {sum(not feats[i] for i in test)} test bars lack features", flush=True)
        else:
            V["ag-tab"] = ag_tab(f"{sym}-vol", pd.DataFrame([feats[i] for i in fi]),
                                 [lr[i + 1] for i in fi], pd.DataFrame([feats[i] for i in test]),
                                 "regression")
    vres = {}
    e_ref = np.abs(V["time-of-day"] - yv)
    mse_ref = np.mean((V["time-of-day"] - yv) ** 2)
    for k, p in V.items():
        e = np.abs(p - yv)
        vres[k] = dict(mae=float(e.mean()), rmse=float(np.sqrt(np.mean((p - yv) ** 2))),
                       skill=float(1 - np.mean((p - yv) ** 2) / mse_ref), rho=spearman(p, yv),
                       dmae=boot_ci(e - e_ref) if k != "time-of-day" else None,
                       dmae_best=boot_ci(e - np.abs(V["tod+ewma"] - yv)) if k not in ("time-of-day", "tod+ewma") else None)
    info = dict(bars=int(df["have"].sum()), fit=len(fit), test=len(test), dir_n=len(keep),
                dir_base_fit=float(base), dir_base_test=float(y.mean()))
    return dres, vres, notes, info


def show(sym, dres, vres, notes, info):
    best_const = max(info["dir_base_test"], 1 - info["dir_base_test"])
    L = [f"\n## {sym}  ({info['bars']} bars; test {info['dir_n']} direction / {info['test']} volatility)",
         f"\nDirection — up-rate in test {info['dir_base_test']*100:.1f}% (best constant {best_const*100:.1f}%)\n",
         "| model | acc | 95% CI | Brier | vs constant (acc) | ΔBrier vs constant [95% CI] |", "|---|---|---|---|---|---|"]
    for k, r in dres.items():
        vs = "—" if r["vs_const"][0] is None else f"+{r['vs_const'][1]}/−{r['vs_const'][2]}, p={r['vs_const'][0]:.3f}"
        db = "—" if r["dbrier"] is None else f"{r['dbrier'][0]:+.4f} [{r['dbrier'][1]:+.4f}, {r['dbrier'][2]:+.4f}]"
        L.append(f"| {k} | {r['acc']*100:.1f}% | {r['ci'][0]*100:.0f}–{r['ci'][1]*100:.0f}% | {r['brier']:.4f} | {vs} | {db} |")
    L += ["\nVolatility — next bar's log range; skill = 1 − MSE/MSE(time-of-day)\n",
          "| model | MAE | RMSE | skill vs time-of-day | Spearman ρ | ΔMAE vs time-of-day [95% CI] | ΔMAE vs tod+ewma [95% CI] |",
          "|---|---|---|---|---|---|---|"]
    for k, r in vres.items():
        dm = "—" if r["dmae"] is None else f"{r['dmae'][0]:+.4f} [{r['dmae'][1]:+.4f}, {r['dmae'][2]:+.4f}]"
        db = "—" if r.get("dmae_best") is None else f"{r['dmae_best'][0]:+.4f} [{r['dmae_best'][1]:+.4f}, {r['dmae_best'][2]:+.4f}]"
        L.append(f"| {k} | {r['mae']:.4f} | {r['rmse']:.4f} | {r['skill']*100:+.1f}% | {r['rho']:.3f} | {dm} | {db} |")
    if notes:
        L.append("\n" + " · ".join(f"{k}: {v}" for k, v in notes.items()))
    return "\n".join(L)


if __name__ == "__main__":
    np.random.seed(SEED)
    os.makedirs(WORK, exist_ok=True)
    syms = [a for a in sys.argv[1:] if not a.startswith("--")] or ["NQ_F", "ES_F", "YM_F", "GC_F", "CL_F"]
    out, text = {}, []
    for s in syms:
        t0 = time.time()
        dres, vres, notes, info = run(s)
        block = show(s, dres, vres, notes, info)
        print(block, f"\n  ({time.time()-t0:.0f}s)", flush=True)
        text.append(block)
        out[s] = dict(info=info, direction=dres, volatility=vres, notes=notes)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    jp = os.path.join(HERE, "results", f"ml-futures-{stamp}.json")
    json.dump(out, open(jp, "w"), indent=1,
              default=lambda o: o.tolist() if hasattr(o, "tolist") else (None if o is None else str(o)))
    open(os.path.join(HERE, "results", f"ml-futures-{stamp}.tables.md"), "w").write("\n".join(text) + "\n")
    print("\nwrote", jp)
