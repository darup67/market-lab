"""Fixed rules. Each returns the target position (-1, 0, +1) decided at each bar's
CLOSE, using only that bar and earlier ones. The simulator fills it at the
NEXT bar's open, so no rule can see the price it trades at.

Parameters come from config.json and were chosen before looking at any
results. They are textbook defaults, not fitted values.
"""
import numpy as np
import pandas as pd


def flat(df, p):
    return np.zeros(len(df))


def hold(df, p):
    return np.ones(len(df))


def trend(df, p):
    """Long above, short below: fast EMA vs slow EMA of closes (32 vs 128 bars = 8h vs 32h)."""
    c = df["c"]
    fast = c.ewm(span=p["fast"], adjust=False).mean()
    slow = c.ewm(span=p["slow"], adjust=False).mean()
    out = np.sign(fast - slow).to_numpy()
    out[: p["slow"]] = 0
    return out


def breakout(df, p):
    """Donchian / turtle: enter on a close beyond the prior `entry`-bar high or low,
    exit on a close beyond the prior `exit`-bar channel the other way."""
    hi_in = df["h"].rolling(p["entry"]).max().shift(1).to_numpy()
    lo_in = df["l"].rolling(p["entry"]).min().shift(1).to_numpy()
    hi_out = df["h"].rolling(p["exit"]).max().shift(1).to_numpy()
    lo_out = df["l"].rolling(p["exit"]).min().shift(1).to_numpy()
    c = df["c"].to_numpy()
    out, pos = np.zeros(len(df)), 0
    for i in range(len(df)):
        if np.isnan(hi_in[i]):
            continue
        if pos == 1 and c[i] < lo_out[i]:
            pos = 0
        elif pos == -1 and c[i] > hi_out[i]:
            pos = 0
        if c[i] > hi_in[i]:
            pos = 1
        elif c[i] < lo_in[i]:
            pos = -1
        out[i] = pos
    return out


def reversion(df, p):
    """Fade a close more than `enter_z` standard deviations from its `window`-bar mean;
    exit back inside `exit_z`, or after `max_bars` bars regardless."""
    c = df["c"]
    z = ((c - c.rolling(p["window"]).mean()) / c.rolling(p["window"]).std()).to_numpy()
    out, pos, held = np.zeros(len(df)), 0, 0
    for i in range(len(df)):
        if np.isnan(z[i]):
            continue
        if pos:
            held += 1
            if abs(z[i]) < p["exit_z"] or held >= p["max_bars"]:
                pos = 0
        if not pos and abs(z[i]) > p["enter_z"]:
            pos, held = (-1 if z[i] > 0 else 1), 0
        out[i] = pos
    return out


ALL = {"flat": flat, "hold": hold, "trend": trend, "breakout": breakout, "reversion": reversion}


def targets(name, df, params):
    return ALL[name](df, params)
