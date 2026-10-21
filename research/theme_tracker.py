"""Monthly theme tracker: trend, distance from highs and momentum rank for the 2027 theme ETFs, vs QQQ.
Writes market-lab/data/theme_tracker.json; the 08:55 morning brief (flip-notifier/headless-flip.js) shows it once per month.
Research only: no orders, no recommendations. Run: python3 theme_tracker.py [--force]"""
import datetime as dt, json, os, sys, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data", "theme_tracker.json")
THEMES = [("CIBR", "Cybersecurity"), ("IGV", "AI software"), ("SMH", "Semis / AI chips"), ("QTUM", "Quantum"), ("NLR", "Nuclear"), ("URA", "Uranium"),
          ("GRID", "Power grid"), ("XLE", "Oil & gas"), ("GDX", "Gold miners"), ("UFO", "Space"), ("ITA", "Defense"), ("IBIT", "Bitcoin"),
          ("ARKK", "Disruptive tech"), ("XBI", "Biotech"), ("COPX", "Copper"), ("TAN", "Solar"), ("LIT", "Lithium / EV")]
BENCH = "QQQ"
H = {"User-Agent": "Mozilla/5.0"}


def closes(sym):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", headers=H, timeout=30,
                     params={"period1": int(time.time() - 420 * 86400), "period2": int(time.time()), "interval": "1d"}).json()["chart"]["result"][0]
    return [c for c in r["indicators"]["adjclose"][0]["adjclose"] if c]


def row(sym, name):
    c = closes(sym)
    ret = lambda d: round(100 * (c[-1] / c[-1 - d] - 1), 1) if len(c) > d else None
    ma = lambda n: sum(c[-n:]) / n if len(c) >= n else None
    m50, m200 = ma(50), ma(200)
    hi = max(c[-252:])
    trend = "up" if m50 and m200 and c[-1] > m50 > m200 else "down" if m50 and m200 and c[-1] < m50 < m200 else "mixed"
    # turning up: back above the 200-day after spending most of the prior 3 months below it
    below = sum(1 for i in range(-63, -5) if len(c) >= 200 - i and c[i] < sum(c[i - 199:i + 1]) / 200) if len(c) > 270 else 0
    turning = bool(m200 and c[-1] > m200 and below >= 35)
    return {"sym": sym, "theme": name, "px": round(c[-1], 2), "r1m": ret(21), "r3m": ret(63), "r6m": ret(126), "r12m": ret(252),
            "off_high": round(100 * (c[-1] / hi - 1), 1), "vs_200d": round(100 * (c[-1] / m200 - 1), 1) if m200 else None, "trend": trend, "turning_up": turning}


def main(force=False):
    month = dt.date.today().strftime("%Y-%m")
    prev = {}
    if os.path.exists(OUT):
        prev = json.load(open(OUT))
        if prev.get("month") == month and not force:
            print("already have", month)
            return
    rows, bad = [], []
    for s, n in THEMES:
        try:
            rows.append(row(s, n))
        except Exception as e:
            bad.append(f"{s}: {str(e)[:60]}")
        time.sleep(0.5)
    rows.sort(key=lambda r: -(r["r3m"] if r["r3m"] is not None else -999))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    old = {r["sym"]: r["rank"] for r in (prev.get("rows") or [])} if prev.get("month") != month else {r["sym"]: r.get("prev_rank") for r in prev.get("rows", [])}
    for r in rows:
        r["prev_rank"] = old.get(r["sym"])
    bench = row(BENCH, "Nasdaq-100")
    top3 = [r["sym"] for r in rows[:3]]
    prev_top3 = prev.get("top3") if prev.get("month") != month else prev.get("prev_top3")
    out = {"month": month, "t": time.time(), "rows": rows, "bench": bench, "top3": top3, "prev_top3": prev_top3,
           "turning_up": [r["sym"] for r in rows if r["turning_up"]], "beating_qqq_3m": [r["sym"] for r in rows if (r["r3m"] or -999) > (bench["r3m"] or 0)], "errors": bad, "shown": None}
    json.dump(out, open(OUT, "w"), indent=1)
    print(json.dumps({k: out[k] for k in ("month", "top3", "prev_top3", "turning_up", "beating_qqq_3m", "errors")}, indent=1))


if __name__ == "__main__":
    main("--force" in sys.argv)
