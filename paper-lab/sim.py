"""Paper execution: a deterministic risk layer and a bar-by-bar fill simulator.

Nothing here talks to a broker. Timing, per bar i:
  1. the position held through bar i-1 is marked from its close to bar i's open (gaps count)
  2. the target decided at bar i-1's close passes through Risk.clamp
  3. any change fills at bar i's OPEN, worse by slippage, plus fees
  4. the new position is marked from open to close; Risk.on_close sees the equity
"""
import time

import numpy as np


def utc_day(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t))


class Risk:
    """Hard limits. A strategy's target is only ever reduced here, never increased."""

    def __init__(self, r, inst):
        self.r, self.long_only = r, inst.get("long_only", False)
        self.killed, self.reason, self.halt_day = False, None, None
        self.peak = self.day = self.day_start = self.last_eq = None
        self.halts = 0

    def on_close(self, t, eq):
        d = utc_day(t)
        if d != self.day:
            self.day, self.day_start = d, (self.last_eq if self.last_eq is not None else eq)
        self.last_eq = eq
        self.peak = eq if self.peak is None else max(self.peak, eq)
        if not self.killed and eq <= self.peak * (1 - self.r["max_drawdown_pct"] / 100):
            self.killed = True
            self.reason = f"max drawdown {self.r['max_drawdown_pct']}% hit {utc_day(t)}"
        if self.halt_day != d and eq <= self.day_start * (1 - self.r["daily_loss_pct"] / 100):
            self.halt_day, self.halts = d, self.halts + 1

    def clamp(self, target, next_t):
        if self.killed or self.halt_day == utc_day(next_t):
            return 0
        if self.long_only and target < 0:
            return 0
        return int(np.sign(target))


def run(bars, target, inst, cfg, start):
    """Simulate from bar index `start` (>= 1). Returns summary, equity curve and trades."""
    r, c = cfg["risk"], cfg["costs"]
    fut = inst["kind"] == "future"
    mult = inst["multiplier"] if fut else 1.0
    eq = float(r["sleeve_usd"])
    risk = Risk(r, inst)
    pos, costs, trades, curve = 0.0, 0.0, [], []
    for i in range(start, len(bars)):
        b, p = bars[i], bars[i - 1]
        eq += pos * mult * (b["o"] - p["c"])
        want = risk.clamp(target[i - 1], b["t"])
        if fut:
            new = float(want * r["max_contracts"])
        elif want == 0:
            new = 0.0
        elif pos > 0:
            new = pos   # already long: don't churn fees rebalancing to equity drift
        else:
            new = eq * r["crypto_max_notional_pct"] / 100 / b["o"]
        if new != pos:
            qty = new - pos
            if fut:
                slip = abs(qty) * mult * c["future_slippage_ticks"] * inst["tick"]
                fee = abs(qty) * c["future_fee_per_side"]
            else:
                slip = abs(qty) * b["o"] * c["crypto_slippage_pct"] / 100
                fee = abs(qty) * b["o"] * c["crypto_fee_pct"] / 100
            eq -= slip + fee
            costs += slip + fee
            trades.append({"t": b["t"], "qty": round(qty, 6), "px": b["o"], "cost": round(slip + fee, 2)})
            pos = new
        eq += pos * mult * (b["c"] - b["o"])
        risk.on_close(b["t"], eq)
        curve.append((b["t"], eq, pos))
    return summarize(curve, trades, costs, risk, r["sleeve_usd"], fut)


def summarize(curve, trades, costs, risk, sleeve, fut):
    if not curve:
        return {"bars": 0}
    eqs = np.array([e for _, e, _ in curve])
    peak = np.maximum.accumulate(np.concatenate([[sleeve], eqs]))[1:]
    daily = {}
    for t, e, _ in curve:
        daily[utc_day(t)] = e
    d = np.array([sleeve] + list(daily.values()))
    rets = np.diff(d) / d[:-1]
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252 if fut else 365)) if len(rets) > 2 and rets.std() > 0 else None
    mid = len(eqs) // 2
    return {
        "bars": len(curve), "from": curve[0][0], "to": curve[-1][0],
        "net": round(eqs[-1] - sleeve, 2), "ret_pct": round((eqs[-1] / sleeve - 1) * 100, 2),
        "first_half": round(eqs[mid] - sleeve, 2), "second_half": round(eqs[-1] - eqs[mid], 2),
        "max_dd_pct": round(float(((peak - eqs) / peak).max()) * 100, 2),
        "trades": len(trades), "costs": round(costs, 2),
        "exposure_pct": round(100 * np.mean([p != 0 for _, _, p in curve]), 1),
        "sharpe": None if sharpe is None else round(sharpe, 2), "days": len(daily),
        "killed": risk.reason, "daily_halts": risk.halts,
        "position": curve[-1][2], "equity": round(eqs[-1], 2),
    }
