# paper-lab

Paper trading for **BTC, ETH, MNQ, MES, MGC and MCL**. It answers one question:
do any simple, pre-declared rules make money **after costs**, on data they have
never seen?

**It cannot trade.** There is no broker connection anywhere in this repo. It
never imports the Robinhood connector, and every fill is simulated. If a rule
ever earns trust here, you place any real trade yourself, one confirmed order at a time.

Lives in the **market-lab** repo (`~/market-lab/paper-lab`, remote `darup67/market-lab`).
Every hour the `step` run commits and pushes `paper-lab/data/` itself, the same
way the recorders commit `data/`. It stages only that folder.
`PAPERLAB_AUTOCOMMIT=0` turns this off.

Built 2026-09-24 as the safe version of the "24/7 autonomous agent with AgenKit
+ Jev" prompt. It keeps that prompt's good engineering: a causal state engine,
risk limits in code, and a Brier calibration check. It drops auto-execution,
AgenKit, Kelly sizing on model confidence, and the nightly schema rewrites.

```
Coinbase 15m (BTC, ETH) ─┐
Yahoo 15m (MNQ/MES/      ├─► data/bars/*.jsonl ─► strategies ─► Risk.clamp ─► sim fills ─► results/
  MGC/MCL=F, ~10m delay) ┘    (closed bars only)   (fixed rules)  (hard limits)  (next open, costs)
RSS headlines ─► data/news.jsonl ─► Jev (shadow, when a key exists) ─► 4h hit rate
```

## Commands

```bash
cd ~/market-lab/paper-lab; PY=~/.venvs/market-ml/bin/python
$PY lab.py report      # everything: paper, backtest, calibration, gaps, Jev news
$PY lab.py paper       # live out-of-sample results only
$PY lab.py backtest    # all stored history
$PY lab.py calib       # is the next hour predictable at all?
$PY lab.py step        # fetch + paper (launchd does this every 15 min)
```

| launchd label | schedule | does |
|---|---|---|
| `com.dhruv.paperlab` | :02 :17 :32 :47 every hour (calendar, not StartInterval) | `step` |
| `com.dhruv.paperlab.report` | Sundays 18:00 | `report --email` → darup67@gmail.com |

Calendar triggers are deliberate: StartInterval jobs stopped firing on this
Mac in Sep 2026. The plists set PATH to include `~/.local/bin`, where `node`
lives, so the email step works under launchd.

## How results are produced

- **Timing.** A rule decides at a bar's close and fills at the next bar's open,
  worse by slippage and plus fees. Tests confirm that changing future prices
  never changes a past decision, and that buy-and-hold reconciles to the cent.
- **Costs (estimates, in `config.json`).** Crypto: 0.30%/side for Robinhood's
  spread, plus 0.02% slippage. Futures: $0.85/side/contract plus 1 tick.
- **Crypto is long-only**, as on Robinhood. A short signal means flat.
- **Risk, per $10k sleeve.** Max 1 contract. Crypto notional ≤ 100% of equity.
  A 3% daily loss flattens for the rest of the UTC day. A 15% drawdown kills the
  book for good. Rules can only be reduced by the risk layer, never enlarged.
- **Strategies** (textbook parameters, never fitted): `trend` (EMA 32/128),
  `breakout` (Donchian 96/48), `reversion` (z-score 20, in at 2.5, out at 0.5
  or 16 bars). Baselines: `flat` and `hold`.
- **Paper** = the same rules over bars that arrived after `paper_start`. It is
  recomputed from the stored bars on each run, so there is no position state to
  corrupt. Changing any rule means bumping `rules_version` **and** `paper_start`.

**PASSES** means: net > 0, beats `hold`, and positive in both halves. Then
it has to keep that up in paper. With 18 rule×instrument combinations, about
one pass is expected from luck alone.

## Known limits

- **Futures data is ~10 min delayed**, so paper fills at the next open are
  optimistic. The 1-tick slippage covers part of this, not all of it.
- **`=F` series are unadjusted front months.** Roll dates show up as gaps. The
  report lists every gap > 4 ATR; most are Sunday reopens, which are real weekend risk.
- **A $10k sleeve with 1 MNQ** is about 6× leverage, so even `hold` hits
  the 15% kill on ordinary swings. That's realistic, but it means a killed
  baseline can make a strategy look better than it is.
- **60 days of history** is one market regime. Only paper accumulates new evidence.

## First backtest (2026-09-24, 58–60 days)

- Calibration: **negative Brier skill on all six**. A walk-forward logistic model
  does worse than the base rate at predicting the next hour. There's no edge there.
- Only **MCL trend** and **MCL breakout** pass. Oil trended over the window, and
  $850 of MCL trend's $3,693 came from gap opens. Treat both as watchlist items,
  not findings.
- Every crypto rule loses to buy-and-hold after the 0.30%/side spread.

## Jev

`news.py` stores headlines from CoinDesk, Cointelegraph, CNBC and MarketWatch.
With a key (`~/jev-client`), Jev tags each one: which asset it's about, whether
it's good or bad news for that asset, and whether it's market-moving. The report
scores the direction calls against the actual 4-hour move. It runs in shadow mode:
nothing trades on it. The backlog is judged once a key exists.
