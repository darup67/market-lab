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
import json, glob, os, sys, subprocess, time, statistics as st
from datetime import datetime, timezone, timedelta
from collections import defaultdict

HERE     = os.path.dirname(os.path.abspath(__file__))
SEND     = os.path.expanduser("~/flip-notifier/send-email.js")
KEYCHAIN = ["/usr/bin/security", "find-generic-password",
            "-a", "darup67@gmail.com", "-s", "flip-notifier-gmail", "-w"]
MAX_BYTES = int(os.environ.get("KLAB_MAX_BYTES", 5 * 1024**3))

# When there is enough data to test a forecasting model against the opening
# print. 1400 BTC windows gives a 700-window holdout on a 50/50 split, which
# detects 83% -> 88% at roughly 80% power. Below ~1000 a five-point difference
# is indistinguishable from noise, and a test that cannot separate the two
# would produce a confident answer that means nothing.
CHRONOS_THRESHOLD = int(os.environ.get("KLAB_CHRONOS_N", 1400))
CHRONOS_FLAG = os.path.join(HERE, ".chronos-notified")
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


# --- futures indicators -----------------------------------------------------
# Every figure here is normalised. NQ trades near 29,500 and CL near 91, so an
# absolute range says nothing comparable; percentages and each contract's own
# percentile do.
FUT_SESSION = 26          # bars ~= one 6.5h US cash session at 15m

def load_futures():
    out = {}
    for f in sorted(glob.glob(os.path.join(HERE, "data", "futures", "*.jsonl"))):
        rows = []
        for line in open(f):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            # Skip Yahoo live-quote snapshots (unaligned time, o=h=l=c, v=0)
            # stored before 2026-09-18; they are not bars.
            if r.get("t", 0) % 900 == 0:
                rows.append(r)
        if rows:
            rows.sort(key=lambda r: r["t"])
            out[rows[0]["sym"]] = rows
    return out

def _vwap(bars):
    num = den = 0.0
    for b in bars:
        v = b.get("v") or 0
        tp = (b["h"] + b["l"] + b["c"]) / 3
        num += tp * v; den += v
    return num / den if den else None

def _realised_vol(bars):
    """Stdev of per-bar log returns, as a percent. Scale-free by construction."""
    import math
    rets = []
    for a, b in zip(bars, bars[1:]):
        if a["c"] > 0 and b["c"] > 0:
            rets.append(math.log(b["c"] / a["c"]))
    return st.pstdev(rets) * 100 if len(rets) > 2 else None

def _atr(bars, n=14):
    trs = []
    for a, b in zip(bars, bars[1:]):
        trs.append(max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"])))
    return st.mean(trs[-n:]) if len(trs) >= 2 else None

def futures_block(fut):
    """
    One block per contract, sorted by how unusual its volatility is right now —
    so whatever is actually moving sits at the top rather than in whatever order
    the files happened to load.
    """
    if not fut:
        return []
    rows = []
    for sym, bars in fut.items():
        if len(bars) < FUT_SESSION + 2:
            continue
        sess = bars[-FUT_SESSION:]
        vol  = _realised_vol(sess)
        if vol is None:
            continue

        # Regime from this contract's OWN history, not a shared threshold.
        hist = []
        for i in range(FUT_SESSION, len(bars) + 1):
            v = _realised_vol(bars[i - FUT_SESSION:i])
            if v is not None:
                hist.append(v)
        pct = 100.0 * sum(1 for h in hist if h <= vol) / len(hist) if hist else None
        band = ("HIGH" if pct is not None and pct >= 80 else
                "LOW"  if pct is not None and pct <= 20 else "NORMAL")

        last = sess[-1]["c"]
        vw   = _vwap(sess)
        atr  = _atr(sess)
        chg  = (last / sess[0]["c"] - 1) * 100 if sess[0]["c"] else None
        vols = [b.get("v") or 0 for b in sess]
        vratio = (vols[-1] / st.mean(vols)) if st.mean(vols) else None
        age_h = (datetime.now(timezone.utc).timestamp() - sess[-1]["t"]) / 3600
        rows.append(dict(sym=sym, name=bars[0]["name"], last=last, chg=chg, vol=vol,
                         pct=pct, band=band, vw=vw, atr=atr, vratio=vratio, age=age_h,
                         nbars=len(bars), nhist=len(hist)))

    rows.sort(key=lambda r: -(r["pct"] or 0))     # most unusual first

    def ordinal(n):
        n = int(round(n))
        suf = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suf}"

    L = ["FUTURES  (15-min bars, ~10 min delayed - research data, not signals)"]
    for r in rows:
        vwd = (r["last"] / r["vw"] - 1) * 100 if r["vw"] else None
        # Round before signing, so a -0.004% move prints 0.00% rather than -0.00%.
        chg = 0.0 if r["chg"] is None else round(r["chg"], 2) + 0.0
        head = f"   {r['sym']:<5} {r['name']:<11} {r['last']:>10,.2f}"
        L.append(head + (f"  {chg:+.2f}% session" if r["chg"] is not None else ""))
        L.append(f"         vol {r['vol']:.3f}%/bar  {r['band']} ({ordinal(r['pct'])} pct of own history)")
        if vwd is not None:
            side = "above" if vwd >= 0 else "below"
            L.append(f"         VWAP {r['vw']:>10,.2f}   price {abs(vwd):.2f}% {side}")
        if r["atr"] is not None:
            L.append(f"         ATR {r['atr']:>11,.2f}   volume {r['vratio']:.1f}x session avg"
                     if r["vratio"] else f"         ATR {r['atr']:>11,.2f}")
    oldest_note = max((r["age"] for r in rows), default=0)
    if oldest_note > 3:
        L.append(f"   (newest bar {oldest_note:.0f}h old - market closed)")
    if rows:
        eff = rows[0]["nbars"] // FUT_SESSION
        if eff < 40:
            L.append(f"   Percentiles rest on ~{eff} independent sessions of history - treat")
            L.append(f"   the bands as provisional until roughly two weeks have accumulated.")
    L.append("")
    return L

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
        return "BTC 15m recorder — NO DATA", "data/ is empty. Is com.dhruv.marketlab loaded?"

    now      = datetime.now(timezone.utc)
    day_ago  = now - timedelta(hours=24)
    used     = sum(os.path.getsize(f) for f in glob.glob(os.path.join(HERE, "data", "*.jsonl")))
    total    = sum(len(v) for v in data.values())
    btc      = data.get("KXBTC15M", [])
    night    = [r for r in btc if (wtime(r) or day_ago) > day_ago]

    # ---------------------------------------------------------------- health
    problems = []
    old_gaps = {}
    for s_, rows in sorted(data.items()):
        newest = max((t for t in (wtime(r) for r in rows) if t), default=None)
        if newest and (now - newest).total_seconds() / 3600 > 2:
            problems.append(f"{s_} last captured {(now-newest).total_seconds()/3600:.1f}h ago — recorder may be stopped")
        # Gaps are permanent (Kalshi keeps ~2 days), so a lifetime count would
        # flag every report forever. Only a gap in the last 24h is news.
        miss, _ = gaps(rows)
        recent = [m for m in miss if m > day_ago]
        if recent:
            problems.append(f"{s_} missed {len(recent)} window(s) in the last 24h")
        old_gaps[s_] = len(miss) - len(recent)
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

    # Countdown to the Chronos test — one quiet line, so the dedicated email
    # when it lands is a confirmation rather than a surprise.
    if btc:
        left = CHRONOS_THRESHOLD - len(btc)
        if left > 0:
            L.append(f"Chronos test: {len(btc)} of {CHRONOS_THRESHOLD} windows "
                     f"({left} to go, ~{left/96:.0f} days).")
        else:
            L.append(f"Chronos test: READY - {len(btc)} windows. "
                     f"Say \"fire Chronos on the Kalshi data\".")
        L.append("")

    L.extend(futures_block(load_futures()))

    # --------------------------------------------------------------- footer
    L.append(f"{total} windows recorded, {fmt_bytes(used)} used"
             f"{'' if used/MAX_BYTES < 0.5 else f' ({used/MAX_BYTES*100:.0f}% of ceiling)'}.")
    if any(old_gaps.values()):
        L.append("Older permanent gaps: " + ", ".join(f"{k} {v}" for k, v in sorted(old_gaps.items()) if v) + ".")
    L.append("Figures are in-sample and exclude fees - not advice.")
    L.append("Detail: python3 analyze.py   |   python3 query.py \"SELECT ...\"")

    head = "NEEDS ATTENTION" if problems else "OK"
    subject = f"BTC 15m recorder - {head}"
    if btc and not problems:
        reg = regime_series(btc)
        subject += f" - vol {reg[-1][2]} {reg[-1][1]:.0f}c" if len(reg) else ""
    return subject, "\n".join(L)


def chronos_ready(btc_n):
    """
    One-time email when the dataset is large enough to test Chronos.

    Fires once and then never again — a reminder repeated daily becomes another
    line you skim past, which defeats the point of waiting for it.
    """
    if btc_n < CHRONOS_THRESHOLD or os.path.exists(CHRONOS_FLAG):
        return None
    # State the power claim only when it is actually true. If the threshold was
    # lowered by hand, say so rather than repeating a justification that no
    # longer holds — a reminder that overstates its own basis is worse than none.
    half = btc_n // 2
    if btc_n >= 1400:
        why = (f"   A 50/50 split leaves ~{half} test windows, which detects a move from the\n"
               f"   83% opening-print baseline to 88% at roughly 80% power. Below ~1000\n"
               f"   windows a five-point difference is indistinguishable from noise.")
    else:
        why = (f"   NOTE: the threshold was lowered to {CHRONOS_THRESHOLD}, below the ~1400\n"
               f"   originally chosen. A {half}-window holdout may not separate a real\n"
               f"   improvement from noise, so treat any result as provisional.")
    body = f"""The BTC 15m dataset has reached {btc_n} windows ({btc_n/96:.0f} days).
That is enough to test a forecasting model with a holdout that can actually
separate a real improvement from noise.

WHY THIS NUMBER
{why}

THE TEST, AS AGREED
   Model      Chronos-2 / Chronos-Bolt (HuggingFace, zero-shot, runs on CPU)
   Fit on     the first half of the windows, chronologically
   Predict    the second half
   Beat       "just use the opening price" - currently right 83% of the time
   Prediction stated in advance: Chronos LOSES to the opening print, because
              the market prices ~8,000 trades per window

   A negative result is the point, not a failure. It is written down now so it
   cannot be quietly revised afterwards.

TO START
   Tell Claude: "fire Chronos on the Kalshi data"

This reminder is sent once. Delete .chronos-notified to re-arm it."""
    return (f"BTC 15m - ready for the Chronos test ({btc_n} windows)", body)


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
    def send(subj, text, tries=4):
        # A single network blip used to cost the whole day's report. Retry with
        # growing pauses; the job runs once a day, so waiting is cheap.
        env = {**os.environ, "FLIP_GMAIL_APP_PASSWORD": pw, "SEND_EMAIL_TIMEOUT_MS": "90000"}
        for n in range(tries):
            try:
                r = subprocess.run(["node", SEND, subj, text], capture_output=True,
                                   text=True, timeout=120, env=env)
                err = None if r.returncode == 0 else (r.stderr or r.stdout).strip()[:300]
            except subprocess.TimeoutExpired:
                err = "node send-email.js timed out"
            if err is None:
                print(f"sent: {subj}"); return True
            print(f"send failed (try {n+1}/{tries}):", err, flush=True)
            if "permanent" in err or n == tries - 1:
                return False
            time.sleep(60 * (n + 1))
        return False

    if not send(subject, body):
        sys.exit(1)

    # Separate email, so the one moment worth acting on is not buried inside a
    # routine daily report.
    btc_n = len(load().get("KXBTC15M", []))
    ready = chronos_ready(btc_n)
    if ready and send(*ready):
        open(CHRONOS_FLAG, "w").write(datetime.now(timezone.utc).isoformat() + "\n")

if __name__ == "__main__":
    main()
