#!/usr/bin/env python3
"""Is a Jev desk ready for live funding? Reads both paper records (read-only) and scores them against fixed gates.
  ~/.venvs/market-ml/bin/python ~/market-lab/ops/jev_readiness.py
The gates were agreed on 2026-10-06 (a few weeks of paper first); change them here, in one place, on purpose."""
import json, os, sqlite3, statistics, time

HOME = os.path.expanduser("~")
GATES = {"days": 21, "trades": 20, "profit_factor": 1.3, "max_drawdown": 0.15}
DESKS = [("Jev desk (memecoins, spot)", f"{HOME}/jev-desk/data/desk.db", "ticker", "ticket_usd"),
         ("Jev Majors (perps)", f"{HOME}/jev-majors/data/desk.db", "coin", "margin_usd"),
         ("Jev Markets (US stocks, ETFs, futures)", f"{HOME}/jev-markets/data/desk.db", "symbol", "risk_usd")]


def load(path, name_col, stake_col):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    kv = lambda k: (lambda r: json.loads(r[0]) if r else None)(db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone())
    epoch, start = kv("paper_epoch") or 0, kv("paper_start_bank") or 1000
    cols = [c[1] for c in db.execute("PRAGMA table_info(orders)")]
    cond = "status='closed'" if "shadow" not in cols else "status='shadow_closed' AND shadow=1"      # jev-markets has no shadow column
    rows = db.execute(f"SELECT created, {name_col}, {stake_col}, pnl_usd, updated FROM orders WHERE {cond} AND created>=? ORDER BY updated", (epoch,)).fetchall()
    return epoch, start, rows


def review(title, path, name_col, stake_col):
    print(f"\n{title}")
    try:
        epoch, start, rows = load(path, name_col, stake_col)
    except Exception as e:
        return print(f"  cannot read the record: {e}")
    days = (time.time() - epoch) / 86400 if epoch else 0
    pnl = [r[3] or 0 for r in rows]
    n = len(pnl)
    gw, gl = sum(p for p in pnl if p > 0), -sum(p for p in pnl if p <= 0)
    pf = gw / gl if gl else (float("inf") if gw else 0)
    run, peak, mdd = start, start, 0.0
    for p in pnl:
        run += p; peak = max(peak, run); mdd = min(mdd, run / peak - 1)
    total = sum(pnl)
    ex_best = total - max(pnl) if pnl else 0
    top3 = sorted(pnl)[-3:] if n else []
    checks = [("running long enough", days >= GATES["days"], f"{days:.1f} of {GATES['days']} days"),
              ("enough closed trades", n >= GATES["trades"], f"{n} of {GATES['trades']}"),
              ("profit after fees and funding", total > 0, f"${total:+.2f} ({total / start:+.1%} of ${start:,.0f})"),
              ("not carried by one trade", n > 1 and ex_best > 0, f"without the best trade: ${ex_best:+.2f}"),
              (f"profit factor >= {GATES['profit_factor']}", pf >= GATES["profit_factor"], "inf" if pf == float("inf") else f"{pf:.2f}"),
              (f"max drawdown <= {GATES['max_drawdown']:.0%}", abs(mdd) <= GATES["max_drawdown"], f"{abs(mdd):.1%}")]
    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'wait'}] {label:<34} {detail}")
    if n:
        wins = [p for p in pnl if p > 0]
        print(f"  win rate {len(wins) / n:.0%}, average trade ${statistics.mean(pnl):+.2f}, three best ${[round(x, 2) for x in top3]}")
    ready = all(ok for _, ok, _ in checks)
    print(f"  VERDICT: {'READY to discuss funding' if ready else 'NOT YET'}  ({sum(ok for _, ok, _ in checks)} of {len(checks)} gates)")


if __name__ == "__main__":
    print(f"Jev readiness review, {time.strftime('%Y-%m-%d %H:%M')}")
    for d in DESKS:
        review(*d)
    print("\nA pass is permission to talk about live funding with a small amount, not a promise. Paper fills are priced from real quotes but"
          "\nno paper test captures every live cost. Start small, keep approval on, and keep the loss limits.")
