# Chronos + AutoGluon on CME futures — 2026-09-18

15-minute bars for NQ, ES, YM, GC and CL, Sep 1–18 (~1,127 real bars each).
Fit on the first half chronologically (Sep 1–10), test on the second (Sep 10–18,
~556 bars). Each question is asked at a bar's close about the next bar. Bars
that precede a halt are not scored.

## Verdict

**Direction — nothing.** No model beat the best constant guess on any symbol.
Every Brier-vs-constant CI touches or exceeds zero.

**Volatility — Chronos-2 is the one real result, with a caveat.** It beat the
strongest simple baseline on all 5 symbols, and every 95% CI excludes zero:

- ΔMAE vs time-of-day + persistence: NQ −0.051, ES −0.056, YM −0.062, GC −0.040, CL −0.031
- Skill vs time-of-day alone: +26% to +41%

The simple baseline is the time-of-day average plus an EWMA of the deviation
from it. It sees exactly the same past as the models.

Other volatility models:
- Chronos-Bolt and AutoGluon-Tabular tied that baseline.
- AutoGluon-TS was worse, and on YM it collapsed (−86% skill).

**The caveat:** Monday Sep 14, the AI-slowdown selloff, carries much of the edge.
Day-by-day checks (tod+ewma at alpha 0.2 for all symbols):

| | days Chronos-2 better | ΔMAE | ΔMAE without Sep 14 |
|---|---|---|---|
| NQ | 7/8 | −0.051 | −0.021 |
| ES | 8/8 | −0.056 | −0.027 |
| YM | 7/8 | −0.062 | −0.029 |
| GC | 5/8 | −0.045 | −0.009 |
| CL | 5/8 | −0.031 | −0.002 |

So on equity indices the edge is consistent but modest outside the shock day.
On gold and crude it is mostly that one day. NQ, ES and YM are one market
(Nasdaq/S&P/Dow beta), so this is ~3 independent tests, not 5.

## Why this is not a trading signal

- These forecasts predict how big the next bar will be, not which way it will go.
  That helps with position sizing or where to put a stop. It does not say
  whether to buy.
- Yahoo data is ~10 min delayed.
- The test covers only 8 days.

## Method notes

- Snapshot rows removed. Yahoo's live-quote rows (272 per symbol, unaligned
  timestamps, o=h=l=c, v=0) were being stored as bars. They are dropped on
  load, and record-futures.js stops storing them from 2026-09-18.
- Real 15-min grid. Bars sit on the grid with NaN for halts and weekends, so
  daily seasonality keeps its 96-bar period.
- Momentum as a probability. P(up | last bar up/down) is measured in the fit half.
- AutoGluon gaps. AutoGluon-Tabular has no neural-net model (fastai not
  installed). AutoGluon-TS used its default medium_quality ensemble.

Re-run: `~/.venvs/market-ml/bin/python ml_futures.py`
Raw numbers: `ml-futures-2026-09-18.json`


## NQ_F  (1126 bars; test 553 direction / 556 volatility)

Direction — up-rate in test 52.3% (best constant 52.3%)

| model | acc | 95% CI | Brier | vs constant (acc) | ΔBrier vs constant [95% CI] |
|---|---|---|---|---|---|
| constant | 52.3% | 48–56% | 0.2499 | — | — |
| momentum | 53.2% | 49–57% | 0.2493 | +137/−132, p=0.807 | -0.0006 [-0.0016, +0.0004] |
| chronos-bolt | 46.1% | 42–50% | 0.2790 | +106/−140, p=0.035 | +0.0292 [+0.0173, +0.0409] |
| chronos-2 | 47.9% | 44–52% | 0.2833 | +110/−134, p=0.141 | +0.0335 [+0.0206, +0.0462] |
| ag-ts | 51.5% | 47–56% | 0.2806 | +153/−157, p=0.865 | +0.0307 [+0.0151, +0.0468] |
| ag-tab | 50.6% | 46–55% | 0.2561 | +155/−164, p=0.654 | +0.0062 [-0.0011, +0.0133] |

Volatility — next bar's log range; skill = 1 − MSE/MSE(time-of-day)

| model | MAE | RMSE | skill vs time-of-day | Spearman ρ | ΔMAE vs time-of-day [95% CI] | ΔMAE vs tod+ewma [95% CI] |
|---|---|---|---|---|---|---|
| time-of-day | 0.4450 | 0.5967 | +0.0% | 0.487 | — | — |
| last bar | 0.5622 | 0.7269 | -48.4% | 0.376 | +0.1172 [+0.0762, +0.1600] | +0.1585 [+0.1260, +0.1913] |
| ewma | 0.4556 | 0.5815 | +5.0% | 0.444 | +0.0106 [-0.0228, +0.0434] | +0.0519 [+0.0274, +0.0765] |
| tod+ewma | 0.4037 | 0.5283 | +21.6% | 0.604 | -0.0413 [-0.0665, -0.0158] | — |
| chronos-bolt | 0.4003 | 0.5217 | +23.5% | 0.580 | -0.0447 [-0.0706, -0.0190] | -0.0033 [-0.0188, +0.0122] |
| chronos-2 | 0.3531 | 0.4591 | +40.8% | 0.672 | -0.0919 [-0.1203, -0.0630] | -0.0506 [-0.0739, -0.0281] |
| ag-ts | 0.4338 | 0.5578 | +12.6% | 0.552 | -0.0112 [-0.0421, +0.0196] | +0.0301 [+0.0113, +0.0488] |
| ag-tab | 0.3992 | 0.5169 | +25.0% | 0.614 | -0.0458 [-0.0670, -0.0251] | -0.0045 [-0.0287, +0.0194] |

dir ag-ts best: WeightedEnsemble · tod+ewma alpha: 0.2 · vol ag-ts best: WeightedEnsemble

## ES_F  (1126 bars; test 538 direction / 556 volatility)

Direction — up-rate in test 51.3% (best constant 51.3%)

| model | acc | 95% CI | Brier | vs constant (acc) | ΔBrier vs constant [95% CI] |
|---|---|---|---|---|---|
| constant | 48.7% | 44–53% | 0.2508 | — | — |
| momentum | 48.7% | 44–53% | 0.2513 | +0/−0, p=1.000 | +0.0006 [-0.0006, +0.0018] |
| chronos-bolt | 48.7% | 44–53% | 0.2861 | +159/−159, p=1.000 | +0.0353 [+0.0206, +0.0503] |
| chronos-2 | 45.2% | 41–49% | 0.2862 | +131/−150, p=0.283 | +0.0355 [+0.0217, +0.0486] |
| ag-ts | 53.0% | 49–57% | 0.2881 | +141/−118, p=0.172 | +0.0373 [+0.0186, +0.0570] |
| ag-tab | 51.3% | 47–56% | 0.2565 | +123/−109, p=0.393 | +0.0057 [-0.0009, +0.0123] |

Volatility — next bar's log range; skill = 1 − MSE/MSE(time-of-day)

| model | MAE | RMSE | skill vs time-of-day | Spearman ρ | ΔMAE vs time-of-day [95% CI] | ΔMAE vs tod+ewma [95% CI] |
|---|---|---|---|---|---|---|
| time-of-day | 0.4640 | 0.6154 | +0.0% | 0.562 | — | — |
| last bar | 0.5823 | 0.7646 | -54.3% | 0.443 | +0.1183 [+0.0750, +0.1626] | +0.1575 [+0.1244, +0.1911] |
| ewma | 0.4705 | 0.6126 | +0.9% | 0.526 | +0.0066 [-0.0271, +0.0391] | +0.0457 [+0.0195, +0.0716] |
| tod+ewma | 0.4248 | 0.5562 | +18.3% | 0.660 | -0.0392 [-0.0647, -0.0137] | — |
| chronos-bolt | 0.4097 | 0.5434 | +22.0% | 0.654 | -0.0542 [-0.0797, -0.0281] | -0.0151 [-0.0313, +0.0008] |
| chronos-2 | 0.3689 | 0.4901 | +36.6% | 0.721 | -0.0951 [-0.1231, -0.0669] | -0.0559 [-0.0782, -0.0336] |
| ag-ts | 0.4430 | 0.5724 | +13.5% | 0.622 | -0.0210 [-0.0530, +0.0107] | +0.0182 [-0.0014, +0.0376] |
| ag-tab | 0.4154 | 0.5524 | +19.4% | 0.654 | -0.0485 [-0.0689, -0.0280] | -0.0094 [-0.0316, +0.0132] |

dir ag-ts best: WeightedEnsemble · tod+ewma alpha: 0.2 · vol ag-ts best: WeightedEnsemble

## YM_F  (1129 bars; test 542 direction / 558 volatility)

Direction — up-rate in test 51.5% (best constant 51.5%)

| model | acc | 95% CI | Brier | vs constant (acc) | ΔBrier vs constant [95% CI] |
|---|---|---|---|---|---|
| constant | 48.5% | 44–53% | 0.2507 | — | — |
| momentum | 49.4% | 45–54% | 0.2514 | +138/−133, p=0.808 | +0.0007 [-0.0013, +0.0027] |
| chronos-bolt | 46.3% | 42–51% | 0.3054 | +95/−107, p=0.439 | +0.0547 [+0.0378, +0.0715] |
| chronos-2 | 52.2% | 48–56% | 0.2739 | +163/−143, p=0.277 | +0.0232 [+0.0086, +0.0369] |
| ag-ts | 53.5% | 49–58% | 0.3199 | +136/−109, p=0.096 | +0.0692 [+0.0445, +0.0946] |
| ag-tab | 52.2% | 48–56% | 0.2575 | +105/−85, p=0.168 | +0.0068 [-0.0023, +0.0158] |

Volatility — next bar's log range; skill = 1 − MSE/MSE(time-of-day)

| model | MAE | RMSE | skill vs time-of-day | Spearman ρ | ΔMAE vs time-of-day [95% CI] | ΔMAE vs tod+ewma [95% CI] |
|---|---|---|---|---|---|---|
| time-of-day | 0.4999 | 0.6509 | +0.0% | 0.646 | — | — |
| last bar | 0.6554 | 0.8493 | -70.3% | 0.489 | +0.1555 [+0.1107, +0.2001] | +0.2017 [+0.1654, +0.2383] |
| ewma | 0.5337 | 0.6858 | -11.0% | 0.564 | +0.0338 [-0.0044, +0.0712] | +0.0800 [+0.0478, +0.1127] |
| tod+ewma | 0.4537 | 0.5863 | +18.9% | 0.706 | -0.0462 [-0.0732, -0.0194] | — |
| chronos-bolt | 0.4539 | 0.5855 | +19.1% | 0.715 | -0.0460 [-0.0752, -0.0162] | +0.0002 [-0.0182, +0.0187] |
| chronos-2 | 0.3916 | 0.5051 | +39.8% | 0.792 | -0.1083 [-0.1382, -0.0773] | -0.0621 [-0.0858, -0.0386] |
| ag-ts | 0.6970 | 0.8867 | -85.6% | 0.421 | +0.1971 [+0.1522, +0.2413] | +0.2433 [+0.1969, +0.2903] |
| ag-tab | 0.4383 | 0.5712 | +23.0% | 0.744 | -0.0616 [-0.0848, -0.0385] | -0.0154 [-0.0386, +0.0071] |

dir ag-ts best: WeightedEnsemble · tod+ewma alpha: 0.2 · vol ag-ts best: WeightedEnsemble

## GC_F  (1129 bars; test 553 direction / 558 volatility)

Direction — up-rate in test 48.3% (best constant 51.7%)

| model | acc | 95% CI | Brier | vs constant (acc) | ΔBrier vs constant [95% CI] |
|---|---|---|---|---|---|
| constant | 51.7% | 48–56% | 0.2500 | — | — |
| momentum | 50.8% | 47–55% | 0.2509 | +127/−132, p=0.804 | +0.0009 [-0.0023, +0.0040] |
| chronos-bolt | 53.2% | 49–57% | 0.2673 | +86/−78, p=0.585 | +0.0173 [+0.0063, +0.0281] |
| chronos-2 | 47.4% | 43–52% | 0.2759 | +135/−159, p=0.180 | +0.0259 [+0.0135, +0.0376] |
| ag-ts | 52.3% | 48–56% | 0.2765 | +169/−166, p=0.913 | +0.0264 [+0.0100, +0.0430] |
| ag-tab | 53.3% | 49–57% | 0.2491 | +57/−48, p=0.435 | -0.0009 [-0.0049, +0.0031] |

Volatility — next bar's log range; skill = 1 − MSE/MSE(time-of-day)

| model | MAE | RMSE | skill vs time-of-day | Spearman ρ | ΔMAE vs time-of-day [95% CI] | ΔMAE vs tod+ewma [95% CI] |
|---|---|---|---|---|---|---|
| time-of-day | 0.4237 | 0.5816 | +0.0% | 0.454 | — | — |
| last bar | 0.5771 | 0.7435 | -63.4% | 0.283 | +0.1534 [+0.1153, +0.1924] | +0.1881 [+0.1549, +0.2217] |
| ewma | 0.4427 | 0.5705 | +3.8% | 0.409 | +0.0189 [-0.0111, +0.0487] | +0.0536 [+0.0312, +0.0767] |
| tod+ewma | 0.3890 | 0.5210 | +19.8% | 0.590 | -0.0347 [-0.0570, -0.0126] | — |
| chronos-bolt | 0.3938 | 0.5193 | +20.3% | 0.565 | -0.0300 [-0.0541, -0.0063] | +0.0047 [-0.0112, +0.0207] |
| chronos-2 | 0.3489 | 0.4677 | +35.3% | 0.666 | -0.0748 [-0.1011, -0.0492] | -0.0401 [-0.0609, -0.0198] |
| ag-ts | 0.4154 | 0.5458 | +11.9% | 0.542 | -0.0083 [-0.0393, +0.0225] | +0.0264 [+0.0045, +0.0484] |
| ag-tab | 0.3838 | 0.5194 | +20.2% | 0.579 | -0.0399 [-0.0599, -0.0193] | -0.0052 [-0.0259, +0.0161] |

dir ag-ts best: WeightedEnsemble · tod+ewma alpha: 0.1 · vol ag-ts best: WeightedEnsemble

## CL_F  (1128 bars; test 543 direction / 557 volatility)

Direction — up-rate in test 50.8% (best constant 50.8%)

| model | acc | 95% CI | Brier | vs constant (acc) | ΔBrier vs constant [95% CI] |
|---|---|---|---|---|---|
| constant | 50.8% | 47–55% | 0.2500 | — | — |
| momentum | 50.8% | 47–55% | 0.2501 | +0/−0, p=1.000 | +0.0001 [-0.0001, +0.0002] |
| chronos-bolt | 51.2% | 47–55% | 0.2691 | +158/−156, p=0.955 | +0.0191 [+0.0045, +0.0338] |
| chronos-2 | 49.5% | 45–54% | 0.2773 | +173/−180, p=0.750 | +0.0272 [+0.0132, +0.0412] |
| ag-ts | 54.1% | 50–58% | 0.2652 | +124/−106, p=0.262 | +0.0151 [+0.0018, +0.0290] |
| ag-tab | 53.4% | 49–58% | 0.2503 | +94/−80, p=0.324 | +0.0003 [-0.0021, +0.0027] |

Volatility — next bar's log range; skill = 1 − MSE/MSE(time-of-day)

| model | MAE | RMSE | skill vs time-of-day | Spearman ρ | ΔMAE vs time-of-day [95% CI] | ΔMAE vs tod+ewma [95% CI] |
|---|---|---|---|---|---|---|
| time-of-day | 0.4588 | 0.6024 | +0.0% | 0.539 | — | — |
| last bar | 0.6295 | 0.8226 | -86.5% | 0.357 | +0.1707 [+0.1287, +0.2091] | +0.2003 [+0.1660, +0.2339] |
| ewma | 0.4889 | 0.6325 | -10.3% | 0.492 | +0.0300 [-0.0003, +0.0594] | +0.0597 [+0.0332, +0.0861] |
| tod+ewma | 0.4292 | 0.5660 | +11.7% | 0.608 | -0.0296 [-0.0521, -0.0069] | — |
| chronos-bolt | 0.4339 | 0.5712 | +10.1% | 0.607 | -0.0249 [-0.0487, -0.0007] | +0.0047 [-0.0104, +0.0195] |
| chronos-2 | 0.3984 | 0.5192 | +25.7% | 0.666 | -0.0604 [-0.0869, -0.0345] | -0.0308 [-0.0525, -0.0090] |
| ag-ts | 0.4302 | 0.5583 | +14.1% | 0.608 | -0.0287 [-0.0527, -0.0052] | +0.0010 [-0.0189, +0.0207] |
| ag-tab | 0.4327 | 0.5567 | +14.6% | 0.610 | -0.0261 [-0.0458, -0.0062] | +0.0036 [-0.0155, +0.0228] |

dir ag-ts best: WeightedEnsemble · tod+ewma alpha: 0.2 · vol ag-ts best: WeightedEnsemble
