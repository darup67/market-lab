# Kalshi 15-minute lab

A durable record of Kalshi's 15-minute crypto contracts, and tests over it.

**Why it exists:** Kalshi serves roughly the last **two days** of settled
markets, and trades vanish with them. Any question about these contracts that
needs more history than that can only be answered by data captured as it goes
past. Nothing here can be backfilled later — which is why `data/*.jsonl` is
committed rather than ignored.

```
KXBTC15M   BTC price up in next 15 mins?     ~8,000 trades/window
KXETH15M   ETH                               ~4,800
KXXRP15M   XRP                               ~2,800
KXSOL15M   SOL                               ~2,400
```

## Capture

```bash
node record.js              # every new settled window, all four series
node record.js --status     # how much history is on disk
node record.js --series KXETH15M
```

Runs every 10 minutes under `com.dhruv.kalshi15m`. Windows settle every 15
minutes and the API retains ~2 days of them, so several consecutive failures
still recover on the next success.

**Append-only and idempotent.** Rows are keyed by window ticker; a re-run adds
nothing. Verified: a second run reported `796 already had`.

Each row carries the settlement, the opening and closing prints, min/max/range,
quartile prices, and a 15-point downsampled path — enough to reconstruct how a
window resolved without storing 8,000 raw trades.

```json
{"series":"KXBTC15M","window":"26SEP071130","target":79054.02,
 "settled_yes":false,"trades":10000,"first":73,"last":0.1,
 "min":0.1,"max":85,"range":84.9,"q25":61,"q50":31,"q75":11,
 "path":[73,75,77,68,61,36,29,31,27,29,...]}
```

That row is a good example of why the path matters: it opened at 73¢ — the
market leaning YES — and settled NO.

## Storage ceiling

Capture halts when `data/` reaches **5 GB** (`KLAB_MAX_BYTES` to change it).

At the observed **~425 bytes/row** and 384 rows a day, that is roughly **57 MB a
year — about ninety years** from the ceiling. It is a guard against a bug
writing in a loop, not a capacity limit you will meet.

Because hitting it stops capture of data that **cannot be backfilled**, the stop
is deliberately loud rather than quiet:

- logs `STOPPED: … Recording is HALTED and these windows cannot be backfilled later`
- **exits non-zero**, so `launchctl list` shows a failing agent instead of a clean one
- warns from **80%** onward, so approach is visible before arrival
- `--status` prints usage, percentage, and years remaining

```
  storage 334 KB of 5.00 GB ceiling (0.006%)
  growing ~159 KB/day -> ceiling in ~90 years
```

The check runs before any capture, so a run either records a complete set of
rows or none — never a file truncated mid-window.

## Querying

Three ways in, in increasing order of power.

**1. The built-in report** — the tests that matter, already written:

```bash
python3 analyze.py            # all series
python3 analyze.py KXBTC15M
```

**2. `jq`** — best for quick, one-off questions against the raw JSONL:

```bash
cd data

# windows that opened above 80c and still settled NO
jq -r 'select(.first>80 and .settled_yes==false)
       | "\(.window)  open \(.first)c  -> NO"' KXBTC15M.jsonl

# mean range for a series
jq -s 'map(.range)|add/length' KXBTC15M.jsonl
```

**3. SQL** — for anything with grouping or joins. `query.py` loads every row
into an in-memory SQLite table called `w`; nothing is written back, so a bad
query costs nothing:

```bash
python3 query.py                       # schema + sample rows
python3 query.py "SELECT ..."          # inline
echo "SELECT ..." | python3 query.py   # piped
python3 query.py -f myquery.sql        # from a file
```

```sql
SELECT series,
       COUNT(*) n,
       ROUND(AVG("range"),1) avg_range,
       ROUND(100.0*SUM(settled_yes)/COUNT(*),1) pct_yes,
       ROUND(100.0*SUM(CASE WHEN ("first">50)<>settled_yes THEN 1 ELSE 0 END)
             /COUNT(*),1) pct_open_wrong
FROM w GROUP BY series ORDER BY avg_range;
```

```
  series    n    avg_range  pct_yes  pct_open_wrong
  KXBTC15M  201  41.8       46.8     17.4
  KXETH15M  201  67.9       52.7     45.8
  KXSOL15M  201  68.7       46.8     44.8
  KXXRP15M  201  70.3       51.7     44.8
```

That last column is the most interesting thing in the dataset so far. **BTC's
opening print is wrong 17% of the time; the other three are wrong ~45%** — a
coin flip. BTC's book is deep enough to price the window at the open; the
others are not really priced at all.

## Daily report

An email lands at **07:00 Eastern** each morning (`com.dhruv.kalshi15m.report`) at
darup67@gmail.com. It reuses `~/flip-notifier/send-email.js` and the same
Keychain app password, so there is no second credential to manage.

```bash
python3 report.py --dry     # print it, send nothing
python3 report.py           # build and send
```

**It leads with health, not statistics.** The failure that matters here is
silent — Kalshi keeps ~2 days of settled markets, so a stopped recorder loses
history permanently rather than falling behind. So the subject line carries the
verdict (`OK` / `ATTENTION (n)`) and the body opens with any problems:

- newest window more than 2h old — the recorder may have stopped
- missing 15-minute slots inside the range already held
- nothing captured in 24h
- storage past 80% of the ceiling

Then, in order:

- **CAPTURE** — windows added, storage against the ceiling, years remaining
- **READINGS** — per-series range, YES rate and opening-print accuracy
- **BTC VOLATILITY REGIME** — current band and index, the last 2h as a sparkline,
  the 24h split across LOW/NORMAL/HIGH, and how many times the band changed
- **BTC INDICATORS** — the calibration curve with confidence intervals and an
  ASCII bar, reversal rate, and the widest windows
- **FINDINGS** — the durable conclusions, **recomputed every morning** so they
  age with the data instead of being quoted from a README that went stale

The regime is computed from this repo's own rows using the same 4-window
smoothing and 28¢/52¢ bands as the notifier, so the email can never disagree
with the dataset it ships.

```
BTC VOLATILITY REGIME
  now        NORMAL  32.8c   (LOW <28c · NORMAL 28-52c · HIGH >52c)
  last 2h    54 39 37 46 31 32 43 33c
              H  ~  ~  ~  ~  ~  ~  ~
  24h split  LOW 21%  NORMAL 52%  HIGH 27%
             28 band change(s) in 24h
```

That last line is itself a finding: **28 band changes in a day** means the
regime is unstable at this smoothing, which is the same story the +0.14
persistence correlation tells.

Scheduled with `StartCalendarInterval`, which launchd reads in the machine's
timezone. That means **07:00 EDT in summer and 07:00 EST in winter** — the
clock time you actually want. Pinning it to a fixed UTC-5 would deliver at
08:00 for most of the year instead. If the Mac is asleep at 07:00, launchd runs
it on wake.

### One bug worth recording

The staleness check originally decoded the window label (`26SEP071130`) as UTC
and reported every series **four hours stale**, crying wolf about a recorder
that was running fine. Those labels are **Eastern** — that window's `close_time`
is `15:30Z`. The check now keys on `close_time`, which is explicit ISO UTC and
needs no decoding.

## Analysis

```bash
python3 analyze.py            # all series
python3 analyze.py KXBTC15M
```

Stdlib only, no dependencies. Every proportion is reported with a **95%
confidence interval**, because small samples lie confidently without one.

### What the first 201 BTC windows say

| | |
|---|---|
| Base rate | 46.8% YES  [40%, 54%] |
| Opening print wrong | 17%  [13%, 23%] |
| ...when it opens <10¢ or >90¢ | 10% |
| ...when it opens 35–65¢ | 30% |
| Effectively decided at the open | 31% |
| Vol persistence, corr(prior hour, next window) | **+0.14** |

**The vol-persistence number is the load-bearing one.** A LOW/NORMAL/HIGH
volatility filter is only useful if a calm hour predicts a calm next window.
At +0.14 it barely does: after LOW the next window averages 37.1¢ of range,
after NORMAL 40.5¢. That gap is thin. Only HIGH separates meaningfully (48.8¢).

So the volatility index in the sibling `flip-notifier` repo is sound as
*context* — it accurately describes what just happened — but the data does not
support treating it as a *signal*.

## What this is not

Not a strategy, and a passing statistic here is not an edge:

- **Every number is in-sample.** Fitting bands to the same data you evaluate on
  is how you find patterns that do not survive.
- **You cannot trade the opening print.** It is the first execution — already
  gone by the time you see it.
- **Fees are not modelled.** On 15-minute contracts traded frequently they
  would consume an edge far larger than anything visible here.
- **The sample is short.** 201 windows is about two days, and one of those was
  a US market holiday with thin crypto liquidity.

The honest use of this repo is to accumulate enough history that a question can
be asked out-of-sample. That takes weeks, not days.
