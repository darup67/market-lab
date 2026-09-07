#!/usr/bin/env python3
"""
Daily email report on the recorder.

Leads with whether anything is wrong, because the failure that matters here is
silent: Kalshi keeps ~2 days of settled markets, so a recorder that stops loses
history permanently rather than falling behind. Gaps are therefore checked
first and stated plainly.

    python3 report.py            build and send
    python3 report.py --dry      print it, send nothing
"""
import json, glob, os, sys, subprocess, statistics as st
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HERE     = os.path.dirname(os.path.abspath(__file__))
SEND     = os.path.expanduser("~/flip-notifier/send-email.js")
KEYCHAIN = ["/usr/bin/security", "find-generic-password",
            "-a", "darup67@gmail.com", "-s", "flip-notifier-gmail", "-w"]
MAX_BYTES = int(os.environ.get("KLAB_MAX_BYTES", 5 * 1024**3))
DRY = "--dry" in sys.argv

def fmt_bytes(b):
    for unit, size in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if b >= size:
            return f"{b/size:.1f} {unit}" if unit != "GB" else f"{b/size:.2f} GB"
    return f"{b} B"

def load():
    out = defaultdict(list)
    for f in sorted(glob.glob(os.path.join(HERE, "data", "*.jsonl"))):
        for line in open(f):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            out[r["series"]].append(r)
    for s in out:
        out[s].sort(key=lambda r: r.get("window") or "")
    return out

def wtime(r):
    """
    When a window actually closed, from its own close_time.

    Deliberately NOT parsed from the window label: '26SEP071130' is Eastern,
    not UTC — that window's close_time is 15:30Z — so decoding the label as UTC
    reported every series four hours stale and cried wolf about a recorder that
    was running fine. close_time is explicit ISO UTC and needs no decoding.
    """
    t = r.get("close_time")
    if not t:
        return None
    try:
        return datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except Exception:
        return None

def gaps(rows):
    """Missing 15-minute slots between the first and last window we hold."""
    ts = sorted(t for t in (wtime(r) for r in rows) if t)
    if len(ts) < 2:
        return [], 0
    missing, expected = [], 0
    cur = ts[0]
    have = set(ts)
    while cur <= ts[-1]:
        expected += 1
        if cur not in have:
            missing.append(cur)
        cur += timedelta(minutes=15)
    return missing, expected

def pct_wrong(rows):
    if not rows:
        return None
    return 100.0 * sum(1 for r in rows if (r["first"] > 50) != r["settled_yes"]) / len(rows)

def build():
    data = load()
    if not data:
        return "Kalshi recorder — NO DATA", "data/ is empty. Is com.dhruv.kalshi15m loaded?"

    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    used = sum(os.path.getsize(f) for f in glob.glob(os.path.join(HERE, "data", "*.jsonl")))
    total = sum(len(v) for v in data.values())

    L, problems = [], []

    # --- health first ---
    for s in sorted(data):
        rows = data[s]
        miss, expected = gaps(rows)
        recent = [r for r in rows if (wtime(r) or day_ago) > day_ago]
        newest = max((t for t in (wtime(r) for r in rows) if t), default=None)
        stale_h = (now - newest).total_seconds() / 3600 if newest else None
        if stale_h is not None and stale_h > 2:
            problems.append(f"{s}: newest window is {stale_h:.1f}h old — recorder may be stopped")
        if miss:
            problems.append(f"{s}: {len(miss)} missing window(s) of {expected} in range")
        if not recent:
            problems.append(f"{s}: nothing captured in 24h")

    pct_full = used / MAX_BYTES * 100
    if pct_full >= 80:
        problems.append(f"storage at {pct_full:.0f}% of ceiling — capture halts at 100%")

    L.append("STATUS: " + ("OK — capturing cleanly" if not problems else "ATTENTION"))
    for p in problems:
        L.append(f"  ! {p}")
    L.append("")

    # --- growth ---
    L.append("CAPTURE")
    added24 = 0
    for s in sorted(data):
        rows = data[s]
        recent = [r for r in rows if (wtime(r) or day_ago) > day_ago]
        added24 += len(recent)
        span = f"{rows[0].get('window')} -> {rows[-1].get('window')}"
        L.append(f"  {s:<10} {len(rows):>5} total  +{len(recent):>3} in 24h   {span}")
    L.append(f"  {'':<10} {total:>5} windows (~{total/96:.1f} series-days), +{added24} yesterday")
    L.append(f"  storage {fmt_bytes(used)} of {fmt_bytes(MAX_BYTES)} ({pct_full:.3f}%)")
    if total and used:
        per_day = (used / total) * 96 * len(data)
        L.append(f"  growing ~{fmt_bytes(per_day)}/day -> ceiling in ~{(MAX_BYTES-used)/per_day/365:.0f} years")
    L.append("")

    # --- what the data says, refreshed ---
    L.append("READINGS  (whole dataset, in-sample)")
    L.append(f"  {'series':<10}{'n':>5}{'avg range':>11}{'YES%':>7}{'open wrong':>12}")
    for s in sorted(data):
        rows = data[s]
        L.append(f"  {s:<10}{len(rows):>5}{st.mean([r['range'] for r in rows]):>10.1f}c"
                 f"{100*sum(1 for r in rows if r['settled_yes'])/len(rows):>6.1f}%"
                 f"{pct_wrong(rows):>11.1f}%")

    btc = data.get("KXBTC15M", [])
    if len(btc) > 8:
        W = 4
        pairs = [(sum(r["range"] for r in btc[i-W:i])/W, btc[i]["range"]) for i in range(W, len(btc))]
        xs = [a for a, _ in pairs]; ys = [b for _, b in pairs]
        mx, my = st.mean(xs), st.mean(ys)
        den = (sum((x-mx)**2 for x in xs) * sum((y-my)**2 for y in ys)) ** 0.5
        corr = (sum((x-mx)*(y-my) for x, y in pairs) / den) if den else 0
        L.append("")
        L.append(f"  BTC vol persistence corr(prior hour, next window) = {corr:+.2f}  n={len(pairs)}")
        L.append("  near zero means a calm hour does not predict a calm next window")

    L.append("")
    L.append("Every figure above is in-sample and excludes fees. Not advice.")
    L.append("  python3 analyze.py | python3 query.py \"SELECT ...\"")

    subject = ("Kalshi recorder — OK" if not problems
               else f"Kalshi recorder — ATTENTION ({len(problems)})") + f" · {total} windows"
    return subject, "\n".join(L)

def main():
    subject, body = build()
    if DRY:
        print(subject); print("-" * len(subject)); print(body); return
    pw = os.environ.get("FLIP_GMAIL_APP_PASSWORD")
    if not pw:
        try:
            pw = subprocess.run(KEYCHAIN, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            pw = ""
    if not pw:
        print("no Gmail app password — cannot send"); sys.exit(1)
    r = subprocess.run(["node", SEND, subject, body], capture_output=True, text=True,
                       timeout=60, env={**os.environ, "FLIP_GMAIL_APP_PASSWORD": pw})
    if r.returncode != 0:
        print("send failed:", (r.stderr or r.stdout).strip()[:300]); sys.exit(1)
    print(f"sent: {subject}")

if __name__ == "__main__":
    main()
