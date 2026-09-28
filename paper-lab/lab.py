#!/usr/bin/env python3
"""Paper-trading lab for BTC, ETH, MNQ, MES, MGC, MCL. Simulated fills only;
nothing here can place an order.

  lab.py update      fetch new closed bars and headlines; Jev judges headlines if a key exists
  lab.py backtest    every strategy x instrument over all stored history, after costs
  lab.py paper       the same rules over bars that arrived after paper_start (true out of sample)
  lab.py calib       walk-forward Brier score: is the next hour predictable at all?
  lab.py step        update + paper (what launchd runs every 15 min); hourly, commits
                     and pushes paper-lab/data to the market-lab remote
  lab.py report      everything above as one report; --email sends it
"""
import json, os, subprocess, sys, time
from datetime import datetime, timezone

import pandas as pd

import calib, feeds, news, sim, strategies

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
WARMUP = 130   # the slowest rule (128-bar EMA) is ready by here
BASELINES = ("flat", "hold")


def cfg():
    with open(os.path.join(HERE, "config.json")) as f:
        return json.load(f)


def log(msg):
    print(f"{datetime.now():%m-%d %H:%M:%S} {msg}", flush=True)


def ts(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%b %d %H:%M")


def update(c):
    for name, inst in c["instruments"].items():
        try:
            total, added = feeds.update(name, inst, c["bar_seconds"])
            log(f"bars {name}: +{added} ({total} stored)")
        except Exception as e:
            log(f"bars {name}: update failed {e!r}")
    n = news.fetch(c["news"]["feeds"])
    j = news.judge(c["news"]["judge_per_run"])
    log(f"news: +{n} headlines, {j} judged by Jev")


def run_all(c, start_at=None):
    """{instrument: {strategy: summary}} starting at WARMUP, or at the first bar >= start_at."""
    out = {}
    for name, inst in c["instruments"].items():
        bars = feeds.load(name)
        if len(bars) <= WARMUP:
            continue
        start = WARMUP
        if start_at is not None:
            start = next((i for i, b in enumerate(bars) if b["t"] >= start_at), len(bars))
            if start < WARMUP or start >= len(bars):
                continue
        df = pd.DataFrame(bars)
        out[name] = {s: sim.run(bars, strategies.targets(s, df, c["strategies"][s]), inst, c, start)
                     for s in c["strategies"] if not s.startswith("_")}
    return out


def gaps(c):
    """Futures bars that open > 4 ATR from the prior close: rolls, halts, weekend news."""
    out = {}
    for name, inst in c["instruments"].items():
        if inst["kind"] != "future":
            continue
        df = pd.DataFrame(feeds.load(name))
        if df.empty:
            continue
        atr = (df["h"] - df["l"]).rolling(14).mean().shift(1)
        g = (df["o"] - df["c"].shift(1)).abs() > 4 * atr
        out[name] = [ts(t) for t in df.loc[g, "t"]]
    return out


def table(res, title):
    lines = [title, f"{'':<5} {'strategy':<10} {'net $':>9} {'ret%':>7} {'1st half':>9} {'2nd half':>9} "
             f"{'maxDD%':>7} {'trades':>6} {'costs $':>8} {'in mkt%':>7} {'sharpe':>6}  notes"]
    for inst, rows in res.items():
        for s, r in rows.items():
            if not r.get("bars"):
                continue
            ok = (r["net"] > max(0, rows["hold"]["net"]) and r["first_half"] > 0 and r["second_half"] > 0)
            beat = "" if s in BASELINES else ("PASSES: beats flat+hold, both halves" if ok else "no edge")
            notes = ", ".join(x for x in (beat, f"KILLED: {r['killed']}" if r["killed"] else "",
                                          f"{r['daily_halts']} daily halts" if r["daily_halts"] else "") if x)
            lines.append(f"{inst:<5} {s:<10} {r['net']:>9,.0f} {r['ret_pct']:>7.1f} {r['first_half']:>9,.0f} "
                         f"{r['second_half']:>9,.0f} {r['max_dd_pct']:>7.1f} {r['trades']:>6} {r['costs']:>8,.0f} "
                         f"{r['exposure_pct']:>7.0f} {str(r['sharpe']):>6}  {notes}")
        first = next(iter(rows.values()))
        lines.append(f"      {ts(first['from'])} to {ts(first['to'])} UTC, {first['bars']} bars, {first['days']} days")
    return "\n".join(lines)


def autocommit():
    """Commit and push paper-lab/data, like market-lab's recorders do for theirs.

    Yahoo keeps only ~60 days of 15m futures bars, so these files are the only
    long-run copy. Only paper-lab/data is staged, never anything else in the
    repo. A git failure (e.g. a recorder holding index.lock) is logged and
    retried next hour: it must never break the paper step.
    """
    def git(*a):
        r = subprocess.run(["git", *a], cwd=HERE, capture_output=True, text=True, timeout=60)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    try:
        ok, out = git("status", "--porcelain", "data")
        if ok and out:
            ok, out = git("add", "data")
            if ok:
                ok, out = git("-c", "user.name=paper-lab", "-c", "user.email=paper-lab@localhost",
                              "commit", "-q", "-m", "paper-lab: bars and headlines", "--", "data")
            if not ok:
                return log(f"autocommit failed: {out[-200:]}")
        ok, ahead = git("rev-list", "--count", "@{u}..HEAD")
        if ok and ahead != "0":
            ok, out = git("push", "-q", "origin", "HEAD")
            log("autocommit: pushed" if ok else f"autocommit: push failed {out[-200:]}")
    except Exception as e:
        log(f"autocommit error {e!r}")


def save(name, obj):
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, name), "w") as f:
        json.dump(obj, f, indent=1)


def calib_text(c):
    lines = ["CALIBRATION: P(higher in 1h), walk-forward, out of sample. skill > 0 beats the base rate."]
    for name in c["instruments"]:
        bars = feeds.load(name)
        r = calib.walk_forward(pd.DataFrame(bars), c["calibration"]) if len(bars) > 2000 else None
        if not r:
            lines.append(f"  {name:<4} not enough history yet")
            continue
        b = "  ".join(f"{x['range']}: {x['pred']:.2f}->{x['actual']:.2f} (n{x['n']})" for x in r["buckets"])
        lines.append(f"  {name:<4} n={r['n']:<5} Brier {r['brier_model']:.4f} vs base rate {r['brier_clim']:.4f}  "
                     f"skill {r['skill']:+.4f}   {b}")
    return "\n".join(lines)


def report(c):
    bt, pp = run_all(c), run_all(c, datetime.fromisoformat(c["paper_start"].replace("Z", "+00:00")).timestamp())
    save("backtest.json", bt)
    save("paper.json", pp)
    parts = [
        f"PAPER LAB, rules v{c['rules_version']}, {datetime.now():%a %b %d %Y %H:%M}",
        "Simulated fills only, no broker connection. Every figure is after the estimated costs in config.json.\n",
        table(pp, f"PAPER (live, out of sample since {c['paper_start']})") if pp else
        f"PAPER: no bars since {c['paper_start']} yet",
        "",
        table(bt, "BACKTEST (all stored history; the rules were fixed before this data was looked at)"),
        "",
        calib_text(c),
    ]
    g = gaps(c)
    parts.append("\nFUTURES GAPS > 4 ATR (mostly Sunday reopens, which are real weekend risk; any on a roll date are "
                 "an artifact of the unadjusted =F series): " +
                 "; ".join(f"{k} {len(v)}" + (f" (latest {v[-1]})" if v else "") for k, v in g.items()))
    ev = news.evaluate({k: feeds.load(k) for k in c["instruments"]}, c["news"]["horizon_bars"])
    nj = len(news._read(news.JUDGED))
    parts.append(f"\nJEV NEWS (shadow): {len(news._read(news.NEWS))} headlines stored, {nj} judged. " +
                 ("Direction hit rate on market-moving headlines, 4h later (0.50 = coin flip): " +
                  ", ".join(f"{k} {v['hit_rate']:.0%} of {v['n']}" for k, v in ev.items())
                  if ev else "No scored headlines yet" + ("" if nj else " (no API key yet).")))
    parts.append("\nHow to read this: a strategy only counts if it beats BOTH flat and hold, after costs, in both "
                 "halves, and then keeps doing it in PAPER. A backtest win alone is not evidence. Most "
                 "rules fail this, and that is the lab working.")
    return "\n".join(parts)


def main():
    c = cfg()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "update":
        update(c)
    elif cmd == "backtest":
        r = run_all(c)
        save("backtest.json", r)
        print(table(r, "BACKTEST"))
    elif cmd == "paper":
        r = run_all(c, datetime.fromisoformat(c["paper_start"].replace("Z", "+00:00")).timestamp())
        save("paper.json", r)
        print(table(r, "PAPER") if r else "no bars since paper_start yet")
    elif cmd == "calib":
        print(calib_text(c))
    elif cmd == "step":
        update(c)
        r = run_all(c, datetime.fromisoformat(c["paper_start"].replace("Z", "+00:00")).timestamp())
        save("paper.json", r)
        live = ", ".join(f"{i} {s} {v['net']:+.0f}" for i, rows in r.items()
                         for s, v in rows.items() if s not in BASELINES and v.get("bars"))
        log(f"paper: {live}" if live else f"paper: waiting for the first bar after {c['paper_start']}")
        if datetime.now().minute < 15 and os.environ.get("PAPERLAB_AUTOCOMMIT", "1") != "0":
            autocommit()
    elif cmd == "report":
        text = report(c)
        print(text)
        if "--email" in sys.argv:
            r = subprocess.run([os.path.expanduser("~/.local/bin/node"), os.path.expanduser("~/flip-notifier/send-email.js"),
                                f"Paper lab weekly: rules v{c['rules_version']}", text],
                               capture_output=True, text=True, timeout=90)
            log("email " + ("sent" if r.returncode == 0 else f"FAILED {r.stderr[-200:]}"))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
