"""Inputs for the event desk. No keys.

- TradingView watchlist: ~/flip-notifier/expected-symbols.json (TV symbols),
  mapped to Yahoo symbols: BTCUSD -> BTC-USD, BNBUSDT -> BNB-USD, MNQ1! -> NQ=F.
- Headlines: Yahoo Finance per-symbol RSS. Feeds are ticker-tagged but loose
  (a PLTR feed can lead with an Anthropic story), which is why Jev is asked
  whether each headline is really about the ticker.
- Earnings dates: Nasdaq's public earnings calendar, one call per day ahead,
  cached for the day in data/earnings.json.
- Health care: ~/biotech-iv-agent's latest snapshot CSV (explode score, IV,
  bias, earnings) and its catalyst cache (RTTNews FDA and trial calendars).
"""
import csv, datetime as dt, email.utils, json, os, re, time, urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36", "Accept": "*/*"}


# Yahoo's RSS answers a full Chrome user agent with 429 (bot filter) but serves
# the short form; Nasdaq's API wants the full one. Measured 2026-09-24.
YAHOO_UA = {"User-Agent": "Mozilla/5.0 (Macintosh)", "Accept": "*/*"}


def get(url, timeout=20, tries=3, headers=UA):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


# ---------------------------------------------------------------- watchlist
def kind(tv):
    if tv.endswith("!") or re.fullmatch(r"[A-Z]{1,4}[FGHJKMNQUVXZ]20\d\d", tv):
        return "future"
    if re.fullmatch(r"[A-Z0-9]{2,10}(USD|USDT|USDC)", tv):
        return "crypto"
    return "equity"


def yahoo_symbol(tv, overrides):
    if tv in overrides:
        return overrides[tv]
    if kind(tv) == "crypto":
        return re.sub(r"(USDT|USDC|USD)$", "", tv) + "-USD"
    return tv


def watchlist(cfg):
    w = cfg["watchlist"]
    with open(os.path.expanduser(w["symbols_file"])) as f:
        doc = json.load(f)
    out = []
    for tv in doc["tickers"]:
        y = yahoo_symbol(tv, w["yahoo_overrides"])
        base = y.replace("-USD", "")
        out.append({"tv": tv, "yahoo": y, "kind": kind(tv), "name": w["names"].get(base) or w["names"].get(y) or base})
    return out, doc.get("updated")


# ---------------------------------------------------------------- headlines
def headlines(yahoo_sym, lookback_hours):
    """Recent Yahoo headlines for one symbol, newest first. [] on any failure."""
    try:
        root = ET.fromstring(get(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={yahoo_sym}&region=US&lang=en-US",
                                 headers=YAHOO_UA))
    except Exception:
        return []
    cutoff = time.time() - lookback_hours * 3600
    out = []
    for it in root.findall(".//item"):
        title, link = (it.findtext("title") or "").strip(), (it.findtext("link") or "").strip()
        try:
            pub = email.utils.parsedate_to_datetime(it.findtext("pubDate")).timestamp()
        except Exception:
            continue
        if title and link and pub >= cutoff:
            out.append({"title": title, "link": link, "pub": pub})
    out.sort(key=lambda h: -h["pub"])
    return out


# ---------------------------------------------------------------- earnings
def earnings(days, log=print):
    """{SYMBOL: {"date": iso, "time": pre/after/unknown, "name": ...}} for the next `days` days."""
    path = os.path.join(DATA, "earnings.json")
    today = dt.date.today()
    cache = {}
    try:
        with open(path) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        pass
    days_cached = cache.get("days", {})
    fresh = cache.get("fetched_on") == today.isoformat()
    changed = False
    for i in range(days + 1):
        d = (today + dt.timedelta(days=i)).isoformat()
        if fresh and d in days_cached:
            continue
        try:
            j = json.loads(get(f"https://api.nasdaq.com/api/calendar/earnings?date={d}"))
            days_cached[d] = [{"symbol": r["symbol"], "name": r.get("name", ""), "time": r.get("time", "")}
                              for r in ((j.get("data") or {}).get("rows") or [])]
            changed = True
            time.sleep(0.3)
        except Exception as e:
            log(f"earnings {d}: fetch failed {e!r}")
    if changed:
        os.makedirs(DATA, exist_ok=True)
        keep = {k: v for k, v in days_cached.items() if k >= today.isoformat()}
        with open(path, "w") as f:
            json.dump({"fetched_on": today.isoformat(), "days": keep}, f)
        days_cached = keep
    out = {}
    for d in sorted(days_cached):
        if d < today.isoformat() or d > (today + dt.timedelta(days=days)).isoformat():
            continue
        for r in days_cached[d]:
            when = {"time-pre-market": "pre-market", "time-after-hours": "after close"}.get(r["time"], "time n/a")
            out.setdefault(r["symbol"], {"date": d, "time": when, "name": r["name"]})
    return out


# ---------------------------------------------------------------- health care
def iv_snapshot(snapshot_dir):
    """Rows of the newest biotech-iv-agent snapshot, and its date."""
    d = os.path.expanduser(snapshot_dir)
    files = sorted(f for f in os.listdir(d) if re.fullmatch(r"snapshot-\d{4}-\d{2}-\d{2}\.csv", f))
    if not files:
        return [], None
    with open(os.path.join(d, files[-1])) as f:
        rows = list(csv.DictReader(f))
    return rows, files[-1][9:19]


def catalysts(snapshot_dir):
    """{TICKER: [events with start/end dates]} from the IV agent's catalyst cache."""
    try:
        with open(os.path.join(os.path.expanduser(snapshot_dir), "catalysts.json")) as f:
            events = json.load(f)["events"]
    except (OSError, ValueError, KeyError):
        return {}
    by = {}
    for e in events:
        if e.get("start"):
            by.setdefault(e["ticker"], []).append(e)
    return by
