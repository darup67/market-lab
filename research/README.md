# Research notes

## Live Chronos / AutoGluon reading (2026-10-04)
`live_reading.py` prints the market price, the cushion model, Chronos-2, Chronos-Bolt and AutoGluon for the CURRENT Kalshi BTC 15-minute
and 1-hour windows (P(finish above the strike) from each). It is a reading, not a recommendation: in every test the market price matched or
beat every model at every minute, and no model had positive expected value after fees. The trained AutoGluon predictors (1.5 GB) stay local in
`research/models/` (git-ignored); the script header says how to rebuild them. Without them only the AutoGluon rows are blank.

## Volatility vs. prediction accuracy at minutes 1, 3, 4, 5: BTC, gold, WTI (2026-10-03)
`vol_accuracy.py` (report `results/vol-accuracy-2026-10-03.md` + chart `.png`/`.json`) asks whether higher or lower volatility
makes the up/down call more often correct on Kalshi's 15-minute markets (BTC 2.6k windows; gold and WTI ~460 windows each, one week).
Volatility = std of 1-, 3-, 4- and 5-minute candle returns over the trailing 60 minutes (bps per sqrt-minute) plus volatility inside the
window so far; predictions = the Kalshi favourite at the mark, the naive spot-vs-strike call and the cushion model. CIs resample whole days.

**Result: no usable volatility edge.** Trailing volatility at any candle scale barely moves accuracy: BTC and gold slopes are indistinguishable
from zero; WTI leans slightly positive (higher vol, a bit more accurate; +0.1 log-odds per SD, significant at minute 1 and pooled, but
1 of 12 cells and a one-week sample). All three assets pooled: +0.034 [-0.03, +0.10]. Volatility inside the window looks strongly positive
but disappears once the cushion is controlled for (it just means price already moved). The market also prices it in: buying the favourite
at the ask earns about -1.5c calm, -0.6c middle, -1.7c volatile, and BTC's EV falls slightly as volatility rises (rho about -0.05 to -0.07).
Companion: `ml_test_minute.py` (Chronos/AutoGluon vs the market at minutes 1, 3, 4, 5): the market price won at every minute.

## Kalshi 15-minute crypto: mispricing vs. spot + volatility (2026-09-28)
`kalshi_mispricing.py` covers 8,817 recorded windows across BTC, ETH, SOL and XRP (24 days). Fair value is Φ(ln(spot/strike) / (σ₁ₘ·√minutes_left)), with σ from the prior 60 one-minute Binance returns.

**Result: no edge.** Buying whatever fair value says is underpriced (by 2–15¢ after fees) loses **−2.2¢ to −2.5¢ per contract**, in both halves of history and on BTC, ETH and SOL. XRP is inconsistent and has few trades.

**Favorites cross-check** (the kalshi-btc-agent style of trade: buy the side priced 65–95¢ when fair value agrees): **−0.3¢ to −2.8¢ per contract**, in both halves. This matches the agent's original backtest (−1.3¢), not its 6-day live streak (+16¢ over 464 calls). The live streak is most likely one market stretch, which is why trade-core's ledger now requires evidence spread over ≥ 10 separate days.

Pitfalls found on the way; keep them in mind for any future Kalshi study:
1. Time parsing: `time.mktime(...) - time.timezone` is off by the DST hour. Use `calendar.timegm`.
2. `minute_quotes[i]` is the close of Kalshi's 1-minute candle **ending** at open + i min (record.js `minuteQuotes`). The spot price known at that moment is the close of the Binance bar that opened one minute *earlier*. Using the bar opening at that moment looks one minute ahead and fakes a +8–15¢ "edge".

## Daily crypto rules vs costs, and the LuxAlgo Edge library (2026-09-29)
Asked to copy the best-rated algo strategy subscription. Findings:
- Web search returned review roundups; the "best rated" names (TrendSpider, Trade Ideas, TradingView) are platforms, and no subscription publishes an independently verified strategy track record that could be copied. Paid proprietary logic isn't copied.
- The user's connected LuxAlgo account exposes an honest **Edge** stats library (hosted only for BTCUSDT and ETHUSDT, 2,463 daily sessions since 2020). On BTC nothing separated from baseline (green days 50.9%): after 3 red days 55.7% [49.6-61.7]; after an outside day 54.3% [48.4-60.1]; no weekday differed.
- `crypto_daily_rules.py` (30 coins, ~3y, 0.5% round-trip): a random day nets -0.48%. 20-day breakout +0.11% (older +0.12 / newer +0.09), 4 red days +0.11% (older -0.05 / newer +0.26), other rules negative. Coins are highly correlated, so the effective sample is much smaller than the trade count.
- Implemented as SILENT ledger-graded signals in crypto-scan.js (`strat:breakout20`, `strat:reds4`); they earn real-time email only by passing the evidence gate (~10/day signals expected, so about 2 weeks).
