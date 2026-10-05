"""Long hourly history from Coinbase spot (public, US-reachable) as the price proxy for perps; HYPE comes from Hyperliquid (short history).
Perp funding: real Hyperliquid funding where it exists, else a 0.0000125/h baseline (long pays) so carry is never ignored."""
import json, os, time, urllib.request
import feeds
HERE = os.path.dirname(os.path.abspath(__file__))
CB = "https://api.exchange.coinbase.com/products/%s-USD/candles?granularity=3600&start=%s&end=%s"
BASE_FUNDING = 0.0000125

def iso(t): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

def coinbase(coin, years=3.0):
    p = os.path.join(HERE, "data", f"{coin}-cb-1h.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 86400:
        return json.load(open(p))
    end, start, out = int(time.time()) // 3600 * 3600, int(time.time() - years * 365 * 86400), {}
    t = start
    while t < end:
        e = min(t + 300 * 3600, end)
        for k in range(6):
            try:
                r = json.load(urllib.request.urlopen(urllib.request.Request(CB % (coin, iso(t), iso(e)), headers={"User-Agent": "perp-lab"}), timeout=30)); break
            except Exception:
                time.sleep(2 * (k + 1)); r = []
        for c in r:
            out[c[0] * 1000] = {"t": c[0] * 1000, "o": c[3], "h": c[2], "l": c[1], "c": c[4], "v": c[5]}
        t = e; time.sleep(0.12)
    cs = [out[k] for k in sorted(out)]
    json.dump(cs, open(p, "w")); return cs

def funding_map(coin, start_ms):
    try:
        return feeds.funding(coin, start_ms)
    except Exception:
        return {}

def get(coin):
    if coin == "HYPE":
        cs, fd = feeds.load("HYPE", 5000, "1h", refresh=False)
        return cs, fd
    cs = coinbase(coin)
    p = os.path.join(HERE, "data", f"{coin}-fund-long.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 86400:
        fd = {int(k): v for k, v in json.load(open(p)).items()}
    else:
        fd = funding_map(coin, cs[0]["t"]); json.dump({str(k): v for k, v in fd.items()}, open(p, "w"))
    return cs, fd
