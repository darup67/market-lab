#!/usr/bin/env python3
"""
Volatility vs. prediction accuracy on Kalshi 15-minute markets: BTC, gold, WTI.

Question: at the 1-, 3-, 4- and 5-minute marks of a window, does HIGHER or LOWER
volatility make the up/down call more often correct (and is the favourite better
or worse priced)?

Per window and mark m (end of minute m):
  volatility, all from the spot path known at the mark, log returns in bps per sqrt(minute):
    rv1, rv3, rv4, rv5   std of 1-, 3-, 4-, 5-minute candle returns over the trailing 60 minutes
                         (60, 20, 15, 12 candles), divided by sqrt(candle minutes) so they share a unit
    rv_win               realized volatility inside the window so far (1-minute returns since the open)
  predictions:
    market    Kalshi YES mid at the mark: up if > 50c          (the price everyone sees)
    gap       spot at the mark vs. the strike: up if above      (the naive call)
    cushion   P(up) = Phi(gap / sd_to_close), the kalshi agents' model (for Brier)
  outcome: settled up/down; plus EV of buying the market favourite at the ask (win - ask - fee)

Analysis per asset x mark x volatility measure:
  * quintiles of volatility: accuracy (market, gap), Brier, EV, median cushion |z|
  * Spearman correlation of volatility with "market call correct", with a day-block bootstrap CI
  * logistic regression of "market call correct" on standardized log volatility, alone and
    controlling for the cushion |z| (does volatility matter beyond how far price already is from the strike?)
  * Spearman matrix of rv1/rv3/rv4/rv5/rv_win (do the candle scales agree?)

Data: BTC windows + minute quotes from market-lab (data/KXBTC15M.jsonl) with Coinbase 1-minute spot;
gold/WTI windows from ~/kalshi-commodity-agent (settled-window archive + Kalshi candlesticks) with
Yahoo CME 1-minute futures (GC=F, CL=F; history only, ~7 days). Gold/WTI strikes are the Pyth price
at the open, so their gap is measured as the spot CHANGE since the open (the basis cancels).

Not pre-registered; windows overlap in time, so CIs come from resampling whole days.

    ~/.venvs/market-ml/bin/python research/vol_accuracy.py
"""
import importlib.util, json, math, os, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from statistics import NormalDist
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(HERE)
CACHE = os.path.join(HERE, "cache")
MARKS = [1, 3, 4, 5]
VOLS = ["rv1", "rv3", "rv4", "rv5", "rv_win"]
SIGMA_PROXY = {"btc": 8.61, "gold": 0.444, "wti": 0.022}   # $ RMS proxy error vs settlement (agents' backtests)
SEED = 11
N = NormalDist()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

btc_feeds = load_module("btc_feeds", os.path.expanduser("~/kalshi-btc-agent/feeds.py"))
com_feeds = load_module("com_feeds", os.path.expanduser("~/kalshi-commodity-agent/feeds.py"))


def ts(s):
    return int(datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def fee(p):
    p = min(max(p, .01), .99)
    return math.ceil(0.07 * p * (1 - p) * 100 - 1e-9) / 100


def cached(name, fn):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        return json.load(open(path))
    v = fn()
    json.dump(v, open(path, "w"))
    return v


# ── assets ──────────────────────────────────────────────────────────────────
def btc_windows():
    seen = {}
    for line in open(os.path.join(LAB, "data", "KXBTC15M.jsonl")):
        line = line.strip()
        if line:
            r = json.loads(line)
            if r.get("settled_yes") is not None and r.get("target") is not None:
                seen[r["ticker"]] = r
    rows = sorted(seen.values(), key=lambda r: r["open_time"])
    start, end = ts(rows[0]["open_time"]) - 7200, ts(rows[-1]["close_time"]) + 60
    spot = cached(f"vol-coinbase-{start}-{end}.json",
                  lambda: {str(t): v[3] for t, v in btc_feeds.coinbase_1m(start, end).items()})
    spot = {int(k): float(v) for k, v in spot.items()}
    out = []
    for r in rows:
        q = r.get("minute_quotes") or []
        quotes = {}
        for m in MARKS:
            if len(q) > m and q[m] and q[m][0] is not None and q[m][1] is not None and q[m][1] - q[m][0] <= 10:
                quotes[m] = (q[m][0] / 100, q[m][1] / 100)
        out.append(dict(ticker=r["ticker"], o=ts(r["open_time"]), y=int(bool(r["settled_yes"])),
                        strike=float(r["target"]), relative=False, quotes=quotes))
    return out, spot


def commodity_windows(name, series, yahoo_sym):
    bars = com_feeds.yahoo_1m(yahoo_sym, "7d")
    spot = {int(t): float(v[3]) for t, v in bars.items()}
    t0, t1 = min(spot), max(spot)
    arch = {}
    path = os.path.expanduser(f"~/kalshi-commodity-agent/data/windows-{name}.jsonl")
    for line in open(path):
        r = json.loads(line)
        arch[r["ticker"]] = r
    try:   # windows settled since the archive was last written
        for m in com_feeds.kalshi_settled(series, min_close_ts=t0):
            if m.get("result") in ("yes", "no") and m.get("floor_strike") is not None:
                arch.setdefault(m["ticker"], {"ticker": m["ticker"], "open_time": m["open_time"],
                                              "close_time": m["close_time"], "strike": m["floor_strike"],
                                              "settle": m.get("expiration_value"), "result": m["result"]})
    except Exception as e:
        print(f"  {name}: could not refresh settled windows ({e})", file=sys.stderr)
    rows = [r for r in arch.values() if t0 + 3700 <= ts(r["open_time"]) <= t1 - 900]
    rows.sort(key=lambda r: r["open_time"])
    cpath = os.path.expanduser(f"~/kalshi-commodity-agent/data/kalshi-candles-{name}.json")
    candles = json.load(open(cpath)) if os.path.exists(cpath) else {}
    todo = [r for r in rows if r["ticker"] not in candles]
    for k, r in enumerate(todo):
        try:
            candles[r["ticker"]] = com_feeds.kalshi_candles(series, r["ticker"], ts(r["open_time"]), ts(r["close_time"]))
        except Exception:
            continue
        time.sleep(0.12)
    if todo:
        json.dump(candles, open(cpath, "w"))
        print(f"  {name}: fetched {len(todo)} more Kalshi candle sets", file=sys.stderr)
    out = []
    for r in rows:
        o = ts(r["open_time"])
        c = candles.get(r["ticker"], {})
        quotes = {m: tuple(c[str(o + m * 60)]) for m in MARKS if str(o + m * 60) in c}
        out.append(dict(ticker=r["ticker"], o=o, y=int(r["result"] == "yes"), strike=float(r["strike"]),
                        relative=True, quotes=quotes))
    return out, spot


# ── features ────────────────────────────────────────────────────────────────
def scaled_std(closes, k):
    """std of k-minute log returns over the trailing 60 minutes, per sqrt(minute), in bps."""
    pts = closes[::-1][::k][::-1]          # every k-th close ending at the mark
    r = np.diff(np.log(pts))
    return float(np.std(r, ddof=1) / math.sqrt(k) * 1e4) if len(r) >= 8 else None


def observations(asset, windows, spot):
    obs = []
    for w in windows:
        o = w["o"]
        for m in MARKS:
            q = w["quotes"].get(m)
            if not q or not (0 < q[0] <= q[1] < 1):
                continue
            mark_bar = o + (m - 1) * 60                     # 1m bar closing at the mark
            trail = [spot.get(t) for t in range(mark_bar - 60 * 60, mark_bar + 60, 60)]   # 61 closes
            if any(v is None for v in trail):
                continue
            inwin = [spot.get(t) for t in range(o - 60, mark_bar + 60, 60)]               # open .. mark
            if any(v is None for v in inwin):
                continue
            px = trail[-1]
            gap = px - inwin[0] if w["relative"] else px - w["strike"]
            if gap == 0:
                continue
            feats = {f"rv{k}": scaled_std(trail, k) for k in (1, 3, 4, 5)}
            rw = np.diff(np.log(inwin))
            feats["rv_win"] = float(np.sqrt(np.mean(rw ** 2)) * 1e4)
            if any(v is None or v <= 0 for v in feats.values()):
                continue
            sig_dollar = float(np.std(np.diff(trail), ddof=1))
            left = (15 - m - 2 / 3) if asset == "btc" else (15 - m)
            sd = math.sqrt(sig_dollar ** 2 * max(left, 1 / 3) + SIGMA_PROXY[asset] ** 2)
            mid = (q[0] + q[1]) / 2
            fav_up = mid > 0.5
            ask = q[1] if fav_up else 1 - q[0]
            win_fav = int((w["y"] == 1) == fav_up)
            obs.append(dict(asset=asset, m=m, day=datetime.fromtimestamp(o, timezone.utc).strftime("%Y-%m-%d"),
                            y=w["y"], mid=mid, hit_mkt=win_fav, hit_gap=int((w["y"] == 1) == (gap > 0)),
                            p_cush=N.cdf(gap / sd), absz=abs(gap) / sd,
                            ev=win_fav - ask - fee(ask), ask=ask, **feats))
    return obs


# ── statistics ──────────────────────────────────────────────────────────────
def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"),) * 2
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def spearman(a, b):
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def logit(X, y, iters=50):
    X = np.column_stack([np.ones(len(y)), X])
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ b, -30, 30)))
        W = p * (1 - p) + 1e-9
        H = X.T @ (X * W[:, None]) + 1e-6 * np.eye(X.shape[1])
        step = np.linalg.solve(H, X.T @ (y - p))
        b += step
        if np.max(np.abs(step)) < 1e-8:
            break
    return b


def day_boot(obs, stat, reps=400):
    days = sorted({o["day"] for o in obs})
    by = defaultdict(list)
    for o in obs:
        by[o["day"]].append(o)
    rng = np.random.default_rng(SEED)
    vals = []
    for _ in range(reps):
        pick = [o for d in rng.choice(days, len(days)) for o in by[d]]
        try:
            v = stat(pick)
            if v == v:
                vals.append(v)
        except Exception:
            pass
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))) if len(vals) > 20 else (float("nan"),) * 2


def analyze(obs, vol):
    v = np.array([o[vol] for o in obs])
    lv = np.log(v)
    z = (lv - lv.mean()) / lv.std()
    hit = np.array([o["hit_mkt"] for o in obs], float)
    absz = np.array([o["absz"] for o in obs])
    qs = np.quantile(v, [0.2, 0.4, 0.6, 0.8])
    qi = np.searchsorted(qs, v, side="right")
    quint = []
    for k in range(5):
        sel = [o for o, i in zip(obs, qi) if i == k]
        n = len(sel)
        km = sum(o["hit_mkt"] for o in sel)
        lo, hi = wilson(km, n)
        quint.append(dict(q=k + 1, n=n, vol_med=float(np.median([o[vol] for o in sel])),
                          acc_mkt=km / n, ci=[lo, hi], acc_gap=sum(o["hit_gap"] for o in sel) / n,
                          brier_mkt=float(np.mean([(o["mid"] - o["y"]) ** 2 for o in sel])),
                          brier_cush=float(np.mean([(o["p_cush"] - o["y"]) ** 2 for o in sel])),
                          ev=float(np.mean([o["ev"] for o in sel])), absz_med=float(np.median([o["absz"] for o in sel]))))

    def rho(os_):
        return spearman(np.array([o[vol] for o in os_]), np.array([o["hit_mkt"] for o in os_]))

    def slope(os_, control):
        lv_ = np.log([o[vol] for o in os_])
        zz = (lv_ - lv.mean()) / lv.std()
        y_ = np.array([o["hit_mkt"] for o in os_], float)
        X = np.column_stack([zz, np.log1p([o["absz"] for o in os_])]) if control else zz[:, None]
        return float(logit(X, y_)[1])

    def ev_rho(os_):
        return spearman(np.array([o[vol] for o in os_]), np.array([o["ev"] for o in os_]))

    return dict(
        n=len(obs), quintiles=quint,
        rho=rho(obs), rho_ci=day_boot(obs, rho),
        slope_raw=slope(obs, False), slope_raw_ci=day_boot(obs, lambda os_: slope(os_, False)),
        slope_ctrl=slope(obs, True), slope_ctrl_ci=day_boot(obs, lambda os_: slope(os_, True)),
        ev_rho=ev_rho(obs), ev_rho_ci=day_boot(obs, ev_rho),
        top_vs_bottom=quint[4]["acc_mkt"] - quint[0]["acc_mkt"])


def verdict(r):
    lo, hi = r["slope_raw_ci"]
    if lo > 0:
        return "higher vol → MORE accurate"
    if hi < 0:
        return "higher vol → LESS accurate"
    return "no clear effect"


# ── chart ───────────────────────────────────────────────────────────────────
def chart(results, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    assets = [a for a in ("btc", "gold", "wti") if a in results]
    fig, axes = plt.subplots(1, len(assets), figsize=(5.2 * len(assets), 4.2), sharey=True)
    axes = np.atleast_1d(axes)
    colors = {1: "#94a3b8", 3: "#6366f1", 4: "#2f5bea", 5: "#0f172a"}
    for ax, a in zip(axes, assets):
        for m in MARKS:
            r = results[a].get(str(m), {}).get("rv1")
            if not r:
                continue
            ax.plot(range(1, 6), [q["acc_mkt"] * 100 for q in r["quintiles"]], marker="o", color=colors[m], label=f"minute {m}")
        ax.set_title({"btc": "BTC", "gold": "Gold", "wti": "WTI oil"}[a])
        ax.set_xticks(range(1, 6), ["calm", "2", "3", "4", "volatile"])
        ax.set_xlabel("trailing 60-min volatility quintile (1-min candles)")
        ax.grid(alpha=.25)
    axes[0].set_ylabel("market favourite correct (%)")
    axes[-1].legend(frameon=False)
    fig.suptitle("Kalshi 15-minute markets: does volatility change how often the favourite is right?", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    return True



def pooled_section(data):
    """All four marks together (more power), every candle scale, and all three assets with vol z-scored within asset."""
    L = ["## Pooled over marks 1, 3, 4, 5", "",
         "Slope = change in log-odds that the market favourite is right per +1 SD of log volatility. "
         "'rv_win' (volatility inside the window so far) looks strongly positive but vanishes once the cushion is controlled for: "
         "a window that has already moved a lot simply has a bigger gap, which is not independent information.", "",
         "| asset | measure | windows×marks | slope [95% CI] | beyond cushion [95% CI] | EV vs vol ρ [95% CI] |", "|---|---|---|---|---|---|"]
    for a, obs in data.items():
        for vol in VOLS:
            r = analyze(obs, vol)
            L.append(f"| {a.upper()} | {vol} | {r['n']} | {r['slope_raw']:+.3f} [{r['slope_raw_ci'][0]:+.3f}, {r['slope_raw_ci'][1]:+.3f}] | "
                     f"{r['slope_ctrl']:+.3f} [{r['slope_ctrl_ci'][0]:+.3f}, {r['slope_ctrl_ci'][1]:+.3f}] | "
                     f"{r['ev_rho']:+.3f} [{r['ev_rho_ci'][0]:+.3f}, {r['ev_rho_ci'][1]:+.3f}] |")
    allobs = []
    for a, obs in data.items():
        lv = np.log([o["rv1"] for o in obs])
        mu, sd = lv.mean(), lv.std()
        allobs += [{**o, "zv": (l - mu) / sd} for o, l in zip(obs, lv)]
    y = np.array([o["hit_mkt"] for o in allobs], float)
    z = np.array([o["zv"] for o in allobs])
    b = logit(z[:, None], y)[1]
    stat = lambda os_: float(logit(np.array([o["zv"] for o in os_])[:, None], np.array([o["hit_mkt"] for o in os_], float))[1])
    lo, hi = day_boot(allobs, stat, 300)
    L += ["", f"All three assets together (1-minute-candle volatility z-scored within asset), {len(allobs)} observations: "
              f"slope {b:+.3f} [{lo:+.3f}, {hi:+.3f}].", "",
          "Buying the market favourite at the ask, by volatility tercile (all assets):", "",
          "| volatility | n | favourite correct | avg ask | EV per contract after fee |", "|---|---|---|---|---|"]
    t = np.quantile(z, [1 / 3, 2 / 3])
    ti = np.searchsorted(t, z)
    for k, lab in enumerate(("calm", "middle", "volatile")):
        idx = np.where(ti == k)[0]
        L.append(f"| {lab} | {len(idx)} | {y[idx].mean():.1%} | {np.mean([allobs[i]['ask'] for i in idx]):.3f} | {np.mean([allobs[i]['ev'] for i in idx]) * 100:+.2f}¢ |")
    L.append("")
    return L


# ── main ────────────────────────────────────────────────────────────────────
def main():
    stamp = datetime.now().strftime("%Y-%m-%d")
    data = {}
    print("loading BTC ...", file=sys.stderr)
    w, s = btc_windows()
    data["btc"] = observations("btc", w, s)
    for name, series, sym in (("gold", "KXGOLD15M", "GC=F"), ("wti", "KXWTI15M", "CL=F")):
        print(f"loading {name} ...", file=sys.stderr)
        w, s = commodity_windows(name, series, sym)
        data[name] = observations(name, w, s)

    results, lines = {}, [f"# Volatility vs. prediction accuracy — Kalshi 15-minute BTC, gold, WTI — {stamp}", "",
                          "Not pre-registered. Market = Kalshi's favourite at the mark; gap = spot vs. strike at the mark; "
                          "volatility from the spot path known at the mark (bps per √min). CIs resample whole days.", ""]
    summary = ["## Summary: is higher volatility better or worse for the up/down call?", "",
               "Logistic slope = change in log-odds of the market favourite being right per +1 SD of log volatility "
               "(1-minute candles, trailing 60 min). 'Beyond cushion' controls for how far price already is from the strike.", "",
               "| asset | mark | windows | acc calm → volatile (quintile 1 → 5) | slope [95% CI] | beyond cushion [95% CI] | EV vs vol ρ [95% CI] | verdict |",
               "|---|---|---|---|---|---|---|---|"]
    detail = []
    for a, obs in data.items():
        results[a] = {}
        for m in MARKS:
            om = [o for o in obs if o["m"] == m]
            if len(om) < 100:
                continue
            results[a][str(m)] = {}
            for vol in VOLS:
                results[a][str(m)][vol] = analyze(om, vol)
            r = results[a][str(m)]["rv1"]
            q1, q5 = r["quintiles"][0]["acc_mkt"], r["quintiles"][4]["acc_mkt"]
            summary.append(f"| {a.upper()} | {m} | {r['n']} | {q1:.1%} → {q5:.1%} | {r['slope_raw']:+.3f} [{r['slope_raw_ci'][0]:+.3f}, {r['slope_raw_ci'][1]:+.3f}] | "
                           f"{r['slope_ctrl']:+.3f} [{r['slope_ctrl_ci'][0]:+.3f}, {r['slope_ctrl_ci'][1]:+.3f}] | "
                           f"{r['ev_rho']:+.3f} [{r['ev_rho_ci'][0]:+.3f}, {r['ev_rho_ci'][1]:+.3f}] | {verdict(r)} |")
            detail += [f"### {a.upper()} · minute {m} · {r['n']} windows, {len({o['day'] for o in om})} days", "",
                       "Quintiles of 1-minute-candle volatility (trailing 60 min):", "",
                       "| quintile | n | median vol bps/√min | market correct [95% CI] | gap correct | Brier market | Brier cushion | EV fav @ ask | median cushion |z| |",
                       "|---|---|---|---|---|---|---|---|---|"]
            for q in r["quintiles"]:
                detail.append(f"| {q['q']} | {q['n']} | {q['vol_med']:.2f} | {q['acc_mkt']:.1%} [{q['ci'][0]:.0%}–{q['ci'][1]:.0%}] | {q['acc_gap']:.1%} | "
                              f"{q['brier_mkt']:.4f} | {q['brier_cush']:.4f} | {q['ev'] * 100:+.1f}¢ | {q['absz_med']:.2f} |")
            detail += ["", "Each volatility measure:", "",
                       "| measure | ρ(vol, correct) [95% CI] | slope [95% CI] | beyond cushion [95% CI] | top − bottom quintile |", "|---|---|---|---|---|"]
            for vol in VOLS:
                rv = results[a][str(m)][vol]
                detail.append(f"| {vol} | {rv['rho']:+.3f} [{rv['rho_ci'][0]:+.3f}, {rv['rho_ci'][1]:+.3f}] | {rv['slope_raw']:+.3f} [{rv['slope_raw_ci'][0]:+.3f}, {rv['slope_raw_ci'][1]:+.3f}] | "
                              f"{rv['slope_ctrl']:+.3f} [{rv['slope_ctrl_ci'][0]:+.3f}, {rv['slope_ctrl_ci'][1]:+.3f}] | {rv['top_vs_bottom'] * 100:+.1f} pts |")
            M = np.array([[o[v] for v in VOLS] for o in om])
            detail += ["", "How the volatility measures agree (Spearman):", "", "| | " + " | ".join(VOLS) + " |", "|---" * (len(VOLS) + 1) + "|"]
            for i, vi in enumerate(VOLS):
                detail.append(f"| {vi} | " + " | ".join(f"{spearman(M[:, i], M[:, j]):.2f}" for j in range(len(VOLS))) + " |")
            detail.append("")
    png = os.path.join(LAB, "results", f"vol-accuracy-{stamp}.png")
    has_chart = chart(results, png)
    lines += summary + [""] + pooled_section(data)
    if has_chart:
        lines += [f"![accuracy by volatility quintile](vol-accuracy-{stamp}.png)", ""]
    lines += detail
    md = os.path.join(LAB, "results", f"vol-accuracy-{stamp}.md")
    open(md, "w").write("\n".join(lines) + "\n")
    json.dump(results, open(md.replace(".md", ".json"), "w"), indent=1)
    print("\n".join(summary))
    print(f"\nwrote {md}" + (f" and {png}" if has_chart else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
