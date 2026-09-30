# Sector research (research only)

Which stock-market sectors see more **green (up) days than red (down) days**, on average, over the last 5 years and going forward.

- **No execution, no signals.** Nothing here places orders, feeds the ledger or gates any alert. It is descriptive statistics.
- **No data stored.** Each run pulls ~5 years of daily bars from Yahoo into memory, computes the statistics, and emails or prints them. No cache, no data files in the repo. (launchd's stderr log stays empty unless a run fails.)
- **Universe:** the 11 GICS sectors via SPDR Select Sector ETFs (XLK XLF XLV XLE XLY XLP XLI XLU XLB XLRE XLC), 16 industry groups as a secondary table, SPY as benchmark.

## Measures
| Measure | Meaning |
|---|---|
| Green share | up days / (up + down days), close vs prior close; unchanged days dropped |
| Avg green / red day, size ratio | average move on up days vs down days (count alone is not performance) |
| p vs 50% | two-sided test that the green share differs from a coin flip |
| vs SPY | green share minus SPY's |
| Green candle | close > open, the intraday direction |
| Up on days SPY fell | how often the sector closes up when the market closes down (defensive behavior) |
| Rank change | 5y green-share rank now vs as of 5 trading days ago (recomputed from the same in-memory data) |

Windows: 5 years, 1 year, 6 months, 3 months.

## Run
```bash
/usr/bin/python3 sector_breadth.py               # print
/usr/bin/python3 sector_breadth.py --email       # weekly email
/usr/bin/python3 sector_breadth.py --email --test
```
Scheduled by `com.dhruv.sectorresearch` (Saturdays 08:30 local). Uses the shared email layout (`~/flip-notifier/email_ui.py`).

## Caveats
Stocks drift up, so all sectors sit above 50%; the information is in the spread between sectors. Sectors are highly correlated (eleven results are not eleven independent findings). One five-year window is one market regime. XLRE and XLC have shorter histories than five years.
