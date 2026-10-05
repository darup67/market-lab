#!/usr/bin/env python3
"""Daily perp setups email (shared ~/flip-notifier/email-ui.js layout). Run scan.py first; this reads results/setups.json.
Sent every day: either the live LONG/SHORT calls with entry/stop/target, or 'no setup' with the exact trigger levels. Informational only.
    daily_email.py          send        email.py --test   send with the [TEST] prefix        email.py --print   print the spec only"""
import json, os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); NODE = "/usr/local/bin/node"
UI = os.path.expanduser("~/flip-notifier/email-ui.js")
def g(x):
    """Plain price formatting, never scientific: 87,400 / 2,807 / 124.90 / 0.2652."""
    a = abs(x)
    return f"{x:,.0f}" if a >= 1000 else f"{x:,.1f}" if a >= 100 else f"{x:,.2f}" if a >= 1 else f"{x:.4f}"

def build():
    res = json.load(open(os.path.join(HERE, "results", "setups.json")))
    rows, live, longs, shorts = [], 0, 0, 0
    for coin, r in res.items():
        for s in r["setups"]:
            if s["signal"] != "none":
                live += 1; longs += s["signal"] == "LONG"; shorts += s["signal"] == "SHORT"
                tone = "good" if s["signal"] == "LONG" else "bad"
                rows.append({"coin": {"v": coin, "bold": True}, "rule": s["rule"], "call": {"v": s["signal"], "tone": tone, "bold": True}, "entry": g(s["entry"]),
                             "stop": f"{g(s['stop'])} ({s['stop_pct']:.1f}%)", "target": g(s["target"]), "size": f"{s['size_x_equity']}x equity, hold <= {s['max_hold_days']}d"})
            else:
                rows.append({"coin": {"v": coin, "bold": True}, "rule": s["rule"], "call": {"v": "no setup", "tone": "neutral"},
                             "entry": f"long > {g(s['long_trigger'])}", "stop": f"then {g(s['long_stop_if_triggered'])}",
                             "target": f"short < {g(s['short_trigger'])}", "size": f"then stop {g(s['short_stop_if_triggered'])}"})
    ctx = [{"label": coin, "sub": f"${g(r['price'])}", "chips": [{"text": f"15m SuperTrend {r['context'].get('supertrend_15m', '?')}", "tone": "good" if r["context"].get("supertrend_15m") == "bull" else "bad"}] +
            [{"text": x, "tone": "neutral"} for x in r["context"].get("recent", [])]} for coin, r in res.items()]
    now = time.strftime("%a %b %d, %-I:%M %p", time.localtime())
    spec = {"kind": "DAILY CALLS", "title": f"Perp Setups: {live} live long/short call{'s' if live != 1 else ''}" if live else "Perp Setups: no live long/short calls today",
            "subtitle": f"BTC, ETH, SOL, HYPE, ADA · daily trend breakouts · {now}",
            "status": {"text": f"{longs} LONG / {shorts} SHORT" if live else "WAIT", "tone": "info" if live else "neutral"},
            "sections": [
                {"title": "Calls and levels", "note": "A call needs a daily close beyond the 55-day (TREND-55) or 20-day (TREND-20) high/low with the 100/50-day trend. With no call, the row shows the trigger levels to watch.",
                 "blocks": [{"type": "table", "columns": [{"key": "coin", "label": "Coin"}, {"key": "rule", "label": "Rule"}, {"key": "call", "label": "Call"}, {"key": "entry", "label": "Entry / long trigger"},
                            {"key": "stop", "label": "Stop"}, {"key": "target", "label": "Target / short trigger"}, {"key": "size", "label": "Size / note"}], "rows": rows}]},
                {"title": "Detector context (not a signal)", "note": "Existing coin detector, mirrored long and short. On 15m bars it did no better than random entries after costs.", "blocks": [{"type": "chipRows", "items": ctx}]},
                {"title": "How much to trust this", "blocks": [{"type": "list", "items": [
                    "TREND-55 and TREND-20 were positive out-of-sample on BTC, ETH, SOL and ADA over about 3 years (24 and 52 out-of-sample trades). Promising, small sample, one market regime.",
                    "HYPE has about 200 days of history and is not validated.",
                    "Costs assumed 0.09% per side plus real funding; Robinhood's perp fee schedule is not visible here, so check yours.",
                    "Sizing shown risks 1% of equity per trade with leverage capped at 3x."]},
                    {"type": "callout", "tone": "warn", "text": "Informational only. This email places no orders and is not investment advice. Perpetual futures are leveraged and can lose more than the margin you post."}]}],
            "footer": "Sent by perp-lab (~/market-lab/perp-lab/daily_email.py)."}
    return spec, live

def main():
    spec, live = build()
    if "--print" in sys.argv: print(json.dumps(spec, indent=1)); return
    subject = f"Perp Setups · Daily long/short calls: {live} live" if live else "Perp Setups · Daily long/short calls: none live, watching triggers"
    env = dict(os.environ, **({"EMAIL_SUBJECT_PREFIX": "[TEST] "} if "--test" in sys.argv else {}))
    if "FLIP_GMAIL_APP_PASSWORD" not in env or not env["FLIP_GMAIL_APP_PASSWORD"]:
        env["FLIP_GMAIL_APP_PASSWORD"] = subprocess.run(["security", "find-generic-password", "-a", "darup67@gmail.com", "-s", "flip-notifier-gmail", "-w"], capture_output=True, text=True).stdout.strip()
    r = subprocess.run([NODE, UI, "send", subject], input=json.dumps(spec), capture_output=True, text=True, timeout=90, env=env)
    ok = r.returncode == 0
    print("email", "sent" if ok else f"FAILED: {(r.stderr or r.stdout)[-200:]}")
    if ok and "--test" not in sys.argv:
        open(os.path.join(HERE, "results", "last-email.txt"), "w").write(time.strftime("%Y-%m-%d"))
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
