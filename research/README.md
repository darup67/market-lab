# Research notes

## Kalshi 15-minute crypto: mispricing vs. spot + volatility (2026-09-28)
`kalshi_mispricing.py` covers 8,817 recorded windows across BTC, ETH, SOL and XRP (24 days). Fair value is Φ(ln(spot/strike) / (σ₁ₘ·√minutes_left)), with σ from the prior 60 one-minute Binance returns.

**Result: no edge.** Buying whatever fair value says is underpriced (by 2–15¢ after fees) loses **−2.2¢ to −2.5¢ per contract**, in both halves of history and on BTC, ETH and SOL. XRP is inconsistent and has few trades.

**Favorites cross-check** (the kalshi-btc-agent style of trade: buy the side priced 65–95¢ when fair value agrees): **−0.3¢ to −2.8¢ per contract**, in both halves. This matches the agent's original backtest (−1.3¢), not its 6-day live streak (+16¢ over 464 calls). The live streak is most likely one market stretch, which is why trade-core's ledger now requires evidence spread over ≥ 10 separate days.

Pitfalls found on the way; keep them in mind for any future Kalshi study:
1. Time parsing: `time.mktime(...) - time.timezone` is off by the DST hour. Use `calendar.timegm`.
2. `minute_quotes[i]` is the close of Kalshi's 1-minute candle **ending** at open + i min (record.js `minuteQuotes`). The spot price known at that moment is the close of the Binance bar that opened one minute *earlier*. Using the bar opening at that moment looks one minute ahead and fakes a +8–15¢ "edge".
