# Chronos + AutoGluon vs. the Kalshi price at minutes 1, 3, 4 — 2026-10-02

Not pre-registered. Each contender sees what is known at minute m and calls the settlement; the baseline is Kalshi's own YES mid at the same minute. Brier: lower is better. 'vs market' = windows only this model got right / only the market got right (exact McNemar p).

## Minute 1: 2582 windows · fit 1291 (KXBTC15M-26SEP050945-45 …) · test 1291

| model | acc | 95% CI | Brier | vs market (acc) | ΔBrier vs market [95% CI] |
|---|---|---|---|---|---|
| market | 60.0% | 57%–63% | 0.2341 | — | — |
| cushion | 59.3% | 57%–62% | 0.2363 | +43/−52, p=0.412 | +0.0022 [+0.0003, +0.0041] |
| chronos-bolt | 56.2% | 53%–59% | 0.2577 | +156/−205, p=0.011 | +0.0236 [+0.0156, +0.0315] |
| chronos-2 | 58.9% | 56%–62% | 0.2453 | +91/−104, p=0.390 | +0.0112 [+0.0068, +0.0155] |
| ag-tab | 58.5% | 56%–61% | 0.2531 | +141/−160, p=0.299 | +0.0190 [+0.0118, +0.0263] |
| ag-tab+mkt | 60.1% | 57%–63% | 0.2383 | +75/−73, p=0.935 | +0.0041 [+0.0010, +0.0074] |
  ag-tab top features: r2 +0.0240, r1 +0.0081, gap +0.0043, r16 +0.0037, r8 +0.0012
  ag-tab+mkt top features: market +0.0162, z +0.0019, r2 +0.0017, rv16 +0.0010, hour +0.0010

## Minute 3: 2581 windows · fit 1290 (KXBTC15M-26SEP050945-45 …) · test 1291

| model | acc | 95% CI | Brier | vs market (acc) | ΔBrier vs market [95% CI] |
|---|---|---|---|---|---|
| market | 68.8% | 66%–71% | 0.2041 | — | — |
| cushion | 68.6% | 66%–71% | 0.2069 | +27/−30, p=0.791 | +0.0028 [+0.0006, +0.0050] |
| chronos-bolt | 64.4% | 62%–67% | 0.2256 | +89/−146, p=0.000 | +0.0215 [+0.0145, +0.0282] |
| chronos-2 | 67.1% | 64%–70% | 0.2135 | +42/−64, p=0.041 | +0.0094 [+0.0053, +0.0133] |
| ag-tab | 67.2% | 65%–70% | 0.2099 | +32/−52, p=0.038 | +0.0058 [+0.0029, +0.0087] |
| ag-tab+mkt | 67.2% | 65%–70% | 0.2084 | +25/−46, p=0.017 | +0.0043 [+0.0019, +0.0067] |
  ag-tab top features: z +0.0268, gap +0.0116, r4 +0.0105, r8 +0.0013, r2 +0.0009
  ag-tab+mkt top features: market +0.0187, z +0.0158, gap +0.0056, r4 +0.0048, r8 +0.0010

## Minute 4: 2582 windows · fit 1291 (KXBTC15M-26SEP050945-45 …) · test 1291

| model | acc | 95% CI | Brier | vs market (acc) | ΔBrier vs market [95% CI] |
|---|---|---|---|---|---|
| market | 70.2% | 68%–73% | 0.1945 | — | — |
| cushion | 69.1% | 67%–72% | 0.1973 | +16/−30, p=0.054 | +0.0028 [+0.0005, +0.0049] |
| chronos-bolt | 65.5% | 63%–68% | 0.2153 | +69/−130, p=0.000 | +0.0208 [+0.0148, +0.0269] |
| chronos-2 | 67.9% | 65%–70% | 0.2034 | +37/−66, p=0.006 | +0.0089 [+0.0051, +0.0128] |
| ag-tab | 68.8% | 66%–71% | 0.2007 | +33/−51, p=0.063 | +0.0062 [+0.0034, +0.0089] |
| ag-tab+mkt | 68.8% | 66%–71% | 0.1996 | +30/−48, p=0.054 | +0.0051 [+0.0025, +0.0077] |
  ag-tab top features: z +0.0434, gap +0.0194, r4 +0.0164, r8 +0.0026, r2 +0.0017
  ag-tab+mkt top features: market +0.0435, z +0.0162, r4 +0.0115, gap +0.0055, r8 +0.0024

