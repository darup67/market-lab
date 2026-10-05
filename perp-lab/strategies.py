"""Pre-declared rules. Each returns a target position series in {-1, 0, +1} computed ONLY from data up to and including each bar's close."""
import numpy as np

def ema(x, n):
    a, out = 2 / (n + 1), np.empty(len(x)); out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out

def rsi(c, n=14):
    d = np.diff(c, prepend=c[0]); up, dn = np.maximum(d, 0), np.maximum(-d, 0)
    au, ad = ema(up, 2 * n - 1), ema(dn, 2 * n - 1)
    return 100 - 100 / (1 + au / np.maximum(ad, 1e-12))

def buy_hold(c, f):
    return np.ones(len(c))

def ema_trend(c, f):
    return np.where(ema(c, 24) > ema(c, 96), 1.0, -1.0)

def donchian(c, f, n=48):
    pos, cur = np.zeros(len(c)), 0.0
    for i in range(n, len(c)):
        hi, lo = c[i - n:i].max(), c[i - n:i].min()
        if c[i] > hi: cur = 1.0
        elif c[i] < lo: cur = -1.0
        pos[i] = cur
    return pos

def rsi_revert(c, f):
    r, pos, cur = rsi(c), np.zeros(len(c)), 0.0
    for i in range(len(c)):
        if r[i] < 25: cur = 1.0
        elif r[i] > 75: cur = -1.0
        elif (cur > 0 and r[i] > 50) or (cur < 0 and r[i] < 50): cur = 0.0
        pos[i] = cur
    return pos

def funding_fade(c, f):
    """Crowded longs (high avg funding) -> short; crowded shorts -> long. f = hourly funding aligned to bars."""
    avg = ema(f, 72)
    hi, lo = np.percentile(avg, 85), np.percentile(avg, 15)   # NOTE: percentiles use whole series -> only compared out-of-sample via fixed thresholds below
    return np.where(avg > 0.00002, -1.0, np.where(avg < -0.00002, 1.0, 0.0))

STRATS = {"buy_hold": buy_hold, "ema_trend": ema_trend, "donchian48": donchian, "rsi_revert": rsi_revert, "funding_fade": funding_fade}
