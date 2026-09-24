# Chronos on CME futures — 2026-09-23 (second run, 10-day test)

Re-run of the 2026-09-18 test with five more days of data. Chronos only —
AutoGluon was not run (`--no-ag`), since on 09-18 it never beat the simple
baselines. NQ/ES/YM/GC/CL 15-minute bars, ~1,400 per symbol; fit on the first
half, test on the second (~690 bars, 10 trading days).

## Verdict: the volatility result held, and got more robust

Chronos-2 again beat the strongest simple baseline (time-of-day average plus an
EWMA of the deviation from it) on all five symbols, with every 95% CI excluding
zero. Chronos-Bolt again only tied it.

| | skill vs time-of-day | Chronos-2 ΔMAE vs tod+ewma [95% CI] | Chronos-Bolt ΔMAE |
|---|---|---|---|
| NQ | +43.1% | -0.048 [-0.069, -0.028] | -0.003 |
| ES | +42.5% | -0.054 [-0.074, -0.034] | -0.013 |
| YM | +37.5% | -0.063 [-0.084, -0.042] | -0.003 |
| GC | +37.1% | -0.048 [-0.066, -0.030] | +0.002 |
| CL | +21.6% | -0.039 [-0.057, -0.021] | -0.000 |

Day by day (tod+ewma at alpha 0.2 for all symbols):

| sym | days Chronos-2 better | ΔMAE | ΔMAE without its best day | best day |
|---|---|---|---|---|
| NQ | 7/10 | −0.023 | −0.017 | 2026-09-21 |
| ES | 10/10 | −0.030 | −0.026 | 2026-09-14 |
| YM | 9/10 | −0.044 | −0.037 | 2026-09-14 |
| GC | 8/10 | −0.037 | −0.025 | 2026-09-14 |
| CL | 8/10 | −0.023 | −0.016 | 2026-09-14 |

**This is the main update to the 09-18 finding.** Then, one shock day (Sep 14)
carried most of the edge, and gold and crude kept almost nothing without it.
With ten test days, removing the best day leaves most of the edge on every
symbol, gold and crude included. The per-day win rate is 7–10 of 10.

**Direction is still nothing.** No model beat the best constant on any symbol,
same as 09-18.

## Standing caveats

- Forecasting how big the next bar is, not which way it goes. Useful for
  position sizing or stop distance; it is not a trade signal.
- Yahoo data, ~10 minutes delayed.
- NQ/ES/YM are one market, so this is ~3 independent tests, not 5.
- In-sample in the sense that the model choice (Chronos-2) came from the 09-18
  run on overlapping data. The honest out-of-sample portion is Sep 18–23.

Re-run: `~/.venvs/market-ml/bin/python ml_futures.py`
Raw numbers: `ml-futures-2026-09-23.json`


## NQ_F  (1400 bars; test 686 direction / 692 volatility)

Direction — up-rate in test 53.8% (best constant 53.8%)

| model | acc | 95% CI | Brier | vs constant (acc) | ΔBrier vs constant [95% CI] |
|---|---|---|---|---|---|
| constant | 46.2% | 43–50% | 0.2500 | — | — |
| momentum | 51.2% | 47–55% | 0.2499 | +198/−164, p=0.083 | -0.0001 [-0.0015, +0.0013] |
| chronos-bolt | 49.9% | 46–54% | 0.2723 | +215/−190, p=0.233 | +0.0223 [+0.0109, +0.0340] |
| chronos-2 | 48.8% | 45–53% | 0.2956 | +136/−118, p=0.286 | +0.0456 [+0.0309, +0.0613] |

Volatility — next bar's log range; skill = 1 − MSE/MSE(time-of-day)

| model | MAE | RMSE | skill vs time-of-day | Spearman ρ | ΔMAE vs time-of-day [95% CI] | ΔMAE vs tod+ewma [95% CI] |
|---|---|---|---|---|---|---|
| time-of-day | 0.4612 | 0.5960 | +0.0% | 0.503 | — | — |
| last bar | 0.6064 | 0.7602 | -62.7% | 0.245 | +0.1452 [+0.1078, +0.1826] | +0.2088 [+0.1801, +0.2385] |
| ewma | 0.4506 | 0.5755 | +6.7% | 0.417 | -0.0106 [-0.0402, +0.0193] | +0.0530 [+0.0320, +0.0737] |
| tod+ewma | 0.3976 | 0.5143 | +25.5% | 0.600 | -0.0636 [-0.0880, -0.0393] | — |
| chronos-bolt | 0.3944 | 0.5065 | +27.8% | 0.596 | -0.0668 [-0.0906, -0.0430] | -0.0032 [-0.0162, +0.0098] |
| chronos-2 | 0.3494 | 0.4497 | +43.1% | 0.705 | -0.1118 [-0.1390, -0.0846] | -0.0482 [-0.0687, -0.0284] |

tod+ewma alpha: 0.2

## ES_F  (1400 bars; test 670 direction / 692 volatility)

Direction — up-rate in test 51.9% (best constant 51.9%)

| model | acc | 95% CI | Brier | vs constant (acc) | ΔBrier vs constant [95% CI] |
|---|---|---|---|---|---|
| constant | 48.1% | 44–52% | 0.2503 | — | — |
| momentum | 48.1% | 44–52% | 0.2503 | +0/−0, p=1.000 | +0.0000 [-0.0000, +0.0000] |
| chronos-bolt | 51.0% | 47–55% | 0.2783 | +197/−177, p=0.326 | +0.0280 [+0.0140, +0.0434] |
| chronos-2 | 46.9% | 43–51% | 0.2946 | +138/−146, p=0.678 | +0.0443 [+0.0290, +0.0601] |

Volatility — next bar's log range; skill = 1 − MSE/MSE(time-of-day)

| model | MAE | RMSE | skill vs time-of-day | Spearman ρ | ΔMAE vs time-of-day [95% CI] | ΔMAE vs tod+ewma [95% CI] |
|---|---|---|---|---|---|---|
| time-of-day | 0.4713 | 0.6133 | +0.0% | 0.587 | — | — |
| last bar | 0.6152 | 0.7788 | -61.2% | 0.382 | +0.1439 [+0.1068, +0.1810] | +0.2092 [+0.1806, +0.2381] |
| ewma | 0.4715 | 0.6021 | +3.6% | 0.539 | +0.0002 [-0.0292, +0.0314] | +0.0655 [+0.0441, +0.0872] |
| tod+ewma | 0.4060 | 0.5311 | +25.0% | 0.689 | -0.0653 [-0.0889, -0.0412] | — |
| chronos-bolt | 0.3928 | 0.5167 | +29.0% | 0.700 | -0.0785 [-0.1014, -0.0544] | -0.0132 [-0.0262, -0.0003] |
| chronos-2 | 0.3522 | 0.4650 | +42.5% | 0.778 | -0.1192 [-0.1446, -0.0942] | -0.0539 [-0.0744, -0.0342] |

tod+ewma alpha: 0.2

## YM_F  (1401 bars; test 676 direction / 693 volatility)

Direction — up-rate in test 52.4% (best constant 52.4%)

| model | acc | 95% CI | Brier | vs constant (acc) | ΔBrier vs constant [95% CI] |
|---|---|---|---|---|---|
| constant | 47.6% | 44–51% | 0.2506 | — | — |
| momentum | 48.7% | 45–52% | 0.2516 | +175/−168, p=0.746 | +0.0010 [-0.0007, +0.0026] |
| chronos-bolt | 47.3% | 44–51% | 0.3099 | +134/−136, p=0.951 | +0.0593 [+0.0430, +0.0756] |
| chronos-2 | 53.4% | 50–57% | 0.2662 | +236/−197, p=0.068 | +0.0156 [+0.0035, +0.0284] |

Volatility — next bar's log range; skill = 1 − MSE/MSE(time-of-day)

| model | MAE | RMSE | skill vs time-of-day | Spearman ρ | ΔMAE vs time-of-day [95% CI] | ΔMAE vs tod+ewma [95% CI] |
|---|---|---|---|---|---|---|
| time-of-day | 0.4684 | 0.6107 | +0.0% | 0.687 | — | — |
| last bar | 0.7025 | 0.8819 | -108.5% | 0.434 | +0.2342 [+0.1944, +0.2722] | +0.2674 [+0.2359, +0.2986] |
| ewma | 0.5489 | 0.6928 | -28.7% | 0.562 | +0.0805 [+0.0493, +0.1135] | +0.1138 [+0.0864, +0.1415] |
| tod+ewma | 0.4351 | 0.5627 | +15.1% | 0.740 | -0.0332 [-0.0552, -0.0120] | — |
| chronos-bolt | 0.4318 | 0.5605 | +15.8% | 0.749 | -0.0365 [-0.0597, -0.0143] | -0.0033 [-0.0177, +0.0111] |
| chronos-2 | 0.3722 | 0.4829 | +37.5% | 0.829 | -0.0961 [-0.1210, -0.0706] | -0.0629 [-0.0836, -0.0415] |

tod+ewma alpha: 0.2

## GC_F  (1401 bars; test 688 direction / 693 volatility)

Direction — up-rate in test 48.3% (best constant 51.7%)

| model | acc | 95% CI | Brier | vs constant (acc) | ΔBrier vs constant [95% CI] |
|---|---|---|---|---|---|
| constant | 51.7% | 48–55% | 0.2501 | — | — |
| momentum | 48.3% | 45–52% | 0.2533 | +150/−174, p=0.201 | +0.0031 [-0.0000, +0.0060] |
| chronos-bolt | 50.1% | 46–54% | 0.2740 | +158/−169, p=0.580 | +0.0239 [+0.0140, +0.0347] |
| chronos-2 | 48.1% | 44–52% | 0.2695 | +157/−182, p=0.192 | +0.0194 [+0.0093, +0.0293] |

Volatility — next bar's log range; skill = 1 − MSE/MSE(time-of-day)

| model | MAE | RMSE | skill vs time-of-day | Spearman ρ | ΔMAE vs time-of-day [95% CI] | ΔMAE vs tod+ewma [95% CI] |
|---|---|---|---|---|---|---|
| time-of-day | 0.4323 | 0.5741 | +0.0% | 0.479 | — | — |
| last bar | 0.6350 | 0.7841 | -86.5% | 0.155 | +0.2028 [+0.1682, +0.2382] | +0.2455 [+0.2153, +0.2763] |
| ewma | 0.4532 | 0.5721 | +0.7% | 0.373 | +0.0209 [-0.0046, +0.0462] | +0.0637 [+0.0437, +0.0832] |
| tod+ewma | 0.3895 | 0.5128 | +20.2% | 0.590 | -0.0428 [-0.0618, -0.0238] | — |
| chronos-bolt | 0.3912 | 0.5096 | +21.2% | 0.572 | -0.0410 [-0.0609, -0.0204] | +0.0017 [-0.0116, +0.0149] |
| chronos-2 | 0.3417 | 0.4555 | +37.1% | 0.696 | -0.0906 [-0.1138, -0.0690] | -0.0478 [-0.0661, -0.0303] |

tod+ewma alpha: 0.1

## CL_F  (1400 bars; test 670 direction / 692 volatility)

Direction — up-rate in test 49.6% (best constant 50.4%)

| model | acc | 95% CI | Brier | vs constant (acc) | ΔBrier vs constant [95% CI] |
|---|---|---|---|---|---|
| constant | 49.6% | 46–53% | 0.2507 | — | — |
| momentum | 49.6% | 46–53% | 0.2504 | +0/−0, p=1.000 | -0.0002 [-0.0006, +0.0002] |
| chronos-bolt | 50.9% | 47–55% | 0.2661 | +232/−223, p=0.708 | +0.0154 [+0.0031, +0.0276] |
| chronos-2 | 46.7% | 43–51% | 0.2854 | +198/−217, p=0.377 | +0.0347 [+0.0226, +0.0467] |

Volatility — next bar's log range; skill = 1 − MSE/MSE(time-of-day)

| model | MAE | RMSE | skill vs time-of-day | Spearman ρ | ΔMAE vs time-of-day [95% CI] | ΔMAE vs tod+ewma [95% CI] |
|---|---|---|---|---|---|---|
| time-of-day | 0.4370 | 0.5703 | +0.0% | 0.595 | — | — |
| last bar | 0.6514 | 0.8278 | -110.7% | 0.331 | +0.2145 [+0.1793, +0.2490] | +0.2272 [+0.1960, +0.2568] |
| ewma | 0.5010 | 0.6418 | -26.6% | 0.468 | +0.0640 [+0.0399, +0.0898] | +0.0767 [+0.0541, +0.0992] |
| tod+ewma | 0.4242 | 0.5581 | +4.2% | 0.622 | -0.0127 [-0.0310, +0.0056] | — |
| chronos-bolt | 0.4241 | 0.5591 | +3.9% | 0.636 | -0.0128 [-0.0333, +0.0073] | -0.0001 [-0.0125, +0.0123] |
| chronos-2 | 0.3853 | 0.5051 | +21.6% | 0.708 | -0.0516 [-0.0743, -0.0300] | -0.0389 [-0.0571, -0.0209] |

tod+ewma alpha: 0.2
