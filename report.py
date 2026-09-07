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


# --- regime, computed from our own data so the email never disagrees with the
# --- dataset it ships. Same bands and 4-window smoothing the notifier uses.
VOL_W, VOL_LOW, VOL_HIGH = 4, 28, 52

def band(v):
    return "LOW" if v < VOL_LOW else "HIGH" if v > VOL_HIGH else "NORMAL"

def regime_series(rows):
    """Rolling 4-window mean range, oldest first, paired with its window."""
    out = []
    for i in range(VOL_W, len(rows) + 1):
        idx = st.mean([r["range"] for r in rows[i-VOL_W:i]])
        out.append((rows[i-1], idx, band(idx)))
    return out

def corr(pairs):
    if len(pairs) < 10:
        return None
    xs = [a for a, _ in pairs]; ys = [b for _, b in pairs]
    mx, my = st.mean(xs), st.mean(ys)
    den = (sum((x-mx)**2 for x in xs) * sum((y-my)**2 for y in ys)) ** 0.5
    return None if den == 0 else sum((x-mx)*(y-my) for x, y in pairs) / den

def wilson(k, n):
    if n == 0:
        return (0.0, 1.0)
    p, z = k / n, 1.96
    d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    h = z * ((p*(1-p)/n + z*z/(4*n*n)) ** 0.5) / d
    return (max(0, c-h), min(1, c+h))

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

    # ---------------------------------------------------------- REGIME
    if len(btc) > VOL_W:
        reg = regime_series(btc)
        cur_row, cur_idx, cur_band = reg[-1]
        L.append("")
        L.append("BTC VOLATILITY REGIME")
        L.append(f"  now        {cur_band}  {cur_idx:.1f}c   (LOW <{VOL_LOW}c · NORMAL {VOL_LOW}-{VOL_HIGH}c · HIGH >{VOL_HIGH}c)")

        last8 = reg[-8:]
        L.append("  last 2h    " + " ".join(f"{i:.0f}" for _, i, _ in last8) + "c")
        L.append("             " + " ".join({"LOW":" L","NORMAL":" ~","HIGH":" H"}[b] for _, _, b in last8))

        day = [(r, i, b) for r, i, b in reg if (wtime(r) or day_ago) > day_ago]
        if day:
            cnt = {"LOW": 0, "NORMAL": 0, "HIGH": 0}
            for _, _, b in day:
                cnt[b] += 1
            tot = len(day)
            L.append("  24h split  " + "  ".join(f"{k} {100*v/tot:.0f}%" for k, v in cnt.items()))
            trans = sum(1 for a, b in zip(day, day[1:]) if a[2] != b[2])
            L.append(f"             {trans} band change(s) in 24h")

    # ------------------------------------------------------- INDICATORS
    if len(btc) >= 40:
        L.append("")
        L.append("BTC INDICATORS")
        cal = defaultdict(list)
        for r in btc:
            cal[min(9, int(r["first"] // 10))].append(r["settled_yes"])
        L.append("  calibration — opening print vs outcome")
        for k in sorted(cal):
            g = cal[k]
            lo, hi = wilson(sum(g), len(g))
            bar = "#" * round(sum(g) / len(g) * 20)
            L.append(f"    {k*10:>3}-{k*10+9:<3}{len(g):>4}  {100*sum(g)/len(g):>3.0f}% YES "
                     f"[{lo*100:>3.0f},{hi*100:>3.0f}]  {bar}")

        rev = [r for r in btc if (r["first"] > 50) != r["settled_yes"]]
        big = sorted(btc, key=lambda r: -r["range"])[:3]
        L.append(f"  reversals        {len(rev)}/{len(btc)} = {100*len(rev)/len(btc):.0f}% of windows")
        L.append("  widest windows   " + ", ".join(
            f"{r['window']} {r['first']:.0f}->{r['last']:.0f}c" for r in big))

    # --------------------------------------------------------- FINDINGS
    L.append("")
    L.append("FINDINGS  (recomputed each morning — these age with the data)")
    if len(btc) > VOL_W + 10:
        pairs = [(st.mean([x["range"] for x in btc[i-VOL_W:i]]), btc[i]["range"])
                 for i in range(VOL_W, len(btc))]
        c = corr(pairs)
        L.append(f"  1. Vol persistence corr = {c:+.2f} (n={len(pairs)}).")
        by = defaultdict(list)
        for prior, nxt in pairs:
            by[band(prior)].append(nxt)
        for k in ("LOW", "NORMAL", "HIGH"):
            if by[k]:
                L.append(f"       after {k:<6} next range {st.mean(by[k]):5.1f}c  n={len(by[k])}")
        L.append("     A vol filter needs this gap to be wide. It is not.")
    liquid = sorted(data, key=lambda s: pct_wrong(data[s]))
    if len(liquid) > 1:
        L.append(f"  2. Opening print is informative only on {liquid[0]} "
                 f"({pct_wrong(data[liquid[0]]):.0f}% wrong) vs "
                 f"{pct_wrong(data[liquid[-1]]):.0f}% on {liquid[-1]} — a coin flip.")
    if len(btc) >= 40:
        decided = sum(1 for r in btc if r["first"] < 10 or r["first"] > 90)
        L.append(f"  3. {100*decided/len(btc):.0f}% of BTC windows are effectively decided at the open.")

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
