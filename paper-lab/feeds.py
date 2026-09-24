"""15-minute bars, no keys.

- Coinbase Exchange: BTC-USD, ETH-USD. Real time; pages back as far as needed,
  300 candles per call. Rows are [start, low, high, open, close, volume].
- Yahoo chart API: MNQ=F, MES=F, MGC=F, MCL=F. 15m history goes back ~60 days.
  CME quotes here run ~10 min delayed (see ~/market-lab/README.md), which is
  acceptable for paper fills at the next bar's open but is not a live feed.
  The =F series is the front month without back-adjustment, so contract rolls
  appear as price gaps; lab.py counts them rather than hiding them.

Only closed bars are ever stored: a bar whose start + 15 min is in the future
is still forming, and using it would leak the future into the signal.
"""
import json, os, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BARS = os.path.join(HERE, "data", "bars")
UA = {"User-Agent": "Mozilla/5.0 (paper-lab)", "Accept": "application/json"}


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def coinbase(product, start, end, step=900):
    out, s = {}, start
    while s < end:
        e = min(end, s + 300 * step)
        iso = lambda x: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(x))
        for t, lo, hi, o, c, v in get(f"https://api.exchange.coinbase.com/products/{product}/candles"
                                      f"?granularity={step}&start={iso(s)}&end={iso(e)}") or []:
            out[int(t)] = {"t": int(t), "o": o, "h": hi, "l": lo, "c": c, "v": v}
        s = e
        time.sleep(0.4)
    return [out[k] for k in sorted(out)]


def yahoo(symbol, rng="60d"):
    r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=15m&range={rng}")
    r = r["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(r.get("timestamp") or []):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue   # periods that never traded come back as nulls
        out.append({"t": int(t), "o": o, "h": h, "l": l, "c": c, "v": q["volume"][i] or 0})
    return out


def _path(name):
    return os.path.join(BARS, f"{name}.jsonl")


def load(name):
    try:
        with open(_path(name)) as f:
            return [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []


def update(name, inst, step=900, backfill_days=60):
    """Merge fresh closed bars into data/bars/<name>.jsonl. Returns (total, added)."""
    os.makedirs(BARS, exist_ok=True)
    have = {b["t"]: b for b in load(name)}
    now = int(time.time())
    if inst["source"] == "coinbase":
        start = (max(have) - 4 * step) if have else now - backfill_days * 86400
        fresh = coinbase(inst["symbol"], start, now, step)
    else:
        fresh = yahoo(inst["symbol"], "5d" if have else f"{backfill_days}d")
    added = 0
    for b in fresh:
        if b["t"] + step > now:
            continue   # still forming
        if b["t"] not in have:
            added += 1
        have[b["t"]] = b   # a re-sent closed bar replaces ours (late prints)
    rows = [have[k] for k in sorted(have)]
    tmp = _path(name) + ".tmp"
    with open(tmp, "w") as f:
        for b in rows:
            f.write(json.dumps(b) + "\n")
    os.replace(tmp, _path(name))
    return len(rows), added
