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

## Setups (the usable part): `scan.py`
Prints, per coin, whether a daily long/short trend-breakout setup is live, with **entry, stop, target, size and max hold**, or the exact trigger levels to watch if not. Context lines show the existing detector's 15m SuperTrend regime and latest flip/rally/gap. Output also in `results/setups.json` and `results/setups.txt`.

- `TREND-55`: close above/below the 55-day high/low, with price on the right side of the 100-day EMA. Stop 3 ATR(14d), target 5R, hold up to 40 days.
- `TREND-20`: same on 20 days / 50-day EMA. Stop 2.5 ATR, target 3R.
- Sizing: 1% of equity at risk per trade, leverage capped at 3x.

## Research (2023-10 to 2026-10, hourly/15m, costs 0.09%/side + real funding)
| test | verdict |
|---|---|
| 1h pullback/breakout, 24 variants (`research.py`) | none positive out-of-sample |
| Existing detector (flip/rally/gap) mirrored long+short, 15m (`research3.py`) | no better than random entries; costs dominate intraday |
| Daily trend breakouts, long+short (`research2.py`) | positive OOS on BTC, ETH, SOL, ADA; 24 and 52 OOS trades, small sample |
| Funding carry | 9-15%/yr average on longs paying, but 7-day funding swings to -45%/yr on SOL/ADA |

Caveats: fee/slippage are assumptions; the OOS sample is a single regime; HYPE has about 200 days of history and is not validated. Nothing here places orders.
