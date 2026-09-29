#!/usr/bin/env python3
"""Do classic daily crypto rules survive costs? (2026-09-29)

30 Binance USDT coins x ~800-1000 daily bars. Buy at the signal day's close, sell at the next close,
net of a 0.5% round trip (the ledger's 25 bps/side crypto cost). Reported per rule, with the older/newer
half so one lucky stretch can't pass. Coins move together, so the effective sample is far smaller than
the trade count. Result on 2026-09-29: a random day loses 0.48% net; only the 20-day breakout
(+0.11%, both halves) and 4-red-days (+0.11%, older half negative) come out positive.
"""
import json, statistics as st, urllib.request
COINS = "BTC ETH BNB XRP ADA DOGE SOL LINK LTC DOT AVAX ATOM UNI NEAR ARB OP INJ FIL AAVE ALGO XLM BCH ETC SUI HBAR APT TRX SHIB PEPE RENDER".split()
COST = 0.005
def get(u): return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=20))
data = {}
for c in COINS:
    try: data[c] = [(x[0], float(x[1]), float(x[4])) for x in get(f"https://data-api.binance.vision/api/v3/klines?symbol={c}USDT&interval=1d&limit=1000")]
    except Exception: pass
red = lambda b, i: b[i][2] < b[i][1]
RULES = {
  "random day (baseline)": lambda b, i: True,
  "3 red days": lambda b, i: red(b, i) and red(b, i - 1) and red(b, i - 2),
  "4 red days": lambda b, i: all(red(b, i - k) for k in range(4)),
  "5-day closing low": lambda b, i: b[i][2] < min(x[2] for x in b[i - 5:i]),
  "close > 50-day avg": lambda b, i: b[i][2] > sum(x[2] for x in b[i - 50:i]) / 50,
  "20-day breakout": lambda b, i: b[i][2] > max(x[2] for x in b[i - 20:i]),
}
print(f"{len(data)} coins")
for name, rule in RULES.items():
    tr = sorted((b[i][0], b[i + 1][2] / b[i][2] - 1) for b in data.values() for i in range(60, len(b) - 1) if rule(b, i))
    g = [x[1] for x in tr]; mid = len(g) // 2
    print(f"{name:24} n {len(g):6}  gross {100 * st.mean(g):+.2f}%  net {100 * (st.mean(g) - COST):+.2f}%  halves {100 * (st.mean(g[:mid]) - COST):+.2f}% / {100 * (st.mean(g[mid:]) - COST):+.2f}%")
