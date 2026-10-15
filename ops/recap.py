#!/usr/bin/env python3
"""Nightly Jev recap email, 17:50 ET by launchd. 2026-10-14 redesign (less text): a one-line summary, four numbers, every Jev agent, win-rate bars, the return distribution, the largest trades of the day, and only what needs attention.
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


def suggestions(limit=3):
    """Majors' 'Suggested changes (NOT applied)' lines, trimmed: the one part of the old per-desk review text worth a place in the email."""
    try:
        md, _ = load(f"{HOME}/jev-majors/review.py", "majrev").build(DAY)
    except Exception:
        return []
    out, on = [], False
    for ln in md.split("\n"):
        if ln.startswith("## "):
            on = ln[3:].lower().startswith("suggested")
        elif on and ln.strip().startswith(("-", "*")):
            t = ln.strip().lstrip("-* ").strip()
            out.append(t if len(t) <= 130 else t[:127].rsplit(" ", 1)[0] + "…")
    return out[:limit]


def main():
    import jev_report
    secs, agents, meta = jev_report.build()
    sug = suggestions()
    if sug:
        secs.append({"title": "Suggested changes", "note": "Not applied. Ask Claude to apply any of them.", "blocks": [{"type": "list", "items": sug}]})
    checks = (jload(f"{HOME}/market-lab/ops/data/status.json", {}) or {}).get("checks", [])
    bad = [f"{c['check']}: {c['msg']}" for c in checks if c.get("level") == "fail"]
    if bad:
        secs.append({"title": "Needs attention", "blocks": [{"type": "list", "items": [x if len(x) <= 140 else x[:137] + "…" for x in bad[:5]]}]})
    total = meta["total"]
    print(f"Jev recap {DAY}: total ${total:+,.2f}; sections {[s.get('title') or 'glance' for s in secs]}")
    if "--no-email" in sys.argv:
        return
    import email_ui
    email_ui.send(f"Jev Desks · Nightly recap: {DAY} · ${total:+,.0f} paper today",
                  {"kind": "Nightly recap", "status": {"text": f"${total:+,.0f}", "tone": "good" if total >= 0 else "bad"}, "title": f"Jev nightly recap · {DAY}",
                   "tldr": meta["tldr"], "sections": secs, "footer": "Paper results only"})


if __name__ == "__main__":
    main()
