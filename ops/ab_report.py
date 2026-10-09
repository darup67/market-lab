#!/usr/bin/env python3
"""A/B test: guardrails ON (arm A) versus OFF (arm B) on Jev Markets and Jev Majors, running side by side on paper since T0 (ops/data/ab_config.json).
  A = a separate copy of the desk (~/jev-markets-guarded, ~/jev-majors-guarded) with every guardrail on
  B = the desk itself (~/jev-markets, ~/jev-majors) with every guardrail off
Both arms see the same markets, use the same Jev judgements, sizing and exits; only the guardrails differ.
This script reads both arms' paper books (read-only), computes win rate, profit factor (gross profit / gross loss), payoff ratio (average win / average loss), expectancy, net P&L and
max drawdown, a bootstrap interval for the difference in per-trade result, and records everything in the EXISTING export/ store of each desk's git repo (the same folder that already
holds trades.jsonl): export/ab_config.json, export/ab_trades.jsonl, export/ab_summary.json, then commits and pushes.
  ab_report.py            record and commit
  ab_report.py --print    just print the comparison"""
import json, math, os, random, sqlite3, statistics as st, subprocess, sys, time

HOME = os.path.expanduser("~")
CFG = json.load(open(os.path.join(HOME, "market-lab", "ops", "data", "ab_config.json")))
T0 = CFG["T0"]
DESKS = {"markets": {"A": f"{HOME}/jev-markets-guarded", "B": f"{HOME}/jev-markets", "sym": "symbol", "risk": "risk_usd", "unit": "R", "key": "r", "bank": 50000.0, "status": "closed", "repo": f"{HOME}/jev-markets"},
         "majors": {"A": f"{HOME}/jev-majors-guarded", "B": f"{HOME}/jev-majors", "sym": "coin", "risk": "margin_usd", "unit": "ROE", "key": "roe", "bank": 1000.0, "status": "shadow_closed", "repo": f"{HOME}/jev-majors"}}


def trades(desk, arm):
    spec = DESKS[desk]
    db = sqlite3.connect(f"file:{spec[arm]}/data/desk.db?mode=ro", uri=True, timeout=10)
    out = []
    for oid, created, sym, side, pnl, close, upd in db.execute(f"SELECT id, created, {spec['sym']}, side, pnl_usd, close, updated FROM orders WHERE status=? AND created>=? ORDER BY updated", (spec["status"], T0)):
        c = json.loads(close or "{}")
        out.append({"arm": arm, "id": oid, "symbol": sym, "side": side, "opened": round(created, 1), "closed": round(upd or 0, 1), "pnl_usd": round(pnl or 0.0, 2),
                    spec["key"]: c.get(spec["key"]), "rule": (c.get("rule") or "")[:60], "held_min": c.get("held_minutes")})
    return out


def metrics(rows, desk):
    spec = DESKS[desk]
    p = [r["pnl_usd"] for r in rows]
    n = len(p)
    if not n:
        return {"n": 0}
    wins, losses = [x for x in p if x > 0], [x for x in p if x < 0]
    gp, gl = sum(wins), -sum(losses)
    cum, peak, dd = 0.0, 0.0, 0.0
    for x in p:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    unit = [r[spec["key"]] for r in rows if r.get(spec["key"]) is not None]
    days = max(1.0, (rows[-1]["closed"] - T0) / 86400)
    return {"n": n, "win_rate": round(len(wins) / n, 4), "net_usd": round(sum(p), 2), "return_pct": round(sum(p) / spec["bank"], 5), "profit_factor": round(gp / gl, 3) if gl else None,
            "avg_win_usd": round(gp / len(wins), 2) if wins else 0.0, "avg_loss_usd": round(-gl / len(losses), 2) if losses else 0.0,
            "payoff_ratio": round((gp / len(wins)) / (gl / len(losses)), 3) if wins and losses else None, "expectancy_usd": round(sum(p) / n, 2),
            f"mean_{spec['unit']}": round(st.mean(unit), 4) if unit else None, "max_drawdown_usd": round(dd, 2), "worst_trade_usd": round(min(p), 2), "best_trade_usd": round(max(p), 2),
            "trades_per_day": round(n / days, 1)}


def compare(a_rows, b_rows, desk):
    key = DESKS[desk]["key"]
    a = [r[key] for r in a_rows if r.get(key) is not None]
    b = [r[key] for r in b_rows if r.get(key) is not None]
    if len(a) < 30 or len(b) < 30:
        return {"verdict": f"too early: need 30+ closed trades in each arm (A {len(a)}, B {len(b)})", "n_a": len(a), "n_b": len(b)}
    random.seed(1)
    diffs = sorted(st.mean(random.choices(b, k=len(b))) - st.mean(random.choices(a, k=len(a))) for _ in range(3000))
    lo, hi = diffs[int(0.05 * len(diffs))], diffs[int(0.95 * len(diffs))]
    d = st.mean(b) - st.mean(a)
    v = ("B (unfiltered) is ahead and the 90% interval clears zero" if lo > 0 else "A (guarded) is ahead and the 90% interval clears zero" if hi < 0 else "no clear difference: the 90% interval for B minus A includes zero")
    return {"mean_diff_B_minus_A": round(d, 4), "interval_90": [round(lo, 4), round(hi, 4)], "unit": DESKS[desk]["unit"], "verdict": v, "n_a": len(a), "n_b": len(b)}


def record(desk, print_only=False):
    a, b = trades(desk, "A"), trades(desk, "B")
    out = {"desk": desk, "updated": time.strftime("%Y-%m-%d %H:%M:%S"), "T0": CFG["T0"], "started": CFG["started_et"], "A_guarded": metrics(a, desk), "B_unfiltered": metrics(b, desk), "comparison": compare(a, b, desk)}
    if print_only:
        return out
    repo = DESKS[desk]["repo"]
    ex = os.path.join(repo, "export")
    os.makedirs(ex, exist_ok=True)
    json.dump({**CFG, "desk": desk}, open(os.path.join(ex, "ab_config.json"), "w"), indent=1, sort_keys=True)
    json.dump(out, open(os.path.join(ex, "ab_summary.json"), "w"), indent=1, sort_keys=True)
    with open(os.path.join(ex, "ab_trades.jsonl"), "w") as f:
        for r in sorted(a + b, key=lambda r: (r["arm"], r["id"])):
            f.write(json.dumps(r, sort_keys=True) + "\n")
    env = {**os.environ, "PATH": "/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin")}
    git = ["git", "-C", repo]
    subprocess.run(git + ["add", "export/ab_config.json", "export/ab_summary.json", "export/ab_trades.jsonl"], capture_output=True, env=env)
    r = subprocess.run(git + ["commit", "-qm", f"export: A/B guardrails test ({len(a)} guarded / {len(b)} unfiltered closed trades)", "--author", f"{os.path.basename(repo)} <{os.path.basename(repo)}@localhost>"], capture_output=True, text=True, env=env)
    if r.returncode == 0:
        subprocess.run(git + ["push", "-q"], capture_output=True, text=True, timeout=120, env=env)
    return out


def show(o):
    print(f"\n{o['desk'].upper()}  A (guarded) vs B (unfiltered), since {o['started']}   updated {o['updated']}")
    keys = ["n", "win_rate", "net_usd", "return_pct", "profit_factor", "payoff_ratio", "avg_win_usd", "avg_loss_usd", "expectancy_usd", "max_drawdown_usd", "worst_trade_usd", "trades_per_day"]
    A, B = o["A_guarded"], o["B_unfiltered"]
    print(f"  {'':18s}{'A guarded':>14s}{'B unfiltered':>14s}")
    for k in keys:
        print(f"  {k:18s}{str(A.get(k)):>14s}{str(B.get(k)):>14s}")
    print("  ->", o["comparison"]["verdict"], (o["comparison"].get("interval_90") or ""))


if __name__ == "__main__":
    po = "--print" in sys.argv
    for d in DESKS:
        try:
            show(record(d, print_only=po))
        except Exception as e:
            print(d, "failed:", repr(e)[:200])
