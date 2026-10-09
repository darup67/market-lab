#!/usr/bin/env python3
"""Performance guardian for the three Jev desks. REDUCE-ONLY by design: it can pause a lane / coin-direction / trading segment whose own paper results are
statistically negative, and it never raises risk, changes an entry or exit rule, or touches sizing. Pauses expire (default 7 days; 3 for Markets segments) so a
segment gets re-tested instead of being banned for ever. It writes data/guardian.json in each desk, which the desk reads once a minute.
(No script can promise a result: what this does is stop the known bleeders so they no longer drag the totals down.)

  guardian.py            evaluate all three desks, write the pauses, email only if something changed
  guardian.py --dry      show what it would do
  guardian.py --status   current pauses
  guardian.py --clear    remove every pause (manual override)

Rules (all need real sample sizes; none fires on a handful of trades):
  Jev desk     a LANE (gated, swing, explore, pregrad, pregrad:explore, pattern) with >= 12 closed paper trades in the last 14 days, mean return <= -4%, t <= -1.0
  Jev Majors   a COIN:SIDE with >= 8 closed paper trades in the last 30 days, mean ROE <= -5%, t <= -1.5
  Jev Markets  a SEGMENT (mode, trigger, 3-hour block) with >= 25 closed MNQ trades in the last 14 days, profit factor < 0.8, net negative, t <= -1.5"""
import datetime as dt, json, math, os, sqlite3, statistics as st, sys, time

HOME = os.path.expanduser("~")
LOG = os.path.join(HOME, "market-lab", "ops", "data", "guardian.log")
sys.path.insert(0, os.path.join(HOME, "flip-notifier"))


def tstat(xs):
    if len(xs) < 3:
        return 0.0
    sd = st.stdev(xs)
    return (st.mean(xs) / (sd / math.sqrt(len(xs)))) if sd > 0 else 0.0


def load(desk):
    p = os.path.join(HOME, desk, "data", "guardian.json")
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return {"paused": {}}


def save(desk, g):
    p = os.path.join(HOME, desk, "data", "guardian.json")
    json.dump(g, open(p + ".tmp", "w"), indent=1)
    os.replace(p + ".tmp", p)


def db(desk):
    return sqlite3.connect(f"file:{os.path.join(HOME, desk, 'data', 'desk.db')}?mode=ro", uri=True, timeout=10)


def desk_lanes():
    since = time.time() - 14 * 86400
    g = {}
    for body, ticket, pnl, upd in db("jev-desk").execute("SELECT body, ticket_usd, pnl_usd, updated FROM orders WHERE status='shadow_closed'"):
        if (upd or 0) < since or not ticket:
            continue
        b = json.loads(body or "{}")
        lane = "pattern" if b.get("pattern") else "pregrad:explore" if b.get("pregrad_explore") else (b.get("lane") or "gated")
        g.setdefault(lane, []).append((pnl or 0.0) / ticket)
    return {k: v for k, v in g.items() if len(v) >= 12 and st.mean(v) <= -0.04 and tstat(v) <= -1.0}, g


def majors_coins():
    since = time.time() - 30 * 86400
    g = {}
    for coin, side, margin, pnl, upd in db("jev-majors").execute("SELECT coin, side, margin_usd, pnl_usd, updated FROM orders WHERE status='shadow_closed'"):
        if (upd or 0) < since or not margin:
            continue
        g.setdefault(f"{coin}:{side}", []).append((pnl or 0.0) / margin)
    return {k: v for k, v in g.items() if len(v) >= 8 and st.mean(v) <= -0.05 and tstat(v) <= -1.5}, g


def markets_segments():
    since = time.time() - 14 * 86400
    rows = []
    for body, close, pnl, upd, created in db("jev-markets").execute("SELECT body, close, pnl_usd, updated, created FROM orders WHERE status='closed' AND symbol='NQ'"):
        if (upd or 0) < since:
            continue
        b, c = json.loads(body or "{}"), json.loads(close or "{}")
        if c.get("r") is None:
            continue
        rows.append({"mode": b.get("scalp_mode") or "rth", "trigger": b.get("trigger") or "?", "hour": dt.datetime.fromtimestamp(created).hour // 3 * 3, "pnl": pnl or 0.0, "r": c["r"]})
    seg = {}
    for r in rows:
        for key in (f"mode:{r['mode']}", f"trigger:{r['trigger']}", f"hours:{r['hour']:02d}"):
            seg.setdefault(key, []).append(r)
    bad = {}
    for k, v in seg.items():
        gp, gl = sum(x["pnl"] for x in v if x["pnl"] > 0), -sum(x["pnl"] for x in v if x["pnl"] < 0)
        pf = gp / gl if gl else 99
        if len(v) >= 25 and pf < 0.8 and sum(x["pnl"] for x in v) < 0 and tstat([x["r"] for x in v]) <= -1.5:
            bad[k] = [x["r"] for x in v]
    return bad, {k: [x["r"] for x in v] for k, v in seg.items()}


def apply(desk, bad, allg, days, label, dry):
    g = load(desk)
    now = time.time()
    g["paused"] = {k: v for k, v in (g.get("paused") or {}).items() if v.get("until", 0) > now}      # expired pauses fall away: the segment is re-tested
    changes = []
    for k, xs in bad.items():
        if k not in g["paused"]:
            why = f"{label}: n={len(xs)}, mean {st.mean(xs):+.1%}, t={tstat(xs):.1f} over the last window"
            g["paused"][k] = {"since": now, "until": now + days * 86400, "why": why}
            changes.append(f"PAUSED {k}: {why} (until {dt.datetime.fromtimestamp(now + days * 86400):%b %-d})")
    g["checked"] = now
    if not dry:
        save(desk, g)
    return g, changes


def main():
    dry, status = "--dry" in sys.argv, "--status" in sys.argv
    if "--clear" in sys.argv:
        for d in ("jev-desk", "jev-majors", "jev-markets"):
            save(d, {"paused": {}, "checked": time.time()})
        print("all pauses cleared")
        return
    if status:
        for d in ("jev-desk", "jev-majors", "jev-markets"):
            g = load(d)
            print(d, "paused:", {k: (v["why"], dt.datetime.fromtimestamp(v["until"]).strftime("%b %d")) for k, v in (g.get("paused") or {}).items()} or "none")
        return
    out, lines = [], []
    for desk, fn, days, label in (("jev-desk", desk_lanes, 7, "lane losing"), ("jev-majors", majors_coins, 7, "coin+side losing"), ("jev-markets", markets_segments, 3, "segment losing")):
        try:
            bad, allg = fn()
            g, ch = apply(desk, bad, allg, days, label, dry)
        except Exception as e:
            lines.append(f"{desk}: could not evaluate ({e!r})")
            continue
        tops = ", ".join(f"{k} n={len(v)} mean {st.mean(v):+.1%}" for k, v in sorted(allg.items(), key=lambda x: st.mean(x[1]))[:4]) if allg else "no trades in the window"
        lines.append(f"{desk}: {len(g['paused'])} paused; weakest: {tops}")
        out += [f"{desk}: {c}" for c in ch]
    msg = "\n".join(lines + [""] + (out or ["no new pauses"]))
    print(msg)
    if not dry:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        open(LOG, "a").write(f"{dt.datetime.now():%F %T}\n{msg}\n\n")
        if out:
            try:
                import email_ui
                secs = [{"title": "New pauses (reduce-only; expire automatically)", "blocks": [{"type": "para", "text": c} for c in out]},
                        {"title": "Status", "blocks": [{"type": "para", "text": l} for l in lines]},
                        {"title": "How to override", "blocks": [{"type": "para", "text": "Run ~/market-lab/ops/guardian.py --clear to remove every pause, or delete one entry from the desk's data/guardian.json."}]}]
                email_ui.send("Jev guardian · " + f"{len(out)} pause(s) applied", {"kind": "Performance guardian", "status": {"text": f"{len(out)} PAUSED", "tone": "bad"}, "title": "The performance guardian paused losing segments",
                              "subtitle": "Reduce-only: it never raises risk or changes entries and exits", "sections": secs, "footer": "~/market-lab/ops/guardian.py"})
            except Exception:
                pass


if __name__ == "__main__":
    main()
