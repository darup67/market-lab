"""Is anything predictable? Walk-forward calibration of P(price higher in 1h).

A logistic regression on causal features, retrained weekly on everything
before the week it predicts, scored out of sample with the Brier score
against climatology: always predicting the training base rate.

Brier skill > 0 means better than the base rate. Near zero or negative means
no measurable edge, which is the usual answer at a 1-hour horizon. Only every
`horizon`-th bar is scored, so overlapping labels can't inflate the count.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def features(df):
    c, lr = df["c"], np.log(df["c"]).diff()
    f = pd.DataFrame(index=df.index)
    for n in (1, 4, 16, 96):
        f[f"r{n}"] = np.log(c / c.shift(n))
    f["vol16"], f["vol96"] = lr.rolling(16).std(), lr.rolling(96).std()
    f["z20"] = (c - c.rolling(20).mean()) / c.rolling(20).std()
    f["ema"] = (c.ewm(span=32, adjust=False).mean() - c.ewm(span=128, adjust=False).mean()) / c
    hi, lo = df["h"].rolling(96).max(), df["l"].rolling(96).min()
    f["don"] = (c - lo) / (hi - lo)
    hour = (df["t"] % 86400) / 3600
    f["hs"], f["hc"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    return f


def walk_forward(df, p):
    H, step, first = p["horizon_bars"], p["retrain_bars"], p["min_train_bars"]
    X = features(df)
    y = (df["c"].shift(-H) > df["c"]).astype(float)
    y[df["c"].shift(-H).isna()] = np.nan
    ok = X.notna().all(axis=1) & y.notna()
    preds, clims, truth = [], [], []
    end = first
    while end < len(df) - H:
        tr = ok & (df.index < end - H)
        te_idx = [i for i in range(end, min(end + step, len(df) - H), H) if ok[i]]
        if tr.sum() >= 200 and te_idx:
            m = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=500))
            m.fit(X[tr], y[tr])
            preds += list(m.predict_proba(X.loc[te_idx])[:, 1])
            clims += [float(y[tr].mean())] * len(te_idx)
            truth += list(y.loc[te_idx])
        end += step
    if not truth:
        return None
    pr, cl, tr = map(np.array, (preds, clims, truth))
    b_model, b_clim = np.mean((pr - tr) ** 2), np.mean((cl - tr) ** 2)
    buckets = []
    for lo in (0.0, 0.45, 0.5, 0.55):
        hi = {0.0: 0.45, 0.45: 0.5, 0.5: 0.55, 0.55: 1.01}[lo]
        m = (pr >= lo) & (pr < hi)
        if m.sum():
            buckets.append({"range": f"{lo:.2f}-{min(hi, 1):.2f}", "n": int(m.sum()),
                            "pred": round(float(pr[m].mean()), 3), "actual": round(float(tr[m].mean()), 3)})
    return {"n": len(tr), "brier_model": round(float(b_model), 4), "brier_clim": round(float(b_clim), 4),
            "skill": round(float(1 - b_model / b_clim), 4), "base_rate": round(float(tr.mean()), 3),
            "buckets": buckets}
