# perp-lab

**Simulation only.** Tests pre-declared perpetual-futures rules on BTC, ETH, SOL, HYPE and ADA, net of fees, slippage and hourly funding, with leverage and liquidation modeled. There is no broker import and no order path anywhere in this folder. If a rule ever earns trust here, you place any real trade yourself, one confirmed order at a time.

Public Hyperliquid candles and funding stand in for Robinhood's perp market, whose contract list and fee schedule are not visible to this setup. Fees and slippage in `config.json` are assumptions; update them from the real schedule.

## Rules (fixed in `strategies.py`)
`buy_hold` (baseline), `ema_trend` (24/96 EMA), `donchian48` (48-bar breakout), `rsi_revert` (RSI 25/75), `funding_fade` (trade against crowded funding).

## Method
Signal at bar close, fill at the next open. First 60% of history is in-sample, last 40% is out-of-sample (never used to pick anything). Risk limits in code: leverage capped at 3x, 5% daily loss stop, 25% drawdown stop.

## Run
```
~/.venvs/market-ml/bin/python lab.py            # backtest report -> results/
~/.venvs/market-ml/bin/python lab.py --signals  # current paper stance per coin
```
`com.dhruv.perplab.plist` runs both daily at 08:30 and commits `results/`.

## Result so far (2026-10-05, ~208 days of 1h bars)
0 of 20 coin/rule combinations were profitable out-of-sample after costs and funding. Buy-and-hold on BTC/ETH/SOL looked good only because the out-of-sample window was a rally. Treat this as the bar to beat, not a trading recommendation.
