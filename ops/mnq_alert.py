#!/usr/bin/env python3
"""
MNQ price-touch alert. Fires the moment the /MNQ estimate touches a level, then disarms itself.

Price source: Hyperliquid's real-time Nasdaq-100 perp (xyz:XYZ100) shifted to MNQ terms by its median basis against Yahoo's CME MNQ=F bars
(CME data is ~10 minutes delayed, so it can only calibrate the basis, not trigger). The basis wobbles about +/-5 points, so a touch is
accurate to roughly that. Every 10 s it also reads the current minute's HIGH, so a wick between 2-second polls still counts.

Alert = an email to the user (shared ~/flip-notifier/email-ui.js layout, sent first) + sound x3 + a popup + a banner with the text in the
TITLE (this Mac hides notification bodies). No voice, per the user's setting.

    mnq_alert.py            run the watcher (launchd keeps it alive; it exits 0 after firing)
    mnq_alert.py --status   show config, last price and basis
    mnq_alert.py --test     fire the alert once as a test (does not disarm)

Config: ops/mnq-alert.json  {"level": 31400, "direction": "up", "armed": true}
"""
import json, os, subprocess, sys, time, urllib.parse, urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "mnq-alert.json")
LOG = os.path.join(HERE, "mnq-alert.log")
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
COIN = "xyz:XYZ100"
POLL_S, CANDLE_S, BASIS_S = 2, 10, 900


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def load():
    return json.load(open(CONFIG))


def save(c):
    json.dump(c, open(CONFIG, "w"), indent=2)


def http(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={**UA, **({"Content-Type": "application/json"} if body else {})})
    return json.load(urllib.request.urlopen(req, timeout=15))


def hl_mid():
    return float(http("https://api.hyperliquid.xyz/info", {"type": "allMids", "dex": "xyz"})[COIN])


def hl_candle_high():
    now = int(time.time())
    c = http("https://api.hyperliquid.xyz/info", {"type": "candleSnapshot", "req": {"coin": COIN, "interval": "1m", "startTime": (now - 150) * 1000, "endTime": (now + 60) * 1000}})
    return max(float(x["h"]) for x in c), min(float(x["l"]) for x in c)


def basis():
    """Median (MNQ=F close - Hyperliquid close) over the last ~30 hours of 5-minute bars."""
    now = int(time.time())
    y = http("https://query1.finance.yahoo.com/v8/finance/chart/MNQ%3DF?interval=5m&range=2d&includePrePost=true")["chart"]["result"][0]
    closes = {t: c for t, c in zip(y["timestamp"], y["indicators"]["quote"][0]["close"]) if c is not None}
    r = http("https://api.hyperliquid.xyz/info", {"type": "candleSnapshot", "req": {"coin": COIN, "interval": "5m", "startTime": (now - 36 * 3600) * 1000, "endTime": now * 1000}})
    hl = {c["t"] // 1000: float(c["c"]) for c in r}
    d = sorted(closes[t] - hl[t] for t in closes if t in hl and now - t < 30 * 3600)
    if len(d) < 20:
        raise RuntimeError("not enough overlapping bars for the basis")
    return d[len(d) // 2]


def touched(cfg, est, est_hi, est_lo):
    lvl = cfg["level"]
    return est_hi >= lvl if cfg.get("direction", "up") == "up" else est_lo <= lvl


def run(cmd):
    try:
        subprocess.run(cmd, capture_output=True, timeout=75)
    except Exception as e:
        log(f"alert channel {cmd[0]} failed: {e}")


NODE = "/usr/local/bin/node"


def email(title, detail, level, est, test=False):
    spec = {"kind": "PRICE ALERT", "title": title, "subtitle": f"/MNQ {datetime.now():%a %b %d, %-I:%M:%S %p} ET",
            "status": {"text": "TEST" if test else "TOUCHED", "tone": "info" if test else "good"},
            "sections": [{"blocks": [
                {"type": "kpis", "items": [{"label": "Level", "value": f"{level:,}"}, {"label": "Estimate", "value": f"{est:,.0f}", "sub": "MNQ terms"}]},
                {"type": "para", "text": detail},
                {"type": "callout", "tone": "warn", "text": "Informational only. This alert places no orders. The estimate comes from a real-time Nasdaq-100 perpetual plus its basis to MNQ and can differ from the CME price by about 5 points."}]}],
            "footer": "Sent by the MNQ price alert (~/market-lab/ops/mnq_alert.py)."}
    try:
        r = subprocess.run([NODE, os.path.expanduser("~/flip-notifier/email-ui.js"), "send", ("[TEST] " if test else "") + title],
                           input=json.dumps(spec), capture_output=True, text=True, timeout=75)
        log(f"email {'sent' if r.returncode == 0 else 'FAILED: ' + (r.stderr or r.stdout)[-160:]}")
    except Exception as e:
        log(f"email failed: {e}")


def alert(title, detail, level=0, est=0.0, test=False):
    """Every channel runs to completion: launchd kills a job's leftover children when it exits."""
    email(title, detail, level, est, test)
    run(["/usr/bin/osascript", "-e", f'display notification {json.dumps(detail)} with title {json.dumps(title)} sound name "Glass"'])
    for _ in range(3):
        run(["/usr/bin/afplay", "/System/Library/Sounds/Glass.aiff"])
    run(["/usr/bin/osascript", "-e",
         f'display alert {json.dumps(title)} message {json.dumps(detail)} as critical buttons {{"OK"}} default button "OK" giving up after {20 if test else 90}'])


def main():
    if "--status" in sys.argv:
        c = load()
        print(json.dumps(c, indent=2))
        try:
            b = basis()
            print(f"basis {b:+.1f}; live MNQ-equivalent {hl_mid() + b:,.1f}")
        except Exception as e:
            print("price check failed:", e)
        return
    cfg = load()
    if "--test" in sys.argv:
        alert(f"MNQ alert for {cfg['level']:,} is armed and working", "This is only a test. The real alert fires when MNQ touches the level.", cfg["level"], cfg["level"], test=True)
        return
    if not cfg.get("armed"):
        log("not armed; exiting")
        return
    log(f"armed: MNQ {cfg.get('direction', 'up')} touch of {cfg['level']:,}")
    b, b_at, c_at, hb_at, errs = None, 0, 0, 0, 0
    est_hi = est_lo = None
    while True:
        try:
            now = time.time()
            if b is None or now - b_at > BASIS_S:
                try:
                    b, b_at = basis(), now
                    log(f"basis {b:+.1f}")
                except Exception as e:
                    if b is None:
                        raise
                    log(f"basis refresh failed, keeping {b:+.1f}: {e}")
                    b_at = now
            est = hl_mid() + b
            est_hi = est_lo = est
            if now - c_at > CANDLE_S:
                hi, lo = hl_candle_high()
                est_hi, est_lo = max(est, hi + b), min(est, lo + b)
                c_at = now
            errs = 0
            if touched(cfg, est, est_hi, est_lo):
                cfg = load()
                if not cfg.get("armed"):
                    return
                px = est_hi if cfg.get("direction", "up") == "up" else est_lo
                cfg.update(armed=False, triggered_at=datetime.now().isoformat(timespec="seconds"), triggered_price=round(px, 1))
                save(cfg)
                title = f"MNQ TOUCHED {cfg['level']:,} (now ~{est:,.0f})"
                log(f"TRIGGERED: {title}")
                alert(title, f"/MNQ reached {cfg['level']:,} at {datetime.now():%-I:%M:%S %p}. Estimate {px:,.1f} (real-time Nasdaq-100 perp + basis {b:+.0f}; accurate to about 5 points).", cfg["level"], px)
                return
            if now - hb_at > 300:
                log(f"watching: est {est:,.1f} (to level {cfg['level'] - est:+,.1f})")
                hb_at = now
        except Exception as e:
            errs += 1
            if errs in (1, 30, 150):
                log(f"feed error x{errs}: {e}")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
