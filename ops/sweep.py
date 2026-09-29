#!/usr/bin/env python3
"""Daily disk sweep for the trading ecosystem (added 2026-09-29; run by watchdog.py once a day, or by hand).

  logs         any *.log over 1.5 MB in the project folders is cut to its last 300 KB (whole lines)
  screenshots  TradingView MCP screenshots older than 3 days are deleted
  candles      coin-launch-agent: finished days of pump.fun candles are packed into archive/candles/<day>.jsonl.gz,
               committed and pushed (git is the only backup); raw files are deleted 14 days later, only after the
               archive is read back. Labels are copied to archive/labels.json. `prelaunch.py restore` undoes it.
  snapshots    handled by prelaunch.py prune (14 days, packed per day) - reported here for the record

  sweep.py [--dry]   prints what it did (or would do)
"""
import glob, json, os, subprocess, sys, time

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
DRY = "--dry" in sys.argv
PROJECTS = ["flip-notifier", "coin-launch-agent", "market-lab", "market-lab/ops", "market-lab/paper-lab", "market-lab/event-desk",
            "kalshi-btc-agent", "zillow-agent", "market-iv-agent", "asset-agents", "trade-core", "portfolio-agent", "flux-lab"]
LOG_MAX, LOG_KEEP = 1_500_000, 300_000
SHOT_DIRS = [os.path.join(HOME, "tradingview-mcp", "screenshots")]
SHOT_DAYS = 3


def rotate_logs():
    freed = n = 0
    for proj in PROJECTS:
        for p in glob.glob(os.path.join(HOME, proj, "*.log")) + glob.glob(os.path.join(HOME, proj, "logs", "*.log")):
            try:
                size = os.path.getsize(p)
                if size <= LOG_MAX:
                    continue
                with open(p, "rb") as f:
                    f.seek(size - LOG_KEEP)
                    tail = f.read()
                tail = tail[tail.find(b"\n") + 1:]                 # drop the partial first line
                if not DRY:
                    with open(p, "r+b") as f:                      # truncate in place: launchd keeps its handle (O_APPEND)
                        f.truncate(0); f.write(tail)
                freed += size - len(tail); n += 1
            except OSError:
                pass
    return n, freed


def old_screenshots():
    freed = n = 0
    for d in SHOT_DIRS:
        for p in glob.glob(os.path.join(d, "*")):
            try:
                if os.path.isfile(p) and time.time() - os.path.getmtime(p) > SHOT_DAYS * 86400:
                    freed += os.path.getsize(p); n += 1
                    if not DRY:
                        os.remove(p)
            except OSError:
                pass
    return n, freed


def candles():
    if DRY:
        return "skipped (dry)"
    py = os.path.join(HOME, ".venvs", "market-ml", "bin", "python")
    env = {k: v for k, v in os.environ.items() if k != "__PYVENV_LAUNCHER__"}   # inherited from python3 it breaks the venv (no numpy)
    env["DYLD_FALLBACK_LIBRARY_PATH"] = os.path.join(HOME, ".venvs", "market-ml", "lib", "python3.11", "site-packages", "torch", "lib")
    r = subprocess.run([py, "prelaunch.py", "backup"], cwd=os.path.join(HOME, "coin-launch-agent"), capture_output=True, text=True, timeout=300, env=env)
    return (r.stdout.strip() or r.stderr.strip())[-300:]


def main():
    out = {}
    n, b = rotate_logs(); out["logs"] = f"{n} rotated, {b / 1e6:.1f} MB freed"
    n, b = old_screenshots(); out["screenshots"] = f"{n} deleted, {b / 1e6:.1f} MB freed"
    out["candles"] = candles()
    for k, v in out.items():
        print(f"{k:12} {v}")
    if not DRY:
        os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
        json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **out}, open(os.path.join(HERE, "data", "sweep.json"), "w"), indent=1)
    return out


if __name__ == "__main__":
    main()
