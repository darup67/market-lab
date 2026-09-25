# ops

**`watchdog.py`**: one launchd job (`com.dhruv.watchdog`, at :07, :22, :37 and :52
past every hour) keeps the scheduled agents honest without the Claude app. Each pass
checks what should have happened by now, repairs what it can, and emails an alert only
when repair has failed. If Gmail itself is down, it posts a Mac notification with sound
instead. It never re-sends an email that already went out.

| Check | Deadline | Repair | Alert if still failing |
|---|---|---|---|
| outbox (Gmail refused a send) | every pass | `desk.py outbox` resends | after 12:00 |
| Zillow digest | 07:50 daily | re-run `com.dhruv.zillowagent`, max 2 | 09:00 |
| Watchlist email | 09:05 daily | re-run the watchlist job, max 2 | 10:00 |
| Health care IV scan | 09:58 trading days | re-run the 09:45 scan once | (the email then says the scan is missing) |
| Sector IV scans (10) | 10:03 trading days | re-run `run-sectors.sh` once | (same) |
| Bio/pharma email | 10:45 trading days | catch-up send, max 2 | 11:30 |
| Sector emails (10) | 10:50 trading days | send only the missing sectors, max 2 | 11:45 |
| Flip notifier | every pass 09:15–16:15 on trading days, hourly otherwise | `healthcheck.js --repair` | BROKEN during market hours, at most every 2 h |
| NYSE holiday list | yearly | none | when the current year is missing |

A trading day is a weekday that isn't in `nyse_holidays.json`. The IV agent and
event-desk share that list, so no scans or market emails are expected on NYSE holidays.
The watchlist and Zillow emails are daily regardless.

```bash
~/.venvs/market-ml/bin/python ~/market-lab/ops/watchdog.py --status   # last pass
~/.venvs/market-ml/bin/python ~/market-lab/ops/watchdog.py --dry      # what it would do now
WATCHDOG_NOW=2026-09-25T11:50 ~/.venvs/market-ml/bin/python watchdog.py --dry   # simulate a time
```

Status for the Asset Agents dashboard goes to `data/status.json`. Retry counts and alert
de-duplication go to `data/watchdog-state.json`. Both are local and git-ignored.

**What it cannot fix:** the Mac being off, or with no network. launchd runs a missed
calendar job when the Mac wakes; Amphetamine and `caffeinate` keep it from sleeping.
TradingView Desktop also has to be open for the flip notifier to work.
