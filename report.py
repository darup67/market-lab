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
    """
    A morning email, not a dashboard dump.

    Ordered by what a person actually wants at 7am: is it still running, what
    is BTC's volatility doing, what happened overnight. Standing analysis that
    barely moves day to day is compressed to two lines of context at the end —
    repeating a ten-row calibration table every morning is how an email becomes
    something you stop opening.

    Focused on BTC because that is the market with a real book; the other three
    get one line, since their opening prints are coin flips.
    """
    data = load()
    if not data:
        return "BTC 15m recorder — NO DATA", "data/ is empty. Is com.dhruv.kalshi15m loaded?"

    now      = datetime.now(timezone.utc)
    day_ago  = now - timedelta(hours=24)
    used     = sum(os.path.getsize(f) for f in glob.glob(os.path.join(HERE, "data", "*.jsonl")))
    total    = sum(len(v) for v in data.values())
    btc      = data.get("KXBTC15M", [])
    night    = [r for r in btc if (wtime(r) or day_ago) > day_ago]

    # ---------------------------------------------------------------- health
    problems = []
    for s_, rows in sorted(data.items()):
        newest = max((t for t in (wtime(r) for r in rows) if t), default=None)
        if newest and (now - newest).total_seconds() / 3600 > 2:
            problems.append(f"{s_} last captured {(now-newest).total_seconds()/3600:.1f}h ago — recorder may be stopped")
        miss, _ = gaps(rows)
        if miss:
            problems.append(f"{s_} is missing {len(miss)} window(s)")
    if used / MAX_BYTES >= 0.8:
        problems.append(f"storage {used/MAX_BYTES*100:.0f}% full — capture halts at 100%")

    L = []
    if problems:
        L.append("NEEDS ATTENTION")
        for p_ in problems:
            L.append(f"   - {p_}")
    else:
        L.append(f"Recorder OK - {len(night)} new BTC windows overnight, none missed.")
    L.append("")

    # ------------------------------------------------------------ volatility
    if len(btc) > VOL_W:
        reg = regime_series(btc)
        _, idx, bnd = reg[-1]
        L.append("VOLATILITY")
        L.append(f"   Right now      {bnd}  {idx:.0f}c")
        L.append(f"                  LOW under {VOL_LOW}c / NORMAL {VOL_LOW}-{VOL_HIGH}c / HIGH over {VOL_HIGH}c")
        night_reg = [(r, i, b) for r, i, b in reg if (wtime(r) or day_ago) > day_ago]
        if night_reg:
            cnt = {"LOW": 0, "NORMAL": 0, "HIGH": 0}
            for _, _, b in night_reg:
                cnt[b] += 1
            t = len(night_reg)
            L.append(f"   Overnight      {cnt['LOW']*100//t}% low, {cnt['NORMAL']*100//t}% normal, {cnt['HIGH']*100//t}% high")
            ch = sum(1 for a, b in zip(night_reg, night_reg[1:]) if a[2] != b[2])
            note = "  (it flickers - not a stable regime)" if ch > t * 0.15 else ""
            L.append(f"   Band changes   {ch} in 24h{note}")
        L.append("")

    # ------------------------------------------------------------- overnight
    if night:
        yes   = sum(1 for r in night if r["settled_yes"])
        right = sum(1 for r in night if (r["first"] > 50) == r["settled_yes"])
        rng   = st.mean([r["range"] for r in night])
        L.append(f"OVERNIGHT  ({len(night)} BTC windows)")
        L.append(f"   Settled YES        {yes} of {len(night)}  ({100*yes//len(night)}%)")
        L.append(f"   Opening price right {right} of {len(night)}  ({100*right//len(night)}%)")
        L.append(f"   Average swing      {rng:.0f}c")
        surprises = sorted((r for r in night if (r["first"] > 50) != r["settled_yes"]),
                           key=lambda r: -abs(r["first"] - 50))[:2]
        for r in surprises:
            when = (wtime(r).strftime("%H:%MZ") if wtime(r) else r.get("window"))
            L.append(f"   Surprise           {when}  opened {r['first']:.0f}c, settled "
                     f"{'YES' if r['settled_yes'] else 'NO'}")
        L.append("")

    # --------------------------------------------------------------- context
    L.append(f"CONTEXT  (all {len(btc)} BTC windows so far)")
    if btc:
        w = pct_wrong(btc)
        L.append(f"   The opening price is right {100-w:.0f}% of the time.")
        dec = sum(1 for r in btc if r["first"] < 10 or r["first"] > 90)
        L.append(f"   {100*dec//len(btc)}% of windows are already decided when trading starts.")
    if len(btc) > VOL_W + 10:
        pairs = [(st.mean([x["range"] for x in btc[i-VOL_W:i]]), btc[i]["range"])
                 for i in range(VOL_W, len(btc))]
        c = corr(pairs)
        if c is not None:
            L.append(f"   Volatility does not predict the next window (correlation {c:+.2f}).")
    others = [k for k in sorted(data) if k != "KXBTC15M"]
    if others:
        avg = st.mean([pct_wrong(data[k]) for k in others])
        L.append(f"   ETH, SOL and XRP opening prices are wrong {avg:.0f}% of the time - coin flips.")
    L.append("")

    # --------------------------------------------------------------- footer
    L.append(f"{total} windows recorded, {fmt_bytes(used)} used"
             f"{'' if used/MAX_BYTES < 0.5 else f' ({used/MAX_BYTES*100:.0f}% of ceiling)'}.")
    L.append("Figures are in-sample and exclude fees - not advice.")
    L.append("Detail: python3 analyze.py   |   python3 query.py \"SELECT ...\"")

    head = "NEEDS ATTENTION" if problems else "OK"
    subject = f"BTC 15m recorder - {head}"
    if btc and not problems:
        reg = regime_series(btc)
        subject += f" - vol {reg[-1][2]} {reg[-1][1]:.0f}c" if len(reg) else ""
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
