"""Perp simulator: decide at bar close, fill at NEXT open, fee+slippage per side, hourly funding, liquidation, equity-compounded."""
import numpy as np

def run(cs, fund, pos, cfg, lo=0, hi=None):
    hi = hi or len(cs); lev = min(cfg["leverage"], cfg["risk"]["max_leverage"])
    fee = cfg["fee_per_side"] + cfg["slippage_per_side"]; mm = cfg["maintenance_margin"]
    eq, cur, entry, peak, trades, wins, liq = 1.0, 0.0, None, 1.0, 0, 0, 0
    curve, trade_start_eq, halted_until, day_start, day_eq = [], 1.0, -1, 0, 1.0
    for i in range(lo + 1, hi):
        o, h, l, c = cs[i]["o"], cs[i]["h"], cs[i]["l"], cs[i]["c"]
        want = float(pos[i - 1])                         # signal from previous close
        if i <= halted_until: want = 0.0
        if (i - lo) // 24 != day_start:
            day_start, day_eq = (i - lo) // 24, eq
        if want != cur:                                  # trade at this bar's open
            if cur != 0:
                eq -= abs(cur) * lev * eq * fee; trades += 1; wins += eq > trade_start_eq
            if want != 0:
                eq -= abs(want) * lev * eq * fee; entry = o; trade_start_eq = eq
            cur = want
        if cur != 0:
            r_hi, r_lo = h / entry - 1, l / entry - 1    # adverse excursion vs entry
            worst = (r_lo if cur > 0 else -r_hi) * lev
            bar = cur * (c / o - 1) * lev                # hourly pnl from open to close
            if worst <= -(1 - mm):                       # liquidation
                eq = 1e-9; liq += 1; cur = 0.0; trades += 1      # margin wiped out
                curve.append(eq); peak = max(peak, eq); halted_until = hi; continue
            eq *= 1 + bar
            eq -= cur * lev * eq * fund.get(cs[i]["t"], 0.0)   # long pays when funding>0
            entry = c if cur != 0 else entry
        peak = max(peak, eq); curve.append(eq)
        if eq / day_eq - 1 < -cfg["risk"]["daily_loss_stop"]: halted_until = i + 24
        if eq / peak - 1 < -cfg["risk"]["max_drawdown_stop"]: halted_until = hi
    cv = np.array(curve) if curve else np.array([1.0])
    rets = np.diff(np.log(np.maximum(cv, 1e-9)), prepend=0)
    dd = (cv / np.maximum.accumulate(cv) - 1).min()
    sharpe = rets.mean() / rets.std() * np.sqrt(24 * 365) if rets.std() > 0 else 0.0
    return {"net_return": cv[-1] - 1, "max_dd": dd, "sharpe": sharpe, "trades": trades, "win_rate": wins / trades if trades else 0.0, "liquidations": liq}
