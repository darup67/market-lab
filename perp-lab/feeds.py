"""Public Hyperliquid perp data (candles + hourly funding) cached to data/. No keys, no account, read-only."""
import json, os, time, urllib.error, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.hyperliquid.xyz/info"
HOUR = 3600 * 1000

def post(body, tries=6):
    for k in range(tries):
        try:
            return _post(body)
        except urllib.error.HTTPError as e:
            if e.code != 429 or k == tries - 1: raise
            time.sleep(3 * (k + 1))

def _post(body):
    req = urllib.request.Request(API, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))

def candles(coin, n=5000, interval="1h"):
    end = int(time.time() * 1000)
    r = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": end - n * HOUR, "endTime": end}})
    return [{"t": c["t"], "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])} for c in r]

def funding(coin, start_ms):
    out, t = {}, start_ms
    while True:
        r = post({"type": "fundingHistory", "coin": coin, "startTime": t})
        if not r:
            break
        for x in r:
            out[(x["time"] // HOUR) * HOUR] = float(x["fundingRate"])
        nt = r[-1]["time"] + 1
        if nt <= t or len(r) < 500:
            break
        t = nt
    return out

def load(coin, n=5000, interval="1h", refresh=True):
    p = os.path.join(HERE, "data", f"{coin}-{interval}.json")
    if refresh or not os.path.exists(p):
        cs = candles(coin, n, interval)
        fd = funding(coin, cs[0]["t"])
        json.dump({"candles": cs, "funding": {str(k): v for k, v in fd.items()}}, open(p, "w"))
    d = json.load(open(p))
    return d["candles"], {int(k): v for k, v in d["funding"].items()}
