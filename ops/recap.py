#!/usr/bin/env python3
"""One nightly recap email for all three Jev desks (replaces the separate Markets and Majors review emails). 17:50 ET by launchd.
Sections: the headline (paper P&L today, per desk), each desk's own review text (Markets: fills, misses, calibration; Majors: trades and positions; Jev desk: trades by lane, drawdown, guardian pauses),
Jev API spend, the watchdog's verdict, and a short 'look at tomorrow' list. Report only: it changes nothing.
  recap.py            build and email
  recap.py --no-email print only"""
import datetime as dt, importlib.util, json, os, sqlite3, sys, time

HOME = os.path.expanduser("~")
sys.path.insert(0, os.path.join(HOME, "flip-notifier")); sys.path.insert(0, os.path.join(HOME, "jev-client"))
DAY = dt.date.today().strftime("%Y-%m-%d")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def jload(p, d=None):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def desk_section():
    db = sqlite3.connect(f"file:{HOME}/jev-desk/data/desk.db?mode=ro", uri=True, timeout=10)
    rows = []
    for body, ticket, pnl, upd in db.execute("SELECT body, ticket_usd, pnl_usd, updated FROM orders WHERE status='shadow_closed'"):
        rows.append((json.loads(body or "{}"), ticket or 0, pnl or 0.0, upd or 0))
    day0 = dt.datetime.combine(dt.date.today(), dt.time.min).timestamp()
    today = [r for r in rows if r[3] >= day0]
    lane = lambda b: "pattern" if b.get("pattern") else "pregrad:explore" if b.get("pregrad_explore") else (b.get("lane") or "gated")
    L = [f"- {len(today)} paper trades closed today, net ${sum(r[2] for r in today):+,.2f}" if today else "- no trades closed today"]
    by = {}
    for b, t, p, u in today:
        by.setdefault(lane(b), []).append(p)
    for k, v in sorted(by.items()):
        L.append(f"    - {k}: {len(v)} trades, ${sum(v):+,.2f}")
    tot = sum(r[2] for r in rows)
    wins = sum(1 for r in rows if r[2] > 0)
    L.append(f"- since 10/06: {len(rows)} trades, {wins / max(1, len(rows)):.0%} wins, net ${tot:+,.2f} on a $1,000 bank (breaker at -$400)")
    g = (jload(f"{HOME}/jev-desk/data/guardian.json", {}) or {}).get("paused") or {}
    live = [k for k, v in g.items() if v.get("until", 0) > time.time()]
    L.append("- guardian pauses: " + (", ".join(live) if live else "none"))
    hb = jload(f"{HOME}/jev-desk/data/heartbeat.json", {})
    if hb.get("last_scan"):
        L.append(f"- last full scan {int((time.time() - hb['last_scan']) / 60)} minutes ago")
    return "\n".join(L), {"n": len(today), "pnl": sum(r[2] for r in today)}


def main():
    out, notes = [], []
    mk = load(f"{HOME}/jev-markets/research/mnqreview.py", "mnqreview")
    mk_md, mk_s = mk.build(DAY)
    mj = load(f"{HOME}/jev-majors/review.py", "majrev")
    mj_md, mj_s = mj.build(DAY)
    ds_md, ds_s = desk_section()
    total = mk_s["pnl"] + mj_s["pnl"] + ds_s["pnl"]
    secs = []

    def para(t):
        return {"type": "para", "text": t}
    secs.append({"title": "Headline", "blocks": [para(f"Paper P&L today across the three desks: ${total:+,.2f}"),
                                                  para(f"Jev Markets {mk_s['n']} trades ${mk_s['pnl']:+,.0f} · Jev Majors {mj_s['n']} closed ${mj_s['pnl']:+,.2f} ({mj_s['open']} open) · Jev desk {ds_s['n']} trades ${ds_s['pnl']:+,.2f}")]})

    def sect(title, md):
        body = [l for l in md.split("\n") if l.strip() and not l.startswith("# ") and not l.startswith("Report only")]
        blocks, cur = [], None
        for l in body:
            if l.startswith("## "):
                if l[3:].startswith("What could I be wrong") and title != "Jev Markets":
                    cur = {"title": "", "blocks": []}                                  # one 'what could I be wrong about' (Markets') is enough
                    continue
                cur = {"title": f"{title}: {l[3:]}", "blocks": []}; secs.append(cur)
            elif cur is None:
                cur = {"title": title, "blocks": []}; secs.append(cur); cur["blocks"].append(para(l.strip().lstrip("- ")))
            else:
                cur["blocks"].append(para(l.strip().lstrip("- ")))
    sect("Jev Markets", mk_md)
    sect("Jev Majors", mj_md)
    secs.append({"title": "Jev desk", "blocks": [para(l.strip().lstrip("- ")) for l in ds_md.split("\n") if l.strip()]})
    try:
        import jev
        spent, cap = jev.month_spend()
        sp = f"Jev API spend this month ${spent:.2f} of ${cap:.0f}"
    except Exception:
        sp = "Jev API spend unavailable"
    st = jload(f"{HOME}/market-lab/ops/data/status.json", {}) or {}
    checks = st.get("checks", [])
    bad = [f"{c['check']}: {c['msg']}" for c in checks if c.get("level") == "fail"]
    secs.append({"title": "Spend and health", "blocks": [para(sp), para(f"Watchdog: {len(checks) - len(bad)} of {len(checks)} checks ok" + ("" if not bad else "; failing: " + "; ".join(bad)))]})
    look = []
    hbm = jload(f"{HOME}/jev-markets/data/heartbeat.json", {})
    g = hbm.get("goal") or {}
    if g.get("stopped"):
        look.append(f"Jev Markets stopped for the day: {g['stopped']}")
    if (time.time() - (jload(f"{HOME}/jev-desk/data/heartbeat.json", {}).get("last_scan") or 0)) > 1800:
        look.append("Jev desk core scans are slow (over 30 minutes between full scans)")
    look.append("Compare tomorrow's Globex open (6:05 PM ET onward) against the overnight test results above")
    secs.append({"title": "Look at tomorrow", "blocks": [para(x) for x in look]})
    secs = [x for x in secs if x.get('title')]
    print(f"Jev recap {DAY}: total ${total:+,.2f}; sections {[s['title'] for s in secs]}")
    if "--no-email" in sys.argv:
        return
    import email_ui
    email_ui.send(f"Jev Desks · Nightly recap: {DAY} · ${total:+,.0f} paper today",
                  {"kind": "Nightly recap", "status": {"text": f"${total:+,.0f}", "tone": "good" if total >= 0 else "bad"}, "title": f"Jev desks nightly recap: {DAY}",
                   "subtitle": "Report only: nothing here changes any setting", "sections": secs, "footer": "~/market-lab/ops/recap.py"})


if __name__ == "__main__":
    main()
