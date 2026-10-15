#!/usr/bin/env python3
"""Jev daily report sections for the nightly recap email (recap.py): every Jev agent first, a win-rate chart, and a daily return distribution.
Read-only: it only reads each agent's own ledger. All numbers are built from the data each run (nothing hard-coded except where each agent keeps its ledger).
  jev_report.py            print the sections as text (no email)
Agents: Majors (running desk), Majors A (baseline recorder), Majors C (opposite recorder), Markets, Predict, FOMO lane, Options IV; Shadow and AskJev are listed for status.
Win rate = closed trades with P&L > 0. Return per trade = P&L / stake (Majors margin, Markets risk, Predict cost, Options IV cost, FOMO ticket)."""
import datetime as dt, json, os, sqlite3, time

HOME = os.path.expanduser("~")
P = lambda *a: os.path.join(HOME, *a)


def _db_trades(folder, status, stake_col):
    p = P(folder, "data", "desk.db")
    if not os.path.exists(p):
        return None
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
    rows = [(u or c, pnl or 0.0, st or 0.0) for c, u, pnl, st in db.execute(f"SELECT created, updated, pnl_usd, {stake_col} FROM orders WHERE status=?", (status,))]
    opn = db.execute("SELECT COUNT(*) FROM orders WHERE status=?", ("shadow_open" if status == "shadow_closed" else "open",)).fetchone()[0]
    return rows, opn


def _jl(path):
    out = []
    try:
        for ln in open(path):
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def agents():
    """[{name, role, key (supervisor id), mode, open, trades: [(t_close, pnl, stake)]}] in the order shown."""
    A = []
    for name, role, key, folder in (("Jev Majors", "Hyperliquid perps · running desk (positioning + Phantom)", "jevmajors.main", "jev-majors"),
                                    ("Majors lane A", "baseline Jev recorder (no positioning data)", "jevmajors.guarded", "jev-majors-guarded"),
                                    ("Majors lane C", "opposite-of-desk recorder", "jevmajors.invert", "jev-majors-invert")):
        r = _db_trades(folder, "shadow_closed", "margin_usd")
        A.append({"name": name, "role": role, "key": key, "mode": "paper" + (" + Kalshi demo mirror" if folder == "jev-majors" else ""), "open": r[1] if r else 0, "trades": r[0] if r else []})
    r = _db_trades("jev-markets", "closed", "risk_usd")
    A.append({"name": "Jev Markets", "role": "MNQ / MES / micro futures scalper", "key": "jevmarkets.main", "mode": "paper", "open": r[1] if r else 0, "trades": r[0] if r else []})
    tr = [(t.get("closed") or 0, t.get("pnl") or 0.0, t.get("cost") or 0.0) for f in ("trades.jsonl", "trades_general.jsonl") for t in _jl(P("jev-predict", "data", f)) if t.get("closed")]
    try:
        n_open = len(json.load(open(P("jev-predict", "data", "book.json"))).get("positions", [])) + len(json.load(open(P("jev-predict", "data", "book_general.json"))).get("positions", []))
    except (OSError, ValueError):
        n_open = 0
    A.append({"name": "Jev Predict", "role": "Kalshi 1-hour BTC (+ other markets)", "key": "jevpredict.main", "mode": "paper + Kalshi demo mirror", "open": n_open, "trades": tr})
    fo, ftr, live = 0, [], False
    try:
        c = sqlite3.connect(f"file:{P('jev-majors', 'data', 'fomo.db')}?mode=ro", uri=True, timeout=10)
        ftr = [(t or 0, p or 0.0, u or 0.0) for t, p, u in c.execute("SELECT t_out, pnl, usd FROM lane WHERE t_out IS NOT NULL")]
        fo = c.execute("SELECT COUNT(*) FROM lane WHERE t_out IS NULL").fetchone()[0]
        live = bool((json.load(open(P("jev-majors", "config.json"))).get("fomo_live") or {}).get("enabled"))
    except Exception:
        pass
    A.append({"name": "FOMO lane", "role": "memecoin buyer-pace scalps, 9 chains (in Jev Majors)", "key": "jevmajors.fomo", "mode": "paper" + (" · LIVE ARMED" if live else " · live off"), "open": fo, "trades": ftr})
    ot = _jl(P("jev-options-iv", "data", "trades.jsonl"))
    A.append({"name": "Jev Options IV", "role": "bull call spreads from the IV email", "key": "jevoptionsiv.main", "mode": "paper (manual in Robinhood)", "open": sum(1 for t in ot if not t.get("closed")),
              "trades": [(t["closed"], t.get("pnl") or 0.0, t.get("cost") or 0.0) for t in ot if t.get("closed")]})
    A.append({"name": "Jev Shadow", "role": "Chronos + FinBERT forecasts, scored vs baseline", "key": "jevshadow", "mode": "forecast only", "open": 0, "trades": None,
              "note": f"{len(_jl(P('jev-shadow', 'data', 'scored.jsonl')))} forecasts scored"})
    A.append({"name": "AskJev", "role": "chat UI for typed questions", "key": "askjev", "mode": "service", "open": 0, "trades": None})
    return A


def supervisor_status():
    try:
        s = json.load(open(P("jev-client", "data", "supervisor.json")))
        return {k: v for k, v in s["status"]["agents"].items()}, time.time() - s["t"]
    except Exception:
        return {}, None


def _wr(tr):
    return (sum(1 for t in tr if t[1] > 0) / len(tr)) if tr else None


def windows(trades, now):
    day0 = dt.datetime.combine(dt.date.fromtimestamp(now), dt.time.min).timestamp()
    return {"today": [t for t in trades if t[0] >= day0], "week": [t for t in trades if t[0] >= now - 7 * 86400], "all": trades}


def fmt_pct(x):
    return "–" if x is None else f"{100 * x:.0f}%"


def build(now=None):
    """Sections for the recap email: [{title, note, blocks}]."""
    now = now or time.time()
    A = agents()
    sup, sup_age = supervisor_status()
    for a in A:
        a["w"] = windows(a["trades"], now) if a["trades"] is not None else None
    # ---- 1. every agent, first
    rows = []
    for a in A:
        s = sup.get(a["key"], "not supervised")
        tone = "good" if s == "ok" else "warn" if s == "stopped by user" else "bad"
        t, al = (a["w"]["today"], a["w"]["all"]) if a["w"] else ([], [])
        rows.append({"a": {"v": a["name"], "bold": True}, "r": a["role"] + " · " + a["mode"], "s": {"v": "OK" if s == "ok" else s, "tone": tone, "bold": True}, "m": a["mode"], "o": str(a["open"]) if a["w"] else "–",
                     "t": str(len(t)) if a["w"] else (a.get("note") or "–"),
                     "p": ({"v": f"${sum(x[1] for x in t):+,.2f}", "tone": "good" if sum(x[1] for x in t) >= 0 else "bad"} if t else "–") if a["w"] else "–",
                     "w": f"{fmt_pct(_wr(al))} ({len(al)})" if a["w"] else "–"})
    sec1 = {"title": "All Jev agents", "note": "Status is the 30-second supervisor's last check" + (f" ({int(sup_age)} s ago)" if sup_age is not None else " (supervisor unreachable)"),
            "blocks": [{"type": "table", "columns": [{"key": "a", "label": "Agent"}, {"key": "s", "label": "Status"}, {"key": "o", "label": "Open", "align": "right"},
                                                     {"key": "t", "label": "Today", "align": "right"}, {"key": "p", "label": "Today P/L", "align": "right"}, {"key": "w", "label": "Win rate (n)", "align": "right"},
                                                     {"key": "r", "label": "What it does · mode"}], "rows": rows}]}
    # ---- 2. win-rate chart (HTML bars, email-safe)
    def bar(wr, n):
        if wr is None or not n:
            return '<span style="color:#9ca3af;font:12px sans-serif">–</span>'
        col = "#16a34a" if wr >= 0.5 else "#d97706" if wr >= 0.35 else "#dc2626"
        return (f'<div style="background:#eef0f3;border-radius:4px;height:16px;width:150px;position:relative"><div style="background:{col};height:16px;border-radius:4px;width:{max(2, int(150 * wr))}px"></div></div>'
                f'<div style="font:11px -apple-system,Helvetica,Arial,sans-serif;color:#374151;margin-top:2px">{100 * wr:.0f}% · {n} trades</div>')
    html = ['<table role="presentation" cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%">',
            '<tr>' + ''.join(f'<th align="left" style="font:600 10px -apple-system,Helvetica,Arial,sans-serif;letter-spacing:.8px;text-transform:uppercase;color:#6b7280;padding:4px 8px 6px 0">{h}</th>' for h in ("Agent", "Today", "Last 7 days", "All time")) + '</tr>']
    text = []
    for a in A:
        if not a["w"]:
            continue
        html.append(f'<tr><td style="padding:7px 8px 7px 0;border-top:1px solid #e5e7eb;font:600 13px -apple-system,Helvetica,Arial,sans-serif;color:#111827;white-space:nowrap">{a["name"]}</td>' +
                    "".join(f'<td style="padding:7px 8px 7px 0;border-top:1px solid #e5e7eb">{bar(_wr(a["w"][k]), len(a["w"][k]))}</td>' for k in ("today", "week", "all")) + '</tr>')
        text.append(f"{a['name']}: today {fmt_pct(_wr(a['w']['today']))} ({len(a['w']['today'])}) · 7d {fmt_pct(_wr(a['w']['week']))} ({len(a['w']['week'])}) · all {fmt_pct(_wr(a['w']['all']))} ({len(a['w']['all'])})")
    html.append("</table>")
    note = "Green = 50%+, amber 35-50%, red under 35%. Win rate counts closed trades only (a desk that holds losers open, like Markets, looks better than its open book); small samples swing wildly, so read the all-time bar with its trade count."
    pred = next(a for a in A if a["name"] == "Jev Predict")
    cr = [t for t in _jl(P("jev-predict", "data", "trades.jsonl")) if t.get("closed") and t.get("category") == "crypto-1h"]
    if cr:
        wr = sum(1 for t in cr if (t.get("pnl") or 0) > 0) / len(cr)
        note += f" Predict's live gate: 80% win rate over 75+ crypto-1h paper bets with net profit; now {100 * wr:.0f}% over {len(cr)}."
    sec2 = {"title": "Win rate", "note": note, "blocks": [{"type": "raw", "html": "".join(html), "text": "\n".join(text)}]}
    # ---- 3. dynamic daily distribution of returns per trade
    pooled = sorted((t[1] / t[2]) for a in A if a["w"] for t in a["w"]["week"] + [x for x in a["w"]["all"] if x[0] >= now - 14 * 86400 and x not in a["w"]["week"]] if t[2])
    if len(pooled) >= 12:
        qs = [pooled[int(len(pooled) * q)] for q in (0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875)]
        edges = sorted({round(q, 3) for q in qs})
    else:
        edges = [-0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10]
    f = lambda e: f"{100 * e:+.0f}" if abs(100 * e) >= 10 else f"{100 * e:+.1f}"
    labels = [f"< {f(edges[0])}"] + [f"{f(edges[i])} … {f(edges[i + 1])}" for i in range(len(edges) - 1)] + [f"> {f(edges[-1])}"]

    def bucket(r):
        for i, e in enumerate(edges):
            if r < e:
                return i
        return len(edges)
    day0 = dt.datetime.combine(dt.date.fromtimestamp(now), dt.time.min).timestamp()
    tot_today, tot_prev = [0] * len(labels), [0] * len(labels)
    drows = []
    for a in A:
        if not a["w"]:
            continue
        rec = [t for t in a["w"]["all"] if t[0] >= now - 14 * 86400 and t[2]]
        if not rec:
            continue
        c = [0] * len(labels)
        for t in rec:
            b = bucket(t[1] / t[2])
            c[b] += 1
            (tot_today if t[0] >= day0 else tot_prev)[b] += 1
        mx = max(c) or 1
        row = {"a": {"v": a["name"], "bold": True}}
        for i, v in enumerate(c):
            row[f"b{i}"] = {"v": str(v) if v else "·", "tone": ("bad" if i < bucket(0.0) else "good") if v else None, "bold": v == mx}
        drows.append(row)
    cols = [{"key": "a", "label": "14 days, % return"}] + [{"key": f"b{i}", "label": labels[i], "align": "right"} for i in range(len(labels))]
    peak = max(tot_today[i] + tot_prev[i] for i in range(len(labels))) or 1
    h2 = ['<table role="presentation" cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%">']
    for i, lb in enumerate(labels):
        wp, wt = int(260 * tot_prev[i] / peak), int(260 * tot_today[i] / peak)
        col_p, col_t = ("#fca5a5", "#dc2626") if i <= bucket(0.0) - 1 else ("#86efac", "#16a34a")
        h2.append(f'<tr><td style="padding:3px 10px 3px 0;font:12px -apple-system,Helvetica,Arial,sans-serif;color:#374151;white-space:nowrap">{lb}</td><td style="padding:3px 0"><div style="height:14px;white-space:nowrap;font-size:0">'
                  f'<span style="display:inline-block;height:14px;width:{wp}px;background:{col_p}"></span><span style="display:inline-block;height:14px;width:{wt}px;background:{col_t}"></span></div></td>'
                  f'<td style="padding:3px 0 3px 8px;font:11px -apple-system,Helvetica,Arial,sans-serif;color:#6b7280">{tot_prev[i]} earlier · <b>{tot_today[i]} today</b></td></tr>')
    h2.append("</table>")
    note3 = ("Return per trade in % (P&L ÷ stake) across every agent, last 14 days. The bucket edges are the data's own quantiles, so they move as the results do. "
             "Dark segment = today, light = earlier days. Left of zero is loss.") if len(pooled) >= 12 else "Fewer than 12 closed trades in 14 days, so fixed buckets are used."
    sec3 = {"title": "Daily distribution of trade returns", "note": note3, "blocks": [
        {"type": "raw", "html": "".join(h2), "text": "\n".join(f"{labels[i]}: {tot_prev[i]} earlier, {tot_today[i]} today" for i in range(len(labels)))},
        {"type": "table", "columns": cols, "rows": drows, "empty": "No closed trades in the last 14 days."}]}
    return [sec1, sec2, sec3], A


if __name__ == "__main__":
    secs, _ = build()
    for s in secs:
        print("==", s["title"], "-", s.get("note", ""))
        for b in s["blocks"]:
            if b["type"] == "table":
                for r in b["rows"]:
                    print("  ", {k: (v["v"] if isinstance(v, dict) else v) for k, v in r.items()})
            else:
                print(b.get("text", ""))
