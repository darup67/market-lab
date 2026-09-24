#!/usr/bin/env python3
"""Event desk: earnings/news briefings by email, with Jev reading the headlines.

  desk.py watchlist [--dry]   every ticker on the TradingView watchlist: earnings ahead + news
  desk.py biopharma [--dry]   the 10 health-care names with the biggest pending event impact
  desk.py jev-status          is a key in place, and how much is judged
  desk.py --test-email

--dry writes preview-<desk>.html and sends nothing. Read-only throughout: no
orders, no recommendations. Jev labels are unvalidated until data/judged.jsonl
has been scored against price moves.
"""
import datetime as dt, html, json, os, smtplib, ssl, subprocess, sys, time
from email.header import Header
from email.mime.text import MIMEText

import judge, sources

HERE = os.path.dirname(os.path.abspath(__file__))
e = html.escape


def cfg():
    with open(os.path.join(HERE, "config.json")) as f:
        return json.load(f)


def log(msg):
    print(f"{dt.datetime.now():%m-%d %H:%M:%S} {msg}", flush=True)


def ago(ts):
    s = time.time() - ts
    return f"{int(s // 60)}m" if s < 3600 else f"{s / 3600:.0f}h"


def fnum(x, fmt="{:.0f}"):
    try:
        return fmt.format(float(x))
    except (TypeError, ValueError):
        return "—"


# ---------------------------------------------------------------- shared
def collect_news(tickers, lookback, c):
    """Fetch, store, and (if keyed) judge headlines. Returns {ticker: summary}."""
    raw = {}
    for t in tickers:
        items = sources.headlines(t["yahoo"], lookback)
        judge.store(t["id"], t["name"], items)
        raw[t["id"]] = items
        time.sleep(0.25)
    n = judge.judge_backlog(c["jev"]["judge_per_run"], log=log)
    log(f"news: {sum(map(len, raw.values()))} headlines across {len(raw)} tickers; Jev judged {n}")
    judged = judge.answers()
    return {k: judge.summarize(k, v, judged, c["jev"]) for k, v in raw.items()}


def jev_banner(ready):
    if ready:
        return ('<p style="font-size:12px;color:#555;margin:6px 0 14px">Jev labels (topic, direction, '
                '<b>material</b>) come from TypeSafe&#8217;s Jev reading each headline. They are unvalidated: '
                'treat them as a reading aid, not a signal.</p>')
    return ('<p style="font-size:12px;color:#8a5a00;background:#fff6e0;padding:8px 10px;border-radius:4px;'
            'margin:6px 0 14px">Jev is not connected yet (no TypeSafe key in the Keychain), so headlines are '
            'shown unjudged, newest first. Run <code>python3 ~/jev-client/jev.py --set-key</code> once you '
            'have a key; the stored backlog is judged on the next run.</p>')


def news_cell(s, n):
    rows = s["rows"][:n]
    if not rows:
        return '<span style="color:#999">no headlines</span>'
    out = []
    for r in rows:
        j = r["jev"]
        tag = ""
        if j:
            if not j["relevant"]:
                tag = '<span style="color:#999">[not about it] </span>'
            else:
                col = {"up": "#1a7f37", "down": "#b42318"}.get(j["direction"], "#555")
                tag = (f'<span style="color:{col};font-weight:600">[{e(j["event"].replace("_", " "))}'
                       f'{" · " + j["direction"] if j["direction"] != "neutral" else ""}'
                       f'{" · MATERIAL" if j["material"] else ""}]</span> ')
        out.append(f'<div style="margin:2px 0">{tag}<a href="{e(r["link"])}" style="color:#0b62c4;'
                   f'text-decoration:none">{e(r["title"])}</a> <span style="color:#999">{ago(r["pub"])}</span></div>')
    return "".join(out)


def jev_cell(s):
    if not s["judged"]:
        return '<span style="color:#999">—</span>'
    if not s["material"]:
        return f'<span style="color:#777">{s["relevant"]} relevant, 0 material</span>'
    col = {"up": "#1a7f37", "down": "#b42318"}.get(s["lean"], "#555")
    return (f'<b style="color:{col}">{s["material"]} material · {e(s["lean"])}</b><br>'
            f'<span style="color:#555">{e((s["main_event"] or "").replace("_", " "))}</span>')


TD = 'style="padding:7px 8px;border-bottom:1px solid #e5e7eb;vertical-align:top"'
TH = 'style="padding:6px 8px;border-bottom:2px solid #111;text-align:left;font-size:12px"'


def page(title, subtitle, body):
    return (f'<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:900px;'
            f'margin:0 auto;padding:14px;font-size:13.5px;color:#111"><h2 style="margin:0">{e(title)}</h2>'
            f'<div style="color:#666;font-size:12.5px">{e(subtitle)}</div>{body}'
            f'<p style="color:#999;font-size:11px;margin-top:22px">event-desk (~/market-lab/event-desk). '
            f'Information only: no orders, no recommendations.</p></div>')


# ---------------------------------------------------------------- watchlist
def earn_tag(t, earn):
    if t["kind"] != "equity" or t["tv"] not in earn:
        return ""
    d = dt.date.fromisoformat(earn[t["tv"]]["date"])
    return f'<br><span style="color:#b35900;font-size:12px">earnings {d:%b %d}</span>'


def watchlist_report(c):
    w = c["watchlist"]
    wl, updated = sources.watchlist(c)
    for t in wl:
        t["id"] = t["yahoo"]
    earn = sources.earnings(w["earnings_horizon_days"], log)
    news = collect_news(wl, w["news_lookback_hours"], c)
    ready = judge.jev_ready()

    ahead = sorted(((t, earn[t["tv"]]) for t in wl if t["kind"] == "equity" and t["tv"] in earn),
                   key=lambda x: x[1]["date"])
    body = jev_banner(ready)
    body += f'<h3 style="margin:16px 0 4px">Earnings in the next {w["earnings_horizon_days"]} days</h3>'
    body += ("".join(f'<div>{e(t["tv"])} ({e(t["name"])}): <b>{dt.date.fromisoformat(x["date"]):%a %b %d}</b>, '
                     f'{e(x["time"])}</div>' for t, x in ahead)
             or '<div style="color:#777">None of the watchlist stocks report in this window.</div>')

    order = sorted(wl, key=lambda t: (-news[t["id"]]["material"], -news[t["id"]]["relevant"],
                                      -len(news[t["id"]]["rows"])))
    rows = "".join(
        f'<tr><td {TD}><b>{e(t["tv"])}</b><br><span style="color:#666;font-size:12px">{e(t["name"])} · {t["kind"]}'
        f'</span>{earn_tag(t, earn)}</td>'
        f'<td {TD}>{jev_cell(news[t["id"]])}</td>'
        f'<td {TD}>{news_cell(news[t["id"]], w["headlines_per_ticker"])}</td></tr>'
        for t in order)
    body += (f'<h3 style="margin:18px 0 4px">All {len(wl)} watchlist tickers · news in the last '
             f'{w["news_lookback_hours"]}h</h3><table style="width:100%;border-collapse:collapse">'
             f'<tr><th {TH}>Ticker</th><th {TH}>Jev</th><th {TH}>Headlines</th></tr>{rows}</table>')
    mat = sum(news[t["id"]]["material"] for t in wl)
    subject = (f"Watchlist briefing: {len(ahead)} earnings ahead" +
               (f", {mat} material headlines" if ready else f", {sum(len(news[t['id']]['rows']) for t in wl)} headlines"))
    sub = (f"{dt.datetime.now():%A %b %d, %I:%M %p} · {len(wl)} tickers from the TradingView watchlist "
           f"(list last changed {updated[:10] if updated else '?'}; re-checked against TradingView each weekday 08:33)")
    return subject, page("Watchlist events briefing", sub, body)


# ---------------------------------------------------------------- biopharma
def biopharma_report(c):
    b = c["biopharma"]
    rows, snap_date = sources.iv_snapshot(b["snapshot_dir"])
    if not rows:
        raise SystemExit("no biotech-iv-agent snapshot found")
    cats = sources.catalysts(b["snapshot_dir"])
    today = dt.date.today()
    win = b["event_window_days"]

    def next_event(r):
        """(proximity 0..1, label) of the event nearest to landing, or None.

        A dated event scores 1 today falling to 0 at `win` days. A window
        ("Q3 2026", "2H 2026") scores at the midpoint of what is left of it,
        at 60% weight, because it may land anywhere inside that range."""
        best = None
        evs = [(ev["start"], ev["end"], ev["event"]) for ev in cats.get(r["ticker"], [])]
        if r.get("earnings"):
            evs.append((r["earnings"], r["earnings"], "Earnings"))
        for s0, e0, name in evs:
            s, en = dt.date.fromisoformat(s0), dt.date.fromisoformat(e0)
            if en < today:
                continue
            if s == en:
                days, w, when = (s - today).days, 1.0, f"{s:%b %d}"
            else:
                lo = max(s, today)
                days, w, when = (lo - today).days + (en - lo).days / 2, 0.6, f"window to {en:%b %d}"
            prox = w * max(0.0, 1 - days / win)
            if prox > 0 and (best is None or prox > best[0]):
                best = (prox, f"{when}: {name}")
        return best

    cand = []
    for r in rows:
        try:
            score = float(r["score"])
        except (TypeError, ValueError):
            continue
        ev = next_event(r)
        prox = ev[0] if ev else 0.0
        cand.append({"r": r, "score": score, "ev": ev, "prox": prox, "id": r["ticker"], "yahoo": r["ticker"],
                     "name": r["ticker"]})
    W = b["weights"]
    cand.sort(key=lambda x: -(W["score"] * x["score"] / 100 + W["event"] * x["prox"]))
    cand = cand[: b["candidates"]]
    news = collect_news(cand, b["news_lookback_hours"], c)
    ready = judge.jev_ready()
    for x in cand:
        s = news[x["id"]]
        intensity = min(1.0, s["material"] / 3) if s["judged"] else min(1.0, len(s["rows"]) / 5) * 0.5
        x["impact"] = 100 * (W["score"] * x["score"] / 100 + W["event"] * x["prox"] + W["news"] * intensity)
    top = sorted(cand, key=lambda x: -x["impact"])[: b["top_n"]]

    trs = []
    for i, x in enumerate(top, 1):
        r, s = x["r"], news[x["id"]]
        ev = e(x["ev"][1][:110]) if x["ev"] else '<span style="color:#999">no event due within 30d</span>'
        bias = r.get("bias_label") or "—"
        bcol = {"Bull": "#1a7f37", "Bear": "#b42318"}.get(bias, "#555")
        trs.append(
            f'<tr><td {TD}><b>{i}. {e(r["ticker"])}</b><br><span style="color:#666;font-size:12px">'
            f'{e(r.get("sector") or "")} · ${fnum(r.get("spot"), "{:.2f}")}</span></td>'
            f'<td {TD}><b>{x["impact"]:.0f}</b><br><span style="color:#666;font-size:12px">explode {x["score"]:.0f}</span></td>'
            f'<td {TD}>{ev}</td>'
            f'<td {TD}>IV30 {fnum(float(r["iv30"]) * 100 if r.get("iv30") else None)}%<br>'
            f'move ±{fnum(float(r["implied_move"]) * 100 if r.get("implied_move") else None)}%<br>'
            f'<span style="color:{bcol}">{e(bias)}</span></td>'
            f'<td {TD}>{jev_cell(s)}</td>'
            f'<td {TD}>{news_cell(s, b["headlines_per_ticker"])}</td></tr>')
    body = jev_banner(ready)
    body += ('<p style="font-size:12px;color:#555;margin:0 0 10px">Ranked by <b>impact</b>: the IV agent&#8217;s '
             f'explode score ({W["score"]:.0%}), how soon the next dated catalyst or earnings lands ({W["event"]:.0%}), '
             f'and news intensity ({W["news"]:.0%}). Impact measures how big the pending event looks, '
             'not which way it goes. Bias is the IV agent&#8217;s options-flow read.</p>')
    body += (f'<table style="width:100%;border-collapse:collapse"><tr><th {TH}>Ticker</th><th {TH}>Impact</th>'
             f'<th {TH}>Next event</th><th {TH}>Options</th><th {TH}>Jev</th><th {TH}>Headlines</th></tr>'
             f'{"".join(trs)}</table>')
    subject = "Bio/pharma events: top 10 · " + ", ".join(x["r"]["ticker"] for x in top[:4]) + " …"
    sub = (f"{dt.datetime.now():%A %b %d, %I:%M %p} · from biotech-iv-agent snapshot {snap_date} "
           f"({len(rows)} names, {len(cand)} checked for news)")
    return subject, page("Bio/pharma event impact: top 10", sub, body)


# ---------------------------------------------------------------- delivery
def send(c, subject, body_html):
    m = c["email"]
    pw = subprocess.run(["security", "find-generic-password", "-a", m["keychain_account"], "-s",
                         m["keychain_service"], "-w"], capture_output=True, text=True, check=True).stdout.strip()
    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = f"Event Desk <{m['keychain_account']}>"
    msg["To"] = m["to"]
    for i in range(3):
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
                s.login(m["keychain_account"], pw)
                s.sendmail(m["keychain_account"], [m["to"]], msg.as_string())
            return True
        except Exception as ex:
            log(f"email attempt {i + 1} failed: {ex!r}")
            time.sleep(5 * (i + 1))
    return False


def autocommit():
    """Commit and push event-desk/data only, like paper-lab does. Never raises."""
    def git(*a):
        r = subprocess.run(["git", *a], cwd=HERE, capture_output=True, text=True, timeout=60)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    try:
        ok, out = git("status", "--porcelain", "data")
        if ok and out:
            git("add", "data")
            ok, out = git("-c", "user.name=event-desk", "-c", "user.email=event-desk@localhost",
                          "commit", "-q", "-m", "event-desk: headlines and judgments", "--", "data")
            if not ok:
                return log(f"autocommit failed: {out[-200:]}")
        ok, ahead = git("rev-list", "--count", "@{u}..HEAD")
        if ok and ahead != "0":
            ok, out = git("push", "-q", "origin", "HEAD")
            log("autocommit: pushed" if ok else f"autocommit: push failed {out[-200:]}")
    except Exception as ex:
        log(f"autocommit error {ex!r}")


def main():
    c = cfg()
    args = sys.argv[1:]
    dry = "--dry" in args
    if "--test-email" in args:
        sys.exit(0 if send(c, "Event desk: test email", "<p>Event desk email delivery works.</p>") else 1)
    cmd = next((a for a in args if not a.startswith("--")), None)
    if cmd == "jev-status":
        print(f"Jev key: {'present' if judge.jev_ready() else 'MISSING (python3 ~/jev-client/jev.py --set-key)'}")
        print(f"headlines stored: {len(judge._read(judge.HEADLINES))}, judged: {len(judge._read(judge.JUDGED))}")
        return
    if cmd not in ("watchlist", "biopharma"):
        print(__doc__)
        return
    if cmd == "biopharma" and dt.date.today().weekday() >= 5 and not dry:
        return log("weekend: no fresh IV snapshot, skipping")
    subject, body = (watchlist_report if cmd == "watchlist" else biopharma_report)(c)
    if dry:
        path = os.path.join(HERE, f"preview-{cmd}.html")
        with open(path, "w") as f:
            f.write(body)
        log(f"dry run: {subject} -> {path}")
    else:
        log(f"email {'sent' if send(c, subject, body) else 'FAILED'}: {subject}")
        if os.environ.get("EVENTDESK_AUTOCOMMIT", "1") != "0":
            autocommit()


if __name__ == "__main__":
    main()
