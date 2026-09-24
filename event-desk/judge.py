"""Jev's read of each headline, per ticker. Text in, typed answers out.

Every headline the desk sees is stored in data/headlines.jsonl. When a
TypeSafe key exists (Keychain service typesafe-jev, via ~/jev-client), Jev
judges unjudged ones, backlog included, and answers land in data/judged.jsonl.
Without a key nothing here fails: the emails show headlines unjudged.

Jev only reads words. Counting, netting and ranking happen in code, per its
own guidance that it is weak at numbers.
"""
import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
HEADLINES = os.path.join(HERE, "data", "headlines.jsonl")
JUDGED = os.path.join(HERE, "data", "judged.jsonl")
sys.path.insert(0, os.path.expanduser("~/jev-client"))
try:
    import jev
except ImportError:
    jev = None

EVENTS = {
    "earnings": "Quarterly results, earnings date, or an earnings preview",
    "guidance": "Company outlook, forecast, or guidance change",
    "fda_regulatory": "FDA or other regulator decision, approval, rejection, or filing",
    "clinical_data": "Clinical trial results, readout, or trial halt",
    "deal_financing": "Merger, acquisition, partnership, licensing deal, stock offering, or debt raise",
    "analyst_action": "Analyst upgrade, downgrade, or price-target change",
    "legal": "Lawsuit, investigation, settlement, or enforcement action",
    "product_business": "Product launch, contract win, customer, or operations news",
    "macro_market": "Interest rates, economy, market-wide moves, or commodities supply",
    "crypto_policy_flows": "Crypto regulation, ETF flows, exchange, or protocol news",
    "other": "None of the above",
}

QUESTIONS = {
    "about": {
        "type": "noul",
        "instructions": "Is `headline` mainly about `company` (ticker `ticker`), rather than about another "
                        "company or only mentioning it in passing?",
    },
    "event": {
        "type": "choice",
        "instructions": "What kind of event does `headline` report?",
        "criteria": EVENTS,
    },
    "direction": {
        "type": "choice",
        "instructions": "Is `headline` good news or bad news for the price of `company`?",
        "criteria": {"up": "Good news for the price", "down": "Bad news for the price",
                     "neutral": "Neither, mixed, or unclear"},
    },
    "material": {
        "type": "noul",
        "instructions": "Does `headline` report new, unexpected information likely to move `company`'s price "
                        "today, rather than a recap, opinion, price report, or list article?",
    },
}


def _read(path):
    try:
        with open(path) as f:
            return [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []


def key(ticker, link):
    return f"{ticker}|{link}"


def jev_ready():
    return jev is not None and bool(jev.api_key())


def store(ticker, name, items):
    """Append headlines not seen before for this ticker."""
    have = {key(h["ticker"], h["link"]) for h in _read(HEADLINES)}
    os.makedirs(os.path.dirname(HEADLINES), exist_ok=True)
    with open(HEADLINES, "a") as f:
        for h in items:
            k = key(ticker, h["link"])
            if k not in have:
                have.add(k)
                f.write(json.dumps({"ticker": ticker, "name": name, **h, "seen": time.time()}) + "\n")


def judge_backlog(limit, caller="event-desk", log=print):
    if not jev_ready():
        return 0
    done = {key(j["ticker"], j["link"]) for j in _read(JUDGED)}
    todo = [h for h in _read(HEADLINES) if key(h["ticker"], h["link"]) not in done]
    todo.sort(key=lambda h: -h["pub"])   # newest first: today's email matters most
    n = 0
    with open(JUDGED, "a") as f:
        for h in todo[:limit]:
            res = jev.ask({"ticker": h["ticker"], "company": h["name"], "headline": h["title"]},
                          QUESTIONS, caller=caller)
            if not res:
                log("jev: call failed, the rest wait for the next run")
                break
            f.write(json.dumps({"ticker": h["ticker"], "link": h["link"], "title": h["title"], "pub": h["pub"],
                                "model": res["model"], "answers": res["answers"], "at": time.time()}) + "\n")
            n += 1
    return n


def answers():
    """{(ticker, link): answers} for everything judged so far."""
    return {(j["ticker"], j["link"]): j["answers"] for j in _read(JUDGED)}


def summarize(ticker, items, judged, cfg):
    """Code-side rollup of Jev's per-headline answers for one ticker."""
    out = {"judged": 0, "relevant": 0, "material": 0, "up": 0, "down": 0, "events": {}, "rows": []}
    for h in items:
        a = judged.get((ticker, h["link"]))
        row = {**h, "jev": None}
        if a:
            out["judged"] += 1
            rel = a["about"]["noul"] >= cfg["about_min"]
            mat = rel and a["material"]["noul"] >= cfg["material_min"]
            row["jev"] = {"relevant": rel, "material": mat, "event": a["event"]["choice"],
                          "direction": a["direction"]["choice"], "p_material": a["material"]["noul"]}
            out["relevant"] += rel
            if mat:
                out["material"] += 1
                out["up"] += a["direction"]["choice"] == "up"
                out["down"] += a["direction"]["choice"] == "down"
                out["events"][a["event"]["choice"]] = out["events"].get(a["event"]["choice"], 0) + 1
        out["rows"].append(row)
    # material first, then relevant, then newest; unrelated headlines sink
    out["rows"].sort(key=lambda r: (-(r["jev"] or {}).get("material", False),
                                   -((r["jev"] or {}).get("relevant", True)), -r["pub"]))
    out["lean"] = ("up" if out["up"] > out["down"] else "down" if out["down"] > out["up"] else
                   "mixed" if out["material"] else None)
    out["main_event"] = max(out["events"], key=out["events"].get) if out["events"] else None
    return out
