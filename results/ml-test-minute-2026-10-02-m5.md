# Chronos + AutoGluon vs. the Kalshi price at minutes 5 — 2026-10-02

Not pre-registered. Each contender sees what is known at minute m and calls the settlement; the baseline is Kalshi's own YES mid at the same minute. Brier: lower is better. 'vs market' = windows only this model got right / only the market got right (exact McNemar p).

## Minute 5: 2583 windows · fit 1291 (KXBTC15M-26SEP050945-45 …) · test 1292

| model | acc | 95% CI | Brier | vs market (acc) | ΔBrier vs market [95% CI] |
|---|---|---|---|---|---|
| market | 71.7% | 69%–74% | 0.1844 | — | — |
| cushion | 71.1% | 69%–73% | 0.1866 | +14/−22, p=0.243 | +0.0021 [-0.0001, +0.0043] |
| chronos-bolt | 69.0% | 66%–71% | 0.2036 | +60/−95, p=0.006 | +0.0192 [+0.0135, +0.0254] |
| chronos-2 | 69.7% | 67%–72% | 0.1934 | +32/−57, p=0.011 | +0.0089 [+0.0051, +0.0127] |
| ag-tab | 70.9% | 68%–73% | 0.1899 | +39/−49, p=0.337 | +0.0055 [+0.0022, +0.0087] |
| ag-tab+mkt | 71.0% | 68%–73% | 0.1923 | +48/−57, p=0.435 | +0.0078 [+0.0038, +0.0122] |
  ag-tab top features: z +0.1039, gap +0.0363, r4 +0.0055, r2 +0.0029, r16 +0.0027
  ag-tab+mkt top features: market +0.1199, z +0.0205, gap +0.0079, r2 +0.0043, sig +0.0020

