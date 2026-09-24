"""Headlines, and Jev's read of them, in shadow mode.

Every run stores new headlines from the RSS feeds in config.json
(data/news.jsonl). When a TypeSafe key exists, Jev judges any unjudged
headlines, backlog included: which asset the headline is about, whether it is
good or bad news for that asset's price, and whether it is new enough to move
prices today. The report then checks those calls against what the asset
actually did over the following 4 hours.

Jev only reads the headline text. Prices and returns are computed in code.
"""
import email.utils, json, os, sys, time, urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
NEWS = os.path.join(HERE, "data", "news.jsonl")
JUDGED = os.path.join(HERE, "data", "news_judged.jsonl")
sys.path.insert(0, os.path.expanduser("~/jev-client"))
try:
    import jev
except ImportError:
    jev = None

ASSET_TO_INST = {"bitcoin": "BTC", "ether": "ETH", "us_stocks": "MES", "gold": "MGC", "crude_oil": "MCL"}

QUESTIONS = {
    "asset": {
        "type": "choice",
        "instructions": "Which market is `headline` mainly about?",
        "criteria": {
            "bitcoin": "Bitcoin (BTC) specifically",
            "ether": "Ethereum or ether (ETH) specifically",
            "us_stocks": "The US stock market, S&P 500, Nasdaq, or US large-cap tech as a group",
            "gold": "Gold",
            "crude_oil": "Crude oil or oil supply",
            "none": "None of these, a single company, or several of these equally",
        },
    },
    "direction": {
        "type": "choice",
        "instructions": "Is `headline` good news or bad news for the price of the market it is about?",
        "criteria": {"up": "Good news for the price", "down": "Bad news for the price",
                     "neutral": "Neither, mixed, or unclear"},
    },
    "market_moving": {
        "type": "noul",
        "instructions": "Does `headline` report new, unexpected information big enough to move that market "
                        "today, rather than commentary, a recap, a price report, or an opinion?",
    },
}


def _read(path):
    try:
        with open(path) as f:
            return [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []


def fetch(feeds):
    have = {n["link"] for n in _read(NEWS)}
    added = 0
    os.makedirs(os.path.dirname(NEWS), exist_ok=True)
    with open(NEWS, "a") as out:
        for url in feeds:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh)"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    root = ET.fromstring(r.read())
            except Exception as e:
                print(f"news: {url[:50]} failed: {e!r}", file=sys.stderr)
                continue
            for it in root.findall(".//item"):
                link, title = it.findtext("link"), (it.findtext("title") or "").strip()
                try:
                    pub = email.utils.parsedate_to_datetime(it.findtext("pubDate")).timestamp()
                except Exception:
                    pub = None
                if not link or not title or pub is None or link in have:
                    continue
                have.add(link)
                out.write(json.dumps({"link": link, "title": title, "pub": pub, "feed": url,
                                      "seen": time.time()}) + "\n")
                added += 1
    return added


def judge(limit):
    if jev is None or not jev.api_key():
        return 0
    done = {j["link"] for j in _read(JUDGED)}
    n = 0
    with open(JUDGED, "a") as out:
        for item in _read(NEWS):
            if n >= limit:
                break
            if item["link"] in done:
                continue
            res = jev.ask({"headline": item["title"]}, QUESTIONS, caller="paper-lab")
            if not res:
                break   # outage or rate limit: try the rest next run
            out.write(json.dumps({"link": item["link"], "pub": item["pub"], "title": item["title"],
                                  "model": res["model"], "answers": res["answers"], "at": time.time()}) + "\n")
            n += 1
    return n


def evaluate(bars_by_inst, horizon):
    """Hit rate of Jev's direction call on market-moving headlines, by asset."""
    import bisect
    stats = {}
    for j in _read(JUDGED):
        a = j["answers"]
        inst = ASSET_TO_INST.get(a["asset"]["choice"])
        if not inst or a["direction"]["choice"] == "neutral" or a["market_moving"]["noul"] < 0.5:
            continue
        bars = bars_by_inst.get(inst) or []
        ts = [b["t"] for b in bars]
        i = bisect.bisect_left(ts, j["pub"])   # first bar starting at or after the headline
        if i + horizon >= len(bars):
            continue   # outcome not known yet
        move = bars[i + horizon]["c"] / bars[i]["o"] - 1
        hit = (move > 0) == (a["direction"]["choice"] == "up")
        s = stats.setdefault(inst, [0, 0])
        s[0] += 1
        s[1] += hit
    return {k: {"n": n, "hit_rate": round(h / n, 3)} for k, (n, h) in stats.items()}
