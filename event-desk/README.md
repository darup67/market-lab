# event-desk

Earnings and news briefings by email, with **Jev** (TypeSafe's System One
model) reading every headline. Read-only: no orders and no recommendations.

| Briefing | Universe | When | launchd |
|---|---|---|---|
| **Watchlist** | every ticker on the TradingView watchlist: stocks, crypto, futures | daily 08:50 | `com.dhruv.eventdesk.watchlist` |
| **Bio/pharma** (the one consolidated health-care email) | **2–5 act-on bull call spreads**, then the **top 10** names by event impact, then the IV agent's full report | weekdays 10:05 | `com.dhruv.eventdesk.bio` |

Built 2026-09-24. It lives in the market-lab repo and commits and pushes
`event-desk/data/` after each emailed run (staging only that folder;
`EVENTDESK_AUTOCOMMIT=0` turns this off).

## Inputs

- **Watchlist:** `~/flip-notifier/expected-symbols.json`. The `flip-watchlist-sync` task
  rewrites it from the live TradingView watchlist each weekday at 08:33, so it holds the
  watchlist's first 25 symbols (the Scanner's slot count). TV symbols map to Yahoo:
  `BTCUSD`→`BTC-USD`, `BNBUSDT`→`BNB-USD`, `MNQ1!`→`NQ=F`, `MGCV2026`→`GC=F`
  (overrides in `config.json`). Company names for Jev are in `config.watchlist.names`.
- **Headlines:** Yahoo Finance per-symbol RSS. Yahoo answers a full Chrome
  user agent with 429, so Yahoo requests use `Mozilla/5.0 (Macintosh)`.
- **Earnings dates:** Nasdaq's public calendar, one call per day ahead, cached daily.
- **Health care:** `~/biotech-iv-agent/data`: the newest snapshot (explode score, IV30,
  implied move, bias, earnings) and the RTTNews FDA/trial catalyst cache. The desk reads
  these and never writes to the IV agent.

## Consolidated bio/pharma email

`~/biotech-iv-agent` runs in `email_mode: "handoff"` and sends nothing itself.
At 10:05 this desk waits up to 25 minutes for that agent's **09:45 open-screen**
handoff (`data/handoff/handoff.json`). If that run never arrives, it uses any run
from today; failing that, it says the scan is missing. The email then contains:

1. **Bull call spreads to act on (2–5):** the IV agent's fixed rule. Bull bias,
   explode score ≥ 60, both legs liquid, up to 5 by bias, $2,000 each. The rule's
   numbers are read from the IV agent's `config.json`. Fewer than 2 passing is
   reported as-is, never padded. Candidates that failed are listed with the reason.
2. **Top 10 by event impact** (below).
3. **The IV agent's full report**, embedded unchanged.

## Bio/pharma ranking (code-owned)

`impact = 50% explode score + 30% event proximity + 20% news intensity`

- **Proximity:** a dated event scores 1 today, falling to 0 at 30 days. A window
  ("Q3 2026") scores at the midpoint of its remaining range, at 60% weight.
- **News:** Jev-judged material headlines (3 or more = full). Unjudged headlines count
  at half weight.
- Only the 40 highest names on score and proximity are checked for news.

Impact is the *size* of a pending event, never its direction.

## Jev

For each headline, Jev answers four questions: is it really **about** this ticker,
what **kind of event** it reports (earnings, FDA, trial data, deal, analyst…), is it
good or bad for the **price**, and is it **material**, meaning new information that
could move the price today. Code does the counting: material headlines per ticker,
the up/down lean, and the main event type. Jev only reads words.

**No key yet.** Everything runs without one: headlines appear unjudged, with a
banner. Every headline is stored in `data/headlines.jsonl`, so once a key exists the
backlog is judged on the next run. To add the key, in your own terminal:

```bash
python3 ~/jev-client/jev.py --set-key      # hidden prompt → Keychain (typesafe-jev) → test call
~/.venvs/market-ml/bin/python ~/market-lab/event-desk/desk.py jev-status
```

Jev's labels are unvalidated. `data/judged.jsonl` keeps every raw answer, so the
direction and material calls can later be scored against price moves.

## Commands

```bash
cd ~/market-lab/event-desk; PY=~/.venvs/market-ml/bin/python
$PY desk.py watchlist --dry     # preview-watchlist.html, no email
$PY desk.py biopharma --dry     # preview-biopharma.html, no email
$PY desk.py jev-status
$PY desk.py --test-email
```

Email goes to darup67@gmail.com through Gmail SMTP, using the same Keychain app
password as flip-notifier and the IV agent.
