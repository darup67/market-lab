# Market lab

A durable record of **expiring market data**, and tests over it.

Two recorders, one premise: both sources serve only a few days of history, so
whatever you have in a month is whatever you started collecting today. Nothing
here can be backfilled.

| Recorder | Source | Retention | Cadence |
|---|---|---|---|
| `record.js` | Kalshi 15-minute crypto contracts | ~2 days | every 10 min |
| `record-futures.js` | CME futures via Yahoo, 15-minute bars | ~5 days | every 30 min |

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

## Futures

`NQ=F` Nasdaq · `ES=F` S&P · `YM=F` Dow · `GC=F` gold · `CL=F` crude — the five
contracts behind the MNQ/MES/MYM/MGC/MCL micros watched in `flip-notifier`.

```bash
node record-futures.js            # capture new bars
node record-futures.js --status
```

Deduped on bar timestamp, so a re-run is a no-op. Bars Yahoo pads with nulls
for periods that never traded are dropped rather than stored — keeping them
would put fake flat bars into a volatility study.

**What this data is for.** These feeds are delayed (~10 min, same as the `_DL`
feeds in flip-notifier), so it is research data — volatility work, daily-horizon
studies — **not an intraday signal source**. A 15-minute forecast built on
10-minute-old prices leaves almost no usable edge against participants at the
exchange. Real-time CME data needs a paid TradingView plan plus the exchange
add-on.

The interesting question for futures is **volatility, not direction**. Session
structure is real and learnable — Asian overnight is thin, the US cash open at
09:30 ET spikes — whereas direction runs into the same efficient-market wall the
Kalshi data already demonstrated. And the honest baseline for volatility is not
a random walk but *average volatility by hour of day*, which a lookup table
gives you for free.

## Capture

```bash
node record.js              # every new settled window, all four series
node record.js --status     # how much history is on disk
node record.js --series KXETH15M
```

Runs every 10 minutes under `com.dhruv.marketlab`. Windows settle every 15
minutes and the API retains ~2 days of them, so several consecutive failures
still recover on the next success.

**Auto-committed.** New rows are committed and pushed to the private remote on
the run that captures them (`KLAB_AUTOCOMMIT=0` to disable). Without this the
working tree is permanently dirty — the recorder appends every 10 minutes — and
the remote permanently stale, so a disk failure would lose everything since
whoever last remembered to commit. For data that cannot be backfilled, a remote
that only updates by hand is not a backup.

**launchd needs an explicit PATH.** It starts jobs with
`/usr/bin:/bin:/usr/sbin:/sbin`, which omits `/usr/local/bin` — so `git` could
not find `git-credential-osxkeychain` and every scheduled push failed auth,
while pushes by hand worked fine. `report.py` had the same problem finding
`node`. Both plists now set PATH explicitly. This class of bug is invisible
from a terminal; it only appears under the scheduler.

The push also runs whenever anything is unpushed, not only when a run captured
rows — otherwise a push that failed earlier would sit until the next run that
happened to capture something, leaving the only copy of a window on one disk.

It is best-effort by design: a failed push is logged and the run still succeeds,
because losing the next window to a network blip is worse than a remote that is
briefly behind. The commit is already local, so the next run pushes both.

**Append-only and idempotent.** Rows are keyed by window ticker; a re-run adds
nothing. Verified: a second run reported `796 already had`.

Each row carries the settlement, `minute_quotes` (the clock-aligned YES bid/ask
at the open and at the end of each of the 15 minutes, in cents, from Kalshi's
1-minute candles), and legacy trade-derived fields: `first`/`last`,
min/max/range, quartiles and a 15-point `path`.

> **Correction, 2026-09-23 — the legacy fields are not what their names say.**
> The recorder keeps only the newest 10,000 trades, and *every* BTC window hits
> that cap. So `first` is a mid-window price, not the opening print; `path` is
> spaced by trade count, not by minute; and `range`/min/max cover only the tail
> of the window. Use `minute_quotes` for anything time-based. BTC rows were
> backfilled with it (original bytes untouched, checked line by line; pre-change
> copy in `~/market-lab-backups/`). ETH/SOL/XRP carry it only from 2026-09-23 on.
> Every opening-print and volatility figure below was redone on `minute_quotes`.

```json
{"series":"KXBTC15M","window":"26SEP071130","target":79054.02,
 "settled_yes":false,"trades":10000,"first":73,"last":0.1,
 "min":0.1,"max":85,"range":84.9,"q25":61,"q50":31,"q75":11,
 "path":[73,75,77,68,61,36,29,31,27,29,...],
 "minute_quotes":[[0.5,100],[48,49],[44,45],[28,29],[42,43],[71,72],...,[0,0.1]]}
```

That row shows the bug: `first` says it "opened at 73¢", but the real quote at
the end of minute 1 was 48/49¢ — a coin flip. Index 0 is often an empty book
(0.5/100), so the opening print used everywhere is the **minute-1 mid**.

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

That last column was computed from the legacy `first` field and is **wrong** —
see the correction above. On clock-aligned quotes (1,725 windows) BTC's
minute-1 price is wrong **40%** of the time [38%, 43%], much closer to the
alts than it looked.

## Daily report

An email lands at **07:00 Eastern** each morning (`com.dhruv.marketlab.report`) at
darup67@gmail.com. It reuses `~/flip-notifier/send-email.js` and the same
Keychain app password, so there is no second credential to manage.

```bash
python3 report.py --dry     # print it, send nothing
python3 report.py           # build and send
```

**Ordered by what a person wants at 7am**, not by what the code can compute:

```
BTC 15m recorder - OK - vol NORMAL 46c

Recorder OK - 96 new BTC windows overnight, none missed.

VOLATILITY
   Right now      NORMAL  46c
                  LOW under 28c / NORMAL 28-52c / HIGH over 52c
   Overnight      20% low, 52% normal, 27% high
   Band changes   28 in 24h  (it flickers - not a stable regime)

OVERNIGHT  (96 BTC windows)
   Settled YES        44 of 96  (45%)
   Opening price right 77 of 96  (80%)
   Average swing      41c
   Surprise           21:45Z  opened 99c, settled NO

CONTEXT  (all 202 BTC windows so far)
   The price at minute 1 is right 60% of the time.
   0% of windows are already decided by minute 1.
   Volatility does not predict the next window (correlation -0.05).
```

Four deliberate choices, after a first version that was six dense tables:

- **BTC only.** The other three series get one line, because their opening
  prints are coin flips and daily detail on them is noise.
- **Overnight is separated from all-time.** The first is news; the second is
  context that barely moves. Mixing them made neither readable.
- **Standing analysis is compressed to three sentences.** Re-sending a ten-row
  calibration curve every morning is how an email becomes one you stop opening.
- **Plain words.** "Opening price right 80%" rather than "open wrong 20%",
  no bare `corr`, no confidence-interval brackets. `analyze.py` is one command
  away when the full statistics are wanted.

The subject line carries the verdict and the current band —
`BTC 15m recorder - OK - vol NORMAL 46c` — so it is triageable from a lock
screen without opening it.

Operational detail (storage, growth) is one line at the bottom, and only
mentions the ceiling percentage once past 50%.

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

### Futures in the daily email

One block per contract, **sorted by how unusual its volatility is right now**, so
whatever is actually moving sits at the top rather than in file order:

```
FUTURES  (15-min bars, ~10 min delayed - research data, not signals)
   YM=F  Dow          53,440.00  +0.00% session
         vol 0.098%/bar  HIGH (87th pct of own history)
         VWAP  53,418.87   price 0.04% above
         ATR       53.93   volume 0.2x session avg
```

**Every figure is normalised, and that is not cosmetic.** NQ trades near 29,500
and CL near 91 — an absolute range or ATR says nothing comparable across them.
So volatility is percent-per-bar, VWAP distance is a percentage, and the regime
is each contract's **own percentile** rather than a shared threshold. Gold at
0.115%/bar is unremarkable; the S&P at that level would be violent.

| Field | What it is |
|---|---|
| session | change over the trailing 26 bars (~one 6.5h cash session) |
| vol | stdev of per-bar log returns, as a percent — scale-free |
| regime | percentile of that vol within this contract's own history (≤20 LOW, ≥80 HIGH) |
| VWAP | volume-weighted average over the session, and how far price sits from it |
| ATR | average true range, in the contract's own points |
| volume | last bar against the session mean |

**On the percentiles.** They are computed from rolling 26-bar windows, which
overlap almost entirely — 319 readings from 344 bars carry nowhere near 319
windows of information. The email reports the honest number instead:

```
Percentiles rest on ~13 independent sessions of history - treat
the bands as provisional until roughly two weeks have accumulated.
```

That note disappears on its own once ~40 independent sessions exist.

## Chronos-test reminder

The daily email carries a quiet countdown:

```
Chronos test: 203 of 1400 windows (1197 to go, ~12 days).
```

When the dataset reaches **1400 BTC windows**, a *separate* one-time email
arrives with the test design in it, so the one moment worth acting on is not
buried in a routine report. It fires **once** — a reminder repeated daily
becomes another line you skim past, which defeats the point of waiting.

**Why 1400.** A 50/50 split leaves ~700 test windows, which detects a move from
the ~60% minute-1 baseline to ~65% at about 75% power (originally stated as
83%→88% at 80%, on the flawed `first` field). Below ~1000 a
five-point difference is indistinguishable from noise, so an earlier test would
give a confident answer that means nothing. Lowering `KLAB_CHRONOS_N` still
works, but the email then says plainly that the holdout may be too small rather
than repeating a justification that no longer holds.

The test itself is written into that email — model, split, baseline, and the
prediction stated in advance that **Chronos loses to the opening print**. Fixed
now so it cannot be quietly revised after the result is known. (The test ran
2026-09-18 against the flawed `first` baseline; its "print 85.9%" row is a
mid-window price. See `results/ml-test-2026-09-18.md`.)

Re-arm by deleting `.chronos-notified`.

## Analysis

```bash
python3 analyze.py            # all series
python3 analyze.py KXBTC15M
```

Stdlib only, no dependencies. Every proportion is reported with a **95%
confidence interval**, because small samples lie confidently without one.

### What 1,725 BTC windows say (clock-aligned, 2026-09-23)

| | corrected | originally reported (legacy fields, 201 windows) |
|---|---|---|
| Base rate | 50.0% YES [48%, 52%] | 46.8% |
| Opening print (minute-1 mid) wrong | **40%** [38%, 43%] | 17% |
| ...when it opens 35–65¢ | 43% [40%, 45%] (82% of windows) | 30% |
| Decided by minute 1 (<10¢ or >90¢) | **0%** | 31% |
| Vol persistence, corr(prior hour, next window) | **−0.05** | +0.14 |

**The market is not priced at the open.** At minute 1 BTC is almost always
between 20¢ and 80¢, and the minute-1 mid is well calibrated (60–69¢ settles YES
64%, 30–39¢ settles YES 38%) — informative, but nowhere near "decided".

**Volatility does not persist.** On clock-aligned ranges the correlation is
zero, so the LOW/NORMAL/HIGH filter has no support at all. (The report's
regime bands still use the legacy `range` with 28/52¢ cut-offs calibrated on
it; they describe the trade tail, not the window.)

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

## Paper lab

`paper-lab/` tests fixed trading rules on BTC, ETH, MNQ, MES, MGC and MCL with
**simulated fills only**. It has no broker connection. It stores its own
15-minute bars (Coinbase, and the Yahoo micro contracts) in `paper-lab/data/`,
separate from `data/futures/`, and commits them hourly. See `paper-lab/README.md`.
The first backtest agrees with this lab's earlier finding: no measurable
next-hour edge on any of the six. Only MCL trend and breakout passed after costs,
in a window when oil trended.

## Event desk

`event-desk/` emails two briefings: every TradingView watchlist ticker (daily 08:50:
earnings ahead and news) and the top 10 bio/pharma names by event impact (weekdays
10:05, built from `~/market-iv-agent`'s snapshot), plus one email per S&P sector
(weekdays 10:10, S&P 500 + Nasdaq-100 + Dow). Jev reads the headlines once a
TypeSafe key exists. Read-only. See `event-desk/README.md`.
