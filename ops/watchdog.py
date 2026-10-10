#!/usr/bin/env python3
"""Watchdog for the scheduled email agents and the flip notifier. launchd runs it every
15 minutes (com.dhruv.watchdog); it needs no Claude app.

Each pass checks what should have happened by now, repairs what it can, and emails an
alert only when repair has failed. It never duplicates an email that already went out.

  outbox        emails Gmail refused -> resend (event-desk keeps them in data/outbox)
  zillow        daily digest by 07:50 (state.json lastRun today) -> re-run, max 2/day
  watchlist     event-desk watchlist email by 09:05 -> re-run, max 2/day
  health scan   trading days, handoff by 09:58 -> re-run the 09:45 scan once
  sector scans  trading days, all handoffs by 10:03 -> re-run run-sectors.sh once
  bio email     trading days, sent by 10:45 -> catch-up send, max 2
  sector emails trading days, all sent by 10:50 -> send only the missing ones, max 2
  headless flip watcher log freshness + matrix/brief catch-up (chart watcher retired 2026-09-28)
                otherwise; alerts on BROKEN during market hours only
  calendar      alerts once if nyse_holidays.json no longer covers this year

  watchdog.py            one pass
  watchdog.py --dry      report what it would do; change nothing, send nothing
  watchdog.py --status   print the last pass's status

Status for the dashboard: ops/data/status.json. Log: ops/watchdog.log (launchd).
"""
import datetime as dt, json, os, smtplib, ssl, subprocess, sys, time
from email.header import Header
from email.mime.text import MIMEText

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import marketday  # noqa: E402

HOME = os.path.expanduser("~")
DATA = os.path.join(HERE, "data")
STATE = os.environ.get("WATCHDOG_STATE") or os.path.join(DATA, "watchdog-state.json")
STATUS = os.path.join(DATA, "status.json")
PY = os.path.join(HOME, ".venvs", "market-ml", "bin", "python")
NODE = os.path.join(HOME, ".local", "bin", "node")
DESK = os.path.join(HOME, "market-lab", "event-desk")
IV = os.path.join(HOME, "market-iv-agent")
ZILLOW = os.path.join(HOME, "zillow-agent")
FLIP = os.path.join(HOME, "flip-notifier")
MAIL = {"to": "darup67@gmail.com", "account": "darup67@gmail.com", "service": "flip-notifier-gmail"}
UID = os.getuid()
DRY = "--dry" in sys.argv
NOW = dt.datetime.fromisoformat(os.environ["WATCHDOG_NOW"]) if os.environ.get("WATCHDOG_NOW") else dt.datetime.now()
TODAY = NOW.date()


def log(msg):
    print(f"{NOW:%m-%d %H:%M} {msg}", flush=True)


def after(hhmm):
    h, m = map(int, hhmm.split(":"))
    return NOW.time() >= dt.time(h, m)


def jload(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- state
state = jload(STATE, {})
# The email and scan checks arm from the day after the first pass, so installing the
# watchdog mid-day doesn't re-send emails that ran before it existed.
ARMED_FROM = state.get("armed_from") or (TODAY + dt.timedelta(days=1)).isoformat()
if state.get("date") != TODAY.isoformat():
    state = {"date": TODAY.isoformat(), "attempts": {}, "alerted": state.get("alerted", {})}
state["armed_from"] = ARMED_FROM
ARMED = TODAY.isoformat() >= ARMED_FROM
status = []


def note(check, level, msg):
    status.append({"check": check, "level": level, "msg": msg})
    if level != "ok":
        log(f"{level.upper():5} {check}: {msg}")


def attempt(key, limit):
    """True (and counts it) if `key` has been tried fewer than `limit` times today."""
    n = state["attempts"].get(key, 0)
    if n >= limit:
        return False
    state["attempts"][key] = n + 1
    return True


# ---------------------------------------------------------------- launchd
def job(label):
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        p = line.split()
        if len(p) == 3 and p[2] == label:
            return {"loaded": True, "running": p[0] != "-", "exit": p[1]}
    return {"loaded": False, "running": False, "exit": None}


def kick(label):
    """Start a launchd job now. Loads it first if it was unloaded."""
    if DRY:
        return log(f"DRY would kickstart {label}")
    plist = os.path.join(HOME, "Library", "LaunchAgents", f"{label}.plist")
    if not job(label)["loaded"] and os.path.exists(plist):
        subprocess.run(["launchctl", "bootstrap", f"gui/{UID}", plist], capture_output=True)
    r = subprocess.run(["launchctl", "kickstart", f"gui/{UID}/{label}"], capture_output=True, text=True)
    log(f"kickstart {label}: {'ok' if r.returncode == 0 else r.stderr.strip()}")


def spawn(args, logname):
    """Run a catch-up in the background so this pass stays quick."""
    if DRY:
        return log(f"DRY would run {' '.join(args)}")
    with open(os.path.join(HERE, logname), "a") as out:
        subprocess.Popen(args, cwd=DESK, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    log(f"started: {' '.join(args[1:])}")


# ---------------------------------------------------------------- alerts
SYSTEM = {   # alert key -> (exact title, what the watchdog already did about it)
    "outbox": "Emails Gmail refused are waiting in the outbox. The watchdog retries them each pass; this alert means the retries keep failing.",
    "zillow": "The daily apartment digest has not run. The watchdog re-runs it (up to 2 times a day); this alert means the re-runs failed.",
    "watchlist": "The watchlist briefing did not go out by 09:05. The watchdog re-runs it (up to 2 times a day).",
    "bio-email": "The bio/pharma digest did not go out by 10:45. The watchdog sends a catch-up (up to 2 tries).",
    "sector-emails": "Some sector digests did not go out by 10:50. The watchdog sends only the missing ones (up to 2 tries).",
    "headless-flip": "The flip watcher's log is stale or reports a fatal error, so trend flips may be missed.",
    "crypto-scan": "The crypto scanner's log is stale or reports a fatal error, so crypto signals may be missed.",
    "agents-failing": "Background jobs exited with an error on consecutive watchdog passes.",
    "platform": "A Python or Node dependency, pin or import check failed. Jobs that depend on it may fail at their next run.",
    "git-storage": "A GitHub repo is growing toward its size limit. Git is the only backup, so this needs a decision.",
    "coin-watcher": "The original coin watcher is not writing fresh data.",
    "prelaunch": "The pre-graduation collector is not writing fresh data.",
    "listed": "The Coinbase/Robinhood listed-coin board is not updating.",
    "sentiment": "The social sentiment refresh is not writing readings, so emails will show stale or missing sentiment.",
    "calendar": "The NYSE holiday list has no dates for this year, so trading-day checks are unreliable.",
}


def alert_spec(key, subject, body, good=False):
    """Layout for a system email: exact title, what happened, the raw details, what the watchdog already did."""
    title = subject.replace("✅ ", "")
    paras = [p for p in str(body).strip().split("\n\n") if p.strip()]
    first, rest = (paras[0] if paras else ""), "\n\n".join(paras[1:])
    secs = []
    if first:
        (secs.append({"title": "Details", "blocks": [{"type": "code" if "\n" in first or "/" in first else "para", "text": first}]}))
    if rest:
        secs.append({"title": "Log excerpt", "blocks": [{"type": "code", "text": rest[-3000:]}]})
    secs.append({"title": "What the watchdog has done", "blocks": [{"type": "para", "text": SYSTEM.get(key, "The watchdog retries repairable problems itself and emails only when repair has failed.") +
                 " It will not repeat this email for several hours. Ask Claude to \"check health of all projects\" for a full check."}]})
    return {"kind": "System alert" if not good else "Daily delivery report", "status": {"text": "ALL OK" if good else "NEEDS ATTENTION", "tone": "good" if good else "bad"},
            "title": title, "subtitle": f"Agent Watchdog · {dt.datetime.now():%a %b %-d, %-I:%M %p} ET", "sections": secs, "footer": "Sent by the Agent Watchdog (ops/watchdog.py, every 15 minutes)."}


def alert(key, subject, body, every_hours=6, spec=None):
    """Email once per `key` per `every_hours`; a Mac notification if Gmail itself fails."""
    last = state["alerted"].get(key, 0)
    if time.time() - last < every_hours * 3600:
        return
    state["alerted"][key] = time.time()
    if DRY:
        return log(f"DRY would alert: {subject}")
    ok = False
    try:
        sys.path.insert(0, os.path.expanduser("~/flip-notifier"))
        import email_ui
        good = subject.startswith("✅")
        clean = subject.replace("✅ ", "")
        ok = email_ui.send(f"Watchdog · Daily delivery report: {clean}" if key == "daily-confirmation" else f"Watchdog · System alert: {clean}",
                           spec or alert_spec(key, subject, body, good))
    except Exception as ex:
        log(f"alert email failed: {ex!r}")
    if not ok:
        # notification previews are off on this Mac: the text has to be in the title
        subprocess.run(["osascript", "-e", f'display notification "" with title "⚠️ {subject[:90]}" sound name "Basso"'],
                       capture_output=True)
    log(f"ALERT {'emailed' if ok else 'notified'}: {subject}")


# ---------------------------------------------------------------- event-desk records
def sent_today():
    out = set()
    try:
        with open(os.path.join(DESK, "data", "sent.jsonl")) as f:
            for line in f:
                r = json.loads(line)
                if r["ok"] and not r.get("test") and dt.date.fromtimestamp(r["t"]) == TODAY:
                    out.add(r["email"])
    except (OSError, ValueError):
        pass
    return out


def handoff_today(folder):
    h = jload(os.path.join(folder, "handoff", "handoff.json"), {})
    return h.get("date") == TODAY.isoformat()


def sector_keys():
    d = os.path.join(IV, "profiles")
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


# ---------------------------------------------------------------- checks
def check_outbox():
    box = os.path.join(DESK, "data", "outbox")
    n = len([f for f in os.listdir(box) if f.endswith(".json")]) if os.path.isdir(box) else 0
    if not n:
        return note("outbox", "ok", "empty")
    note("outbox", "warn", f"{n} email(s) waiting to resend")
    if not DRY:
        subprocess.run([PY, "desk.py", "outbox"], cwd=DESK, capture_output=True, timeout=300)
    left = len([f for f in os.listdir(box) if f.endswith(".json")]) if not DRY else n
    if left and after("12:00"):
        alert("outbox", f"{left} email(s) stuck in the outbox",
              "Gmail keeps refusing them. Check the app password (Keychain flip-notifier-gmail) "
              f"and ~/market-lab/event-desk/data/outbox/.")


def check_zillow():
    if not after("07:50"):
        return
    last = jload(os.path.join(ZILLOW, "state.json"), {}).get("lastRun")
    ran = last and dt.datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone().date() == TODAY
    if ran:
        return note("zillow", "ok", "digest ran today")
    if job("com.dhruv.zillowagent")["running"]:
        return note("zillow", "warn", "running now")
    if attempt("zillow", 2):
        note("zillow", "warn", "no run today; re-running")
        kick("com.dhruv.zillowagent")
    else:
        note("zillow", "fail", "no successful run today after 2 retries")
        if after("09:00"):
            alert("zillow", "Zillow digest did not run today",
                  "Two re-runs failed. Last lines of ~/zillow-agent/zillow-agent.log:\n\n" + tail(os.path.join(ZILLOW, "zillow-agent.log")))


def check_watchlist():
    if not after("09:05"):
        return
    if "watchlist" in sent_today():
        return note("watchlist email", "ok", "sent today")
    if job("com.dhruv.eventdesk.watchlist")["running"]:
        return note("watchlist email", "warn", "running now")
    if attempt("watchlist", 2):
        note("watchlist email", "warn", "not sent; re-running")
        kick("com.dhruv.eventdesk.watchlist")
    else:
        note("watchlist email", "fail", "not sent after 2 retries")
        if after("10:00"):
            alert("watchlist", "Watchlist email not sent today",
                  tail(os.path.join(DESK, "watchlist.out.log")) + "\n" + tail(os.path.join(DESK, "watchlist.err.log")))


def check_scans():
    if after("11:02"):
        if handoff_today(os.path.join(IV, "data")):
            note("health care scan", "ok", "done")
        elif not job("com.dhruv.healthiv.open")["running"] and attempt("health-scan", 1):
            note("health care scan", "warn", "no scan today; re-running")
            kick("com.dhruv.healthiv.open")
        else:
            note("health care scan", "warn", "not done yet")
    if after("11:06"):
        missing = [k for k in sector_keys() if not handoff_today(os.path.join(IV, "data", "sectors", k))]
        if not missing:
            note("sector scans", "ok", "all 10 done")
        elif not job("com.dhruv.sectoriv.open")["running"] and attempt("sector-scan", 1):
            note("sector scans", "warn", f"missing {', '.join(missing)}; re-running all sectors")
            kick("com.dhruv.sectoriv.open")
        else:
            note("sector scans", "warn", f"missing {', '.join(missing)}")


def check_market_emails():
    done = sent_today()
    if after("11:45"):
        if "biopharma" in done:
            note("bio/pharma email", "ok", "sent today")
        elif job("com.dhruv.eventdesk.bio")["running"]:
            note("bio/pharma email", "warn", "running now")
        elif attempt("bio-email", 2):
            note("bio/pharma email", "warn", "not sent; catch-up send")
            spawn([PY, "desk.py", "biopharma", "--no-wait"], "catchup.log")
        else:
            note("bio/pharma email", "fail", "not sent after 2 catch-ups")
            if after("12:30"):
                alert("bio-email", "Bio/pharma email not sent today", tail(os.path.join(DESK, "biopharma.out.log")))
    if after("11:50"):
        missing = [k for k in sector_keys() if k not in done]
        if not missing:
            note("sector emails", "ok", "all 10 sent")
        elif job("com.dhruv.eventdesk.sectors")["running"]:
            note("sector emails", "warn", f"running now ({len(missing)} to go)")
        elif attempt("sector-emails", 2):
            note("sector emails", "warn", f"missing {', '.join(missing)}; sending just those")
            spawn([PY, "desk.py", "sectors", "--missing", "--no-wait"], "catchup.log")
        else:
            note("sector emails", "fail", f"still missing {', '.join(missing)}")
            if after("12:45"):
                alert("sector-emails", f"{len(missing)} sector email(s) not sent today", ", ".join(missing) + "\n\n" +
                      tail(os.path.join(DESK, "sectors.out.log")))


def check_flip(trading):
    market_hours = trading and after("09:15") and not after("16:15")
    if not market_hours and NOW.minute >= 15:
        return   # off hours: hourly is enough
    if DRY:
        return note("headless flip", "ok", "DRY: not checked")
    # The chart-based flip watcher was retired 2026-09-28; the headless watcher is the only one.
    log = os.path.join(FLIP, "headless-flip.log")
    try:
        runs = [l for l in open(log).read().rstrip().split("\n") if " MATRIX " not in l]
        last = runs[-1]   # ignore matrix-report lines; judge the flip runs
        age = time.time() - dt.datetime.fromisoformat(last.split()[0].replace("Z", "+00:00")).timestamp()
    except OSError:
        last, age = "no log", 1e9
    if age > 40 * 60 or "FATAL" in last or "mode live" not in last:
        note("headless flip", "fail", f"last run {int(age // 60)}m ago: {last[-160:]}")
        alert("headless-flip", "Headless flip watcher not running cleanly",
              f"Last log line ({int(age // 60)} min old):\n{last}\n\n~/flip-notifier/headless-flip.log", every_hours=2)
    elif " ERR " in last:
        note("headless flip", "warn", last.split(" · ERR ")[-1][:160])
    else:
        note("headless flip", "ok", last.split("  ", 1)[-1][:120])
    # Crypto scanner (crypto-scan.js, every 15 min): its log must be fresh.
    try:
        cl = open(os.path.join(FLIP, "crypto-scan.log")).read().rstrip().split("\n")[-1]
        cage = time.time() - dt.datetime.fromisoformat(cl.split()[0].replace("Z", "+00:00")).timestamp()
        if cage > 40 * 60 or "FATAL" in cl:
            note("crypto scan", "fail", f"last run {int(cage // 60)}m ago: {cl[-140:]}")
            alert("crypto-scan", "Crypto scanner not running cleanly", f"Last log line ({int(cage // 60)} min old):\n{cl}", every_hours=2)
        else:
            note("crypto scan", "ok", cl.split("  ", 1)[-1][:120])
    except OSError:
        note("crypto scan", "warn", "no crypto-scan.log yet")
    # Briefs at 08:55 and 16:30 daily: re-send if the latest slot was missed.
    slots = [s for s in ("08:55", "16:30") if after(s)]
    if slots and NOW.hour * 60 + NOW.minute - int(slots[-1][:2]) * 60 - int(slots[-1][3:]) >= 15:
        slot = dt.datetime.combine(TODAY, dt.time(int(slots[-1][:2]), int(slots[-1][3:])))
        try:
            sent = dt.datetime.fromisoformat(json.load(open(os.path.join(FLIP, "headless-matrix.json")))["sentAt"].replace("Z", "+00:00"))
            sent = sent.astimezone().replace(tzinfo=None)
        except (OSError, ValueError, KeyError):
            sent = dt.datetime.min
        if sent < slot:
            note("flip matrix", "warn", f"{slots[-1]} report missing — re-sending")
            subprocess.run([NODE, "headless-flip.js", "--matrix"], cwd=FLIP, capture_output=True, timeout=120)
        else:
            note("flip matrix", "ok", f"{slots[-1]} report sent {sent:%H:%M}")
    return


# ---------- platform checks (added 2026-09-28 after the coin-launch scorer failed silently for 2 days) ----------

DEPS = os.path.join(HERE, "deps")
VENVS = {   # name: (python, extra env the LaunchAgents set)
    "market-ml": (os.path.join(HOME, ".venvs", "market-ml", "bin", "python"),
                  {"DYLD_FALLBACK_LIBRARY_PATH": os.path.join(HOME, ".venvs/market-ml/lib/python3.11/site-packages/torch/lib")}),
    "jev-desk": (os.path.join(HOME, "jev-desk", ".venv", "bin", "python"), {}),
    "jev-majors": (os.path.join(HOME, "jev-majors", ".venv", "bin", "python"), {}),
    "jev-markets": (os.path.join(HOME, "jev-markets", ".venv", "bin", "python"), {}),
}
PINS = {    # symlink: exact target it must resolve to
    os.path.join(HOME, ".local", "bin", "node"): os.path.join(HOME, ".local/opt/node-v22.22.3/bin/node"),
    os.path.join(HOME, ".venvs", "market-ml", "bin", "python"): os.path.join(HOME, ".local/share/uv/python/cpython-3.11.15-macos-aarch64-none/bin/python3.11"),
    os.path.join(HOME, "jev-desk", ".venv", "bin", "python"): os.path.join(HOME, ".local/share/uv/python/cpython-3.11.15-macos-aarch64-none/bin/python3.11"),
    os.path.join(HOME, "jev-majors", ".venv", "bin", "python"): os.path.join(HOME, ".local/share/uv/python/cpython-3.11.15-macos-aarch64-none/bin/python3.11"),
    os.path.join(HOME, "jev-markets", ".venv", "bin", "python"): os.path.join(HOME, ".local/share/uv/python/cpython-3.11.15-macos-aarch64-none/bin/python3.11"),
}
KEEPALIVE_OK = {-15, -9}   # a long-running agent restarted by launchd reports its predecessor's signal


def check_agents():
    """Any com.dhruv.* LaunchAgent that exited non-zero on two separate runs in a row."""
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    fails, seen = {}, state.setdefault("agent_fail", {})
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[2].startswith("com.dhruv."):
            continue
        pid, code, label = parts
        try:
            code = int(code)
        except ValueError:
            continue
        if code == 0 or (pid != "-" and code in KEEPALIVE_OK):
            seen.pop(label, None)
            continue
        # Count failed RUNS, not watchdog passes: an hourly job's single blip (e.g. a 40 s network
        # outage, futures 2026-09-28 19:30) stays "exit 1" across several 15-minute passes.
        runs = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, text=True).stdout
        n = next((int(l.split("=")[1]) for l in runs.splitlines() if l.strip().startswith("runs =")), None)
        rec = seen.get(label) if isinstance(seen.get(label), dict) else {"runs": [], "code": code}
        if n is not None and n not in rec["runs"]:
            rec["runs"] = (rec["runs"] + [n])[-5:]
        rec["code"] = code
        seen[label] = rec
        if len(rec["runs"]) >= 2:
            fails[label] = code
    if fails:
        desc = ", ".join(f"{k.replace('com.dhruv.', '')} (exit {v})" for k, v in sorted(fails.items()))
        note("launch agents", "fail", desc)
        alert("agents-failing", f"{len(fails)} background job(s) failing", "Last exit non-zero on consecutive watchdog passes:\n\n" +
              "\n".join(f"{k}: exit {v}" for k, v in sorted(fails.items())) + "\n\nCheck each job's err.log.", every_hours=6)
    else:
        note("launch agents", "ok", "all com.dhruv.* last exits clean")


def check_platform():
    """Hourly: pinned interpreters still resolve and disk has room; daily (07:00): every venv package still imports."""
    if NOW.minute >= 15 and not os.environ.get("WATCHDOG_FORCE_PLATFORM"):
        return
    problems = []
    for link, want in PINS.items():
        got = os.path.realpath(link)
        if got != os.path.realpath(want) or not os.path.exists(got):
            problems.append(f"{link} -> {got} (pinned {want})")
    # The full import check costs ~40 CPU-s and ~0.5 GB: once a day from 07:00 (or when forced).
    imports_due = os.environ.get("WATCHDOG_FORCE_PLATFORM") or (after("07:00") and state.get("imports_checked") != TODAY.isoformat())
    for name, (py, env) in (VENVS.items() if imports_due else []):
        base_f = os.path.join(DEPS, f"{name}.import-baseline.json")
        try:
            r = subprocess.run([py, os.path.join(DEPS, "import_check.py"), "--json"], capture_output=True, text=True,
                               timeout=900, env={**os.environ, **env})
            res = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception as ex:
            problems.append(f"{name}: import check could not run ({ex!r})")
            continue
        now_fail = {f"{f['dist']}:{f['module']}": f["error"] for f in res["fails"]}
        if not os.path.exists(base_f):
            save(base_f, sorted(now_fail))   # first run records today's known-harmless failures
            continue
        new = {k: v for k, v in now_fail.items() if k not in set(jload(base_f, []))}
        problems += [f"{name}: {k} now fails: {v}" for k, v in new.items()]
    if imports_due:
        state["imports_checked"] = TODAY.isoformat()
    free_gb = os.statvfs(HOME).f_bavail * os.statvfs(HOME).f_frsize / 1e9
    if free_gb < 15:
        problems.append(f"disk: only {free_gb:.0f} GB free")
    if problems:
        note("platform", "fail", "; ".join(problems)[:300])
        alert("platform", "Dependency / platform problem", "\n".join(problems) +
              "\n\nPins and lock files: ~/market-lab/ops/deps/ (README.md has the restore steps).", every_hours=6)
    else:
        note("platform", "ok", f"pins intact, imports {'match baseline' if imports_due else 'checked daily'}, {free_gb:.0f} GB free")


REPOS = ["asset-agents", "coin-launch-agent", "flip-notifier", "flux-lab", "jev-client", "jev-desk", "jev-majors", "jev-markets", "kalshi-btc-agent", "kalshi-btc-1h-agent", "kalshi-commodity-agent",
         "market-iv-agent", "market-lab", "portfolio-agent", "trade-core", "zillow-agent"]
GIT = "/usr/local/bin/git"   # absolute: /usr/bin/git is the CLT stub and pops an install dialog


def check_git_storage():
    """Daily (from 07:00): GitHub-reported size per repo (warn at 500 MB; GitHub recommends < 1 GB and
    strongly < 5 GB), largest tracked file (warn at 25 MB; GitHub rejects > 100 MB), and a local
    `git gc` when a repo's .git passes 300 MB of loose objects (market-lab hit 449 MB on 2026-09-28)."""
    if not after("07:00") or state.get("git_storage_checked") == TODAY.isoformat():
        return
    state["git_storage_checked"] = TODAY.isoformat()
    try:
        cred = subprocess.run([GIT, "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                              capture_output=True, text=True, timeout=20).stdout
        token = next((l[9:] for l in cred.splitlines() if l.startswith("password=")), None)
    except Exception:
        token = None
    problems, biggest_repo, biggest_file = [], ("", 0), ("", 0)
    for r in REPOS:
        d = os.path.join(HOME, r)
        if not os.path.isdir(os.path.join(d, ".git")):
            continue
        # largest tracked file
        try:
            files = subprocess.run([GIT, "ls-files", "-z"], cwd=d, capture_output=True, text=True, timeout=30).stdout.split("\0")
            sizes = [(f, os.path.getsize(os.path.join(d, f))) for f in files if f and os.path.isfile(os.path.join(d, f))]
            f, sz = max(sizes, key=lambda x: x[1]) if sizes else ("", 0)
            if sz > biggest_file[1]:
                biggest_file = (f"{r}/{f}", sz)
            if sz > 25 * 2**20:
                problems.append(f"{r}: tracked file {f} is {sz / 2**20:.0f} MB (GitHub warns at 50, rejects over 100)")
        except Exception as ex:
            problems.append(f"{r}: file-size check failed ({ex!r})")
        # GitHub-reported repo size
        if token:
            try:
                url = subprocess.run([GIT, "remote", "get-url", "origin"], cwd=d, capture_output=True, text=True, timeout=10).stdout.strip()
                name = url.split("github.com/")[-1].split("github.com:")[-1].removesuffix(".git")
                import urllib.request
                req = urllib.request.Request(f"https://api.github.com/repos/{name}", headers={"Authorization": f"token {token}"})
                mb = json.load(urllib.request.urlopen(req, timeout=20))["size"] / 1024
                if mb > biggest_repo[1]:
                    biggest_repo = (r, mb)
                if mb > 500:
                    problems.append(f"{r}: {mb:.0f} MB on GitHub (recommended < 1 GB, strongly < 5 GB)")
            except Exception:
                pass
        # local loose objects
        try:
            out = subprocess.run([GIT, "count-objects", "-v"], cwd=d, capture_output=True, text=True, timeout=30).stdout
            loose_kb = int(next(l.split()[1] for l in out.splitlines() if l.startswith("size:")))
            if loose_kb > 300 * 1024:
                subprocess.run([GIT, "gc", "-q"], cwd=d, capture_output=True, timeout=600)
                note("git storage", "ok", f"{r}: packed {loose_kb // 1024} MB of loose objects locally")
        except Exception:
            pass
    if problems:
        note("git storage", "warn", "; ".join(problems)[:300])
        alert("git-storage", "Git repo size warning", "\n".join(problems) +
              "\n\nOptions: archive old data into release assets, split data into its own repo, or move bulk data out of git.", every_hours=24 * 7)
    else:
        note("git storage", "ok", f"largest repo {biggest_repo[0]} {biggest_repo[1]:.0f} MB on GitHub; largest file {biggest_file[0]} {biggest_file[1] / 2**20:.1f} MB")


def check_prelaunch():
    """Pre-graduation collector (coin-launch prelaunch.py tick, every 5 min): the board file must be fresh."""
    if os.path.exists(os.path.join(HOME, "coin-launch-agent", "PAUSED.md")):
        return note("coin detector", "ok", "paused by the user (coin-launch-agent/PAUSED.md); Jev desk is the successor")
    cfg = json.load(open(os.path.join(HOME, "coin-launch-agent", "config.json")))
    if os.path.exists(os.path.join(HOME, "Library", "LaunchAgents", "com.dhruv.coinlaunch.pre.plist.disabled")):
        # ML add-ons (pre-graduation collector, plus50) unloaded 2026-09-29; the ORIGINAL watcher stays on with its original parameters
        try:
            age = time.time() - os.path.getmtime(os.path.join(HOME, "coin-launch-agent", "data", "live.json"))
        except OSError:
            return note("coin watcher", "warn", "no live.json yet")
        if age > 20 * 60:
            note("coin watcher", "fail", f"live.json {int(age // 60)}m old")
            alert("coin-watcher", "Coin watcher not updating", f"~/coin-launch-agent/data/live.json is {int(age // 60)} minutes old (com.dhruv.coinlaunch, KeepAlive).", every_hours=3)
        else:
            note("coin watcher", "ok", "original watcher live; ML add-ons paused")
        return
    f = os.path.join(HOME, "coin-launch-agent", "data", "pre", "board.json")
    try:
        age = time.time() - os.path.getmtime(f)
    except OSError:
        return note("prelaunch", "warn", "no board yet")
    if age > 40 * 60:
        note("prelaunch", "fail", f"board {int(age // 60)}m old")
        alert("prelaunch", "Pre-graduation collector not running", f"~/coin-launch-agent/data/pre/board.json is {int(age // 60)} minutes old (runs every 5 min).", every_hours=3)
    else:
        b = json.load(open(f))
        note("prelaunch", "ok", f"{len(b.get('rows', []))} on board, {b.get('labeled', 0)} labeled coins")


def check_listed():
    """Coinbase/Robinhood-listed coin board (coin-launch-agent listed.py, every 15 min): must be fresh."""
    if os.path.exists(os.path.join(HOME, "coin-launch-agent", "PAUSED.md")):
        return                                       # reported once, under "coin detector"
    f = os.path.join(HOME, "coin-launch-agent", "data", "listed_board.json")
    try:
        b = json.load(open(f))
    except (OSError, ValueError):
        return note("listed board", "warn", "no board yet")
    age = time.time() - b["updated"]
    if age > 50 * 60:
        note("listed board", "fail", f"board {int(age // 60)}m old")
        alert("listed", "Listed-coin board not updating", f"~/coin-launch-agent/data/listed_board.json is {int(age // 60)} minutes old (runs every 15 min).", every_hours=3)
    else:
        c = b["counts"]
        note("listed board", "ok", f"{b['universe']} scored, {c['clean']} clean (fast+steady+holding)")


def check_jevdesk():
    """Jev memecoin desk (~/jev-desk, com.dhruv.jevdesk.main, KeepAlive loop ticking every 60 s).
    Heartbeat must be fresh; scans must keep coming while nothing is held; in live mode a RISK
    sell that keeps failing is urgent (money is stuck in a token). Repair = kickstart the job."""
    base = os.path.join(HOME, "jev-desk")
    if not os.path.isdir(base):
        return
    if os.path.exists(os.path.join(base, "STOPPED")):                       # user stopped the desk (2026-10-09): do not reload it
        return note("jev desk", "ok", "stopped by the user (~/jev-desk/STOPPED); delete that file and re-bootstrap the plists to restart")
    label = "com.dhruv.jevdesk.main"
    if not job(label)["loaded"]:
        kick(label)
        alert("jevdesk-unloaded", "Jev desk was not loaded", f"{label} was not loaded in launchd; the watchdog tried to load it.", every_hours=6)
        return note("jev desk", "fail", "job was unloaded; reloaded")
    hb = jload(os.path.join(base, "data", "heartbeat.json"), None)
    age = time.time() - hb["t"] if hb else None
    if age is None or age > 10 * 60:
        if attempt("jevdesk-kick", 4):
            kick(label)
        alert("jevdesk-stale", "Jev desk loop not ticking",
              f"~/jev-desk/data/heartbeat.json is {'missing' if age is None else f'{int(age // 60)} min old'} (ticks every 60 s). "
              "The watchdog kickstarted com.dhruv.jevdesk.main.\n\nSee ~/jev-desk/main.err.log.", every_hours=3)
        return note("jev desk", "fail", f"heartbeat {'missing' if age is None else f'{int(age // 60)}m old'}; kicked")
    mode = hb.get("mode")
    positions = hb.get("positions") if "positions" in hb else ([hb["held"]] if hb.get("held") else [])
    if hb.get("offline"):                             # the Mac has no internet: the desk is deliberately paused, not broken
        return note("jev desk", "warn", f"{mode}; Mac offline since {time.strftime('%H:%M', time.localtime(hb.get('offline_since') or time.time()))}: entries paused, positions held")
    scan_alive = time.time() - (hb.get("scan_thread_at") or time.time())
    if scan_alive > 15 * 60:                          # exits still tick but the scan thread is hung
        if attempt("jevdesk-scan-kick", 4):
            kick(label)
        alert("jevdesk-scanthread", "Jev desk scan thread hung", f"The scan thread has not looped for {int(scan_alive // 60)} min "
              "while exits kept running. The watchdog kickstarted com.dhruv.jevdesk.main.", every_hours=3)
        return note("jev desk", "fail", f"scan thread silent {int(scan_alive // 60)}m; kicked")
    low = [ch for ch, v in (hb.get("gas") or {}).items() if v is not None and v < 2 * (hb.get("min_gas") or {}).get(ch, 0)]
    if low:
        alert("jevdesk-gas", f"Jev desk wallet low on gas ({', '.join(low)})",
              "Fee balance below twice the minimum: " + ", ".join(f"{ch} {hb['gas'][ch]}" for ch in low) +
              ". Live buys stop at the minimum and live SELLS need gas too. Top up SOL / ETH (Base) from Coinbase.",
              every_hours=12 if mode != "live" else 2)
    if hb.get("breaker"):
        alert("jevdesk-breaker", "Jev desk circuit breaker tripped",
              f"{hb['breaker'].get('mode')} drawdown ${hb['breaker'].get('drawdown', 0):.2f} hit max_drawdown_usd. No new trades; open "
              "positions still exit normally. Resume: cd ~/jev-desk && .venv/bin/python main.py reset-breaker", every_hours=12)
    if (hb.get("errors_last_hour") or 0) >= 20:
        alert("jevdesk-errors", f"Jev desk: {hb['errors_last_hour']} errors in the last hour",
              "See ~/jev-desk/data/errors.jsonl and main.err.log.", every_hours=3)
    stuck = [p for p in positions if not p.get("shadow") and p.get("sell_fails", 0) >= 3]
    if stuck:
        p = stuck[0]
        alert("jevdesk-sell", f"Jev desk cannot sell {p['ticker']}",
              f"Live position {p['ticker']} on {p['chain']}: the exit sell has failed {p['sell_fails']} times "
              f"(retrying every 15 s). Check ~/jev-desk/main.err.log; manual: cd ~/jev-desk && .venv/bin/python main.py sell-now {p['ticker']}",
              every_hours=1)
        return note("jev desk", "fail", f"live sell of {p['ticker']} failing x{p['sell_fails']}")
    scan_age = time.time() - (hb.get("last_scan") or 0)
    if len(positions) < hb.get("max_positions", 1) and scan_age > 30 * 60 and not hb.get("breaker") and not hb.get("loss_stop_today"):
        note("jev desk", "fail", f"no scan for {int(scan_age // 60)}m")
        alert("jevdesk-scan", "Jev desk stopped scanning", f"Last scan {int(scan_age // 60)} minutes ago (every 5 min while there is room "
              "for a position). If live, today's loss limit may have been hit (main.py status).", every_hours=6)
        return
    if not subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "typesafe-jev"], capture_output=True).returncode == 0:
        return note("jev desk", "warn", f"{mode}; no Jev key yet, so no picks (python3 ~/jev-client/jev.py --set-key)")
    held = ", ".join(f"{p['ticker']} {p['pct']:+.0%}" if p.get("pct") is not None else p["ticker"] for p in positions)
    if hb.get("breaker") or low or (hb.get("errors_last_hour") or 0) >= 20:
        return note("jev desk", "warn", f"{mode}; breaker={bool(hb.get('breaker'))} low_gas={low} errors/h={hb.get('errors_last_hour')}")
    note("jev desk", "ok", f"{mode}; {len(positions)} position(s){': ' + held if held else ''}; last scan {int(scan_age // 60)}m ago")

def check_jevdesk_grads():
    """Live pump.fun graduation feed (~/jev-desk/grads.py, com.dhruv.jevdesk.grads, KeepAlive, Helius websocket).
    Heartbeat every 30 s; graduations never stop for an hour, so a connected feed with none means it is deaf."""
    base = os.path.join(HOME, "jev-desk")
    label = "com.dhruv.jevdesk.grads"
    if os.path.exists(os.path.join(base, "STOPPED")):
        return
    if not os.path.exists(os.path.join(HOME, "Library", "LaunchAgents", f"{label}.plist")):
        return
    if not job(label)["loaded"]:
        kick(label)
        alert("jevgrads-unloaded", "Jev desk graduation feed was not loaded", f"{label} was not loaded; the watchdog tried to load it.", every_hours=6)
        return note("jev grads", "fail", "job was unloaded; reloaded")
    hb = jload(os.path.join(base, "data", "grads.json"), None)
    age = time.time() - hb["t"] if hb else None
    if age is None or age > 5 * 60 or not hb.get("connected"):
        if attempt("jevgrads-kick", 6):
            kick(label)
        alert("jevgrads-stale", "Jev desk graduation feed down",
              f"~/jev-desk/data/grads.json: {'missing' if age is None else f'{int(age)} s old'}, connected={hb and hb.get('connected')}. "
              "Kickstarted com.dhruv.jevdesk.grads. See ~/jev-desk/grads.err.log.", every_hours=3)
        return note("jev grads", "fail", "feed down; kicked")
    up = time.time() - hb.get("up_since", time.time())
    if hb["graduations_last_hour"] == 0 and up > 3600:
        note("jev grads", "warn", "connected but no graduations in the last hour")
        alert("jevgrads-deaf", "Jev desk graduation feed hears nothing",
              "Connected for over an hour with zero pump.fun graduations (normally ~50/hour). The migration account "
              "may have changed, or the Helius key/plan stopped delivering. ~/jev-desk/grads.py --status", every_hours=6)
        return
    note("jev grads", "ok", f"{hb['graduations_last_hour']} graduations/hour via {hb['provider']}")


def check_jevmajors():
    """Jev Majors (~/jev-majors): Hyperliquid long/short perps desk. Jobs loaded, heartbeat and live data fresh, scan thread
    alive, no breaker, errors low. Repair = kickstart the job. Entirely separate from check_jevdesk."""
    base = os.path.join(HOME, "jev-majors")
    if not os.path.isdir(base):
        return
    for label in ("com.dhruv.jevmajors.main", "com.dhruv.jevmajors.dashboard"):
        if not job(label)["loaded"]:
            kick(label)
            alert("jevmajors-unloaded", "Jev Majors job was not loaded", f"{label} was not loaded in launchd; the watchdog tried to load it.", every_hours=6)
            return note("jev majors", "fail", f"{label.split('.')[-1]} was unloaded; reloaded")
    mp = jload(os.path.join(base, "data", "majpat.json"), None)
    if mp and time.time() - mp.get("updated", 0) > 50 * 3600:
        alert("jevmajors-lab", "Jev Majors research job is stale", "data/majpat.json is over 50 h old; the 06:50 job com.dhruv.jevmajors.lab may be failing. See ~/jev-majors/lab.err.log. Run: cd ~/jev-majors && ./run_lab.sh", every_hours=24)
    hb = jload(os.path.join(base, "data", "heartbeat.json"), None)
    age = time.time() - hb["t"] if hb else None
    if age is None or age > 10 * 60:
        if attempt("jevmajors-kick", 4):
            kick("com.dhruv.jevmajors.main")
        alert("jevmajors-stale", "Jev Majors loop not ticking", f"~/jev-majors/data/heartbeat.json is {'missing' if age is None else f'{int(age // 60)} min old'}. Kickstarted com.dhruv.jevmajors.main; see ~/jev-majors/main.err.log.", every_hours=3)
        return note("jev majors", "fail", "heartbeat stale; kicked")
    if hb.get("offline"):
        return note("jev majors", "warn", f"{hb.get('mode')}; Mac offline: entries paused")
    scan_alive = time.time() - (hb.get("scan_thread_at") or time.time())
    if scan_alive > 15 * 60:
        if attempt("jevmajors-scan-kick", 4):
            kick("com.dhruv.jevmajors.main")
        alert("jevmajors-scanthread", "Jev Majors scan thread hung", f"No scan-loop pass for {int(scan_alive // 60)} min; kickstarted.", every_hours=3)
        return note("jev majors", "fail", f"scan thread silent {int(scan_alive // 60)}m; kicked")
    live = jload(os.path.join(base, "data", "live.json"), {})
    if time.time() - live.get("t", 0) > 180:
        return note("jev majors", "fail", "market snapshot stale: the Hyperliquid feed is not updating")
    if hb.get("breaker"):
        alert("jevmajors-breaker", "Jev Majors circuit breaker tripped", "Drawdown limit hit; no new trades. Resume: cd ~/jev-majors && .venv/bin/python main.py reset-breaker", every_hours=12)
    if (hb.get("errors_last_hour") or 0) >= 20:
        alert("jevmajors-errors", f"Jev Majors: {hb['errors_last_hour']} errors in the last hour", "See ~/jev-majors/data/errors.jsonl and main.err.log.", every_hours=3)
    nw = jload(os.path.join(base, "data", "news.json"), None)
    if nw and nw.get("feeds") and time.time() - nw.get("t", 0) < 3600 and all(v <= 0 for v in nw["feeds"].values()):
        alert("jevmajors-news", "Jev Majors news feeds all failing", "all four RSS feeds returned nothing; the news veto is off (entries still run). See ~/jev-majors/data/errors.jsonl.", every_hours=12)
    sat = [p for p in hb.get("positions", []) if not p.get("shadow")]
    note("jev majors", "warn" if hb.get("breaker") else "ok", f"{hb.get('mode')}; {len(hb.get('positions', []))} position(s) ({len(sat)} live); last scan {int((time.time() - (hb.get('last_scan') or 0)) / 60)}m ago")


def check_ab():
    """A/B guardrail test (ops/ab_report.py): record both arms hourly into each repo's export/ store, and make sure the guarded copies are alive."""
    cfgp = os.path.join(DATA, "ab_config.json")
    if not os.path.exists(cfgp):
        return
    last = os.path.getmtime(os.path.join(HOME, "jev-markets", "export", "ab_summary.json")) if os.path.exists(os.path.join(HOME, "jev-markets", "export", "ab_summary.json")) else 0
    if time.time() - last > 55 * 60 and not DRY:
        subprocess.run([sys.executable, os.path.join(HERE, "ab_report.py")], capture_output=True, timeout=300)
    dead = []
    for d in ("jev-markets-guarded", "jev-majors-guarded", "jev-majors-invert"):
        hb = jload(os.path.join(HOME, d, "data", "heartbeat.json"), None)
        if not hb or time.time() - hb.get("t", 0) > 600:
            dead.append(d)
            kick("com.dhruv.jevmarkets.guarded" if "markets" in d else "com.dhruv.jevmajors.invert" if "invert" in d else "com.dhruv.jevmajors.guarded")
    if dead:
        alert("ab-dead", "A/B test: a guarded copy is not running", ", ".join(dead) + " heartbeat is stale; kickstarted.", every_hours=3)
        return note("ab test", "fail", "guarded copy down: " + ", ".join(dead))
    note("ab test", "ok", "both arms running; results in each repo's export/ab_summary.json")


def check_phone_access():
    """Phone access: the Mac must not idle-sleep (it carries Remote Control for the Claude app) and the three Jev dashboards must answer on
    localhost and on the Tailscale address (a 401 means up + password enforced). Repair = kickstart a dead dashboard; sleep and Tailscale only alert."""
    import urllib.request, urllib.error
    pm = subprocess.run(["pmset", "-g"], capture_output=True, text=True).stdout
    never = any(l.split()[:2] == ["sleep", "0"] for l in pm.splitlines()) or any(l.split()[:2] == ["SleepDisabled", "1"] for l in pm.splitlines())
    if not never:
        alert("phone-sleep", "Mac can idle-sleep: phone access and Remote Control will drop",
              "pmset shows system sleep is on and SleepDisabled is 0. Fix: sudo pmset -a sleep 0 (or keep Amphetamine running). Keep the lid open and the Mac plugged in.", every_hours=6)
        note("phone access", "warn", "Mac can idle-sleep")
    ts = subprocess.run(["ifconfig"], capture_output=True, text=True).stdout
    ts_ip = "100.101.160.85" if "100.101.160.85" in ts else None
    if not ts_ip:
        alert("phone-tailscale", "Tailscale is down on the Mac", "No 100.101.160.85 interface. Open Tailscale on the Mac and sign in; the phone cannot reach the Jev dashboards until it is up.", every_hours=3)
        note("phone access", "warn", "Tailscale interface missing")

    def code(url):
        try:
            return urllib.request.urlopen(url, timeout=6).status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return None
    bad = []
    for name, port in (("jevdesk", 8788), ("jevmajors", 8789), ("jevmarkets", 8790), ("jevoptionsiv", 8792), ("zillowdash", 8793), ("askjev", 8794), ("jevpredict", 8795), ("overview", 8791)):
        if name == "jevdesk" and os.path.exists(os.path.join(HOME, "jev-desk", "STOPPED")):
            continue
        if code(f"http://127.0.0.1:{port}/" + ("data" if name == "overview" else "api")) is None:
            kick("com.dhruv.overview" if name == "overview" else "com.dhruv.zillowdash" if name == "zillowdash" else "com.dhruv.askjev" if name == "askjev" else f"com.dhruv.{name}.dashboard")
            bad.append(f"{name} (:{port}) not answering on localhost; kickstarted")
        elif ts_ip and code(f"http://{ts_ip}:{port}/" + ("data" if name == "overview" else "api")) != 401:
            bad.append(f"{name} (:{port}) not reachable with a password on {ts_ip}: password missing (dashboard stays localhost-only) or Tailscale blocked")
    if bad:
        alert("phone-dashboards", "Jev dashboards not reachable from the phone", "\n".join(bad), every_hours=3)
        return note("phone access", "fail", "; ".join(bad))
    if never and ts_ip:
        note("phone access", "ok", "Mac awake, Tailscale up, 3 dashboards reachable + password-gated")


def check_jevmarkets():
    """Jev Markets (~/jev-markets): US stocks + ETFs + futures paper desk. Jobs loaded, heartbeat and live data fresh, the daily
    screen current on trading days, no breaker, errors low. Repair = kickstart. Entirely separate from jevdesk and jevmajors."""
    base = os.path.join(HOME, "jev-markets")
    if not os.path.isdir(base):
        return
    for label in ("com.dhruv.jevmarkets.main", "com.dhruv.jevmarkets.dashboard"):
        if not job(label)["loaded"]:
            kick(label)
            alert("jevmarkets-unloaded", "Jev Markets job was not loaded", f"{label} was not loaded in launchd; the watchdog tried to load it.", every_hours=6)
            return note("jev markets", "fail", f"{label.split('.')[-1]} was unloaded; reloaded")
    lt = jload(os.path.join(base, "data", "live_tickets.json"), {})
    for t in lt.values():
        if t.get("type") == "exit" and t.get("status") == "pending" and time.time() - t["created"] > 3 * 60:
            alert("jevmarkets-exit-ticket", f"Jev Markets EXIT ticket unplaced: {t['action']} {t['qty']} {t['symbol']}",
                  f"A live position still needs closing at Robinhood ({t.get('reason', '')[:80]}). Run: cd ~/jev-markets && .venv/bin/python main.py tickets", every_hours=0.25)
    fp = jload(os.path.join(base, "data", "fut_patterns.json"), None)
    if fp and NOW.weekday() < 5 and time.time() - fp.get("updated", 0) > 50 * 3600:
        alert("jevmarkets-futpat", "Jev Markets futures pattern research is stale", "data/fut_patterns.json is over 50 h old; the 17:20 ET job com.dhruv.jevmarkets.futpat may be failing. See ~/jev-markets/futpat.err.log. Run: cd ~/jev-markets && .venv/bin/python futpat.py refresh", every_hours=24)
    hb = jload(os.path.join(base, "data", "heartbeat.json"), None)
    age = time.time() - hb["t"] if hb else None
    if age is None or age > 10 * 60:
        if attempt("jevmarkets-kick", 4):
            kick("com.dhruv.jevmarkets.main")
        alert("jevmarkets-stale", "Jev Markets loop not ticking", f"~/jev-markets/data/heartbeat.json is {'missing' if age is None else f'{int(age // 60)} min old'}. Kickstarted; see ~/jev-markets/main.err.log.", every_hours=3)
        return note("jev markets", "fail", "heartbeat stale; kicked")
    if hb.get("offline"):
        return note("jev markets", "warn", "Mac offline: entries paused")
    if time.time() - (hb.get("scan_thread_at") or time.time()) > 15 * 60:
        if attempt("jevmarkets-scan-kick", 4):
            kick("com.dhruv.jevmarkets.main")
        alert("jevmarkets-scanthread", "Jev Markets scan thread hung", "No scan-loop pass for 15+ min; kickstarted.", every_hours=3)
        return note("jev markets", "fail", "scan thread silent; kicked")
    live = jload(os.path.join(base, "data", "live.json"), {})
    if time.time() - live.get("t", 0) > 240:
        return note("jev markets", "fail", "price snapshot stale: the Alpaca/Yahoo feed is not updating")
    daily = jload(os.path.join(base, "data", "daily.json"), None)
    if daily is not None and marketday.is_trading_day(TODAY) and after("07:30") and time.time() - daily["t"] > 26 * 3600:
        if attempt("jevmarkets-screen", 2):
            kick("com.dhruv.jevmarkets.screen")
        alert("jevmarkets-screen", "Jev Markets daily screen is stale", "data/daily.json is over 26 h old on a trading day; the watchdog re-ran com.dhruv.jevmarkets.screen.", every_hours=6)
        return note("jev markets", "warn", "daily screen stale; re-run")
    if hb.get("breaker"):
        alert("jevmarkets-breaker", "Jev Markets circuit breaker tripped", "Drawdown limit hit; no new trades. Resume: cd ~/jev-markets && .venv/bin/python main.py reset-breaker", every_hours=12)
    if (hb.get("errors_last_hour") or 0) >= 20:
        alert("jevmarkets-errors", f"Jev Markets: {hb['errors_last_hour']} errors in the last hour", "See ~/jev-markets/data/errors.jsonl and main.err.log.", every_hours=3)
    note("jev markets", "warn" if hb.get("breaker") else "ok", f"paper; {len(hb.get('positions', []))} position(s); session {hb.get('session')}; universe {(daily or {}).get('n_universe')}")


def check_jev_budget():
    """Jev (TypeSafe) spend this calendar month across every agent vs ~/jev-client/budget.json.
    The clients themselves refuse calls past the cap; this emails at 80% and when it is hit."""
    sys.path.insert(0, os.path.join(HOME, "jev-client"))
    import jev
    spent, cap = jev.month_spend()
    pct = 100 * spent / cap if cap else 100
    if pct >= 100:
        note("jev budget", "fail", f"${spent:.2f} of ${cap:.2f}: Jev calls are being refused")
        alert("jev-budget-hit", f"Jev monthly budget reached (${cap:.0f})",
              f"Jev spend this month is ${spent:.2f} of the ${cap:.2f} cap in ~/jev-client/budget.json. Every agent's Jev calls "
              "are refused until next month (the agents carry on without Jev).\n\nSee python3 ~/jev-client/jev.py --usage for who spent it.",
              every_hours=24)
    elif pct >= 80:
        note("jev budget", "warn", f"${spent:.2f} of ${cap:.2f} ({pct:.0f}%)")
        alert("jev-budget-80", f"Jev spend at {pct:.0f}% of the monthly budget",
              f"${spent:.2f} of ${cap:.2f} this month. Calls stop at the cap.\n\npython3 ~/jev-client/jev.py --usage shows the spend by agent.",
              every_hours=24)
    elif pct >= 50:
        note("jev budget", "warn", f"${spent:.2f} of ${cap:.2f} ({pct:.0f}%)")
        alert("jev-budget-50", f"Jev spend at {pct:.0f}% of the monthly budget",
              f"${spent:.2f} of ${cap:.2f} this month. Calls stop at the cap.\n\npython3 ~/jev-client/jev.py --usage shows the spend by agent.", every_hours=48)
    else:
        note("jev budget", "ok", f"${spent:.4f} of ${cap:.2f} this month ({pct:.2f}%)")
    # per-agent daily guard: one agent spending a big share of the whole month's budget in a single day is a runaway loop
    try:
        today, by = time.strftime("%Y-%m-%d"), {}
        for line in open(os.path.join(HOME, "jev-client", "calls.jsonl")):
            r = json.loads(line)
            if r.get("in") and time.strftime("%Y-%m-%d", time.localtime(r["t"])) == today:
                by[r["caller"]] = by.get(r["caller"], 0) + r["in"] / 1e6 * jev.USD_PER_MTOK
        big = {k: v for k, v in by.items() if v > 0.04 * cap}                # over 4% of the month's cap in one day (~$1 at $25)
        if big:
            alert("jev-agent-day", "A Jev agent spent a lot today", "Spend today: " + ", ".join(f"{k} ${v:.2f}" for k, v in sorted(by.items(), key=lambda x: -x[1])) +
                  f"\n\nAn agent over ${0.04 * cap:.2f} in a day (4% of the monthly cap) usually means a loop. Check python3 ~/jev-client/jev.py --usage.", every_hours=12)
            note("jev budget", "warn", "agent spend today: " + ", ".join(f"{k} ${v:.2f}" for k, v in big.items()))
    except Exception:
        pass


def check_kalshi_commodity():
    """Kalshi gold/WTI 15m caller (~/kalshi-commodity-agent, launchd every minute): the job must be loaded, each
    asset's evals log fresh while Kalshi has an open window for it, the weekly recalibration current, and a GitHub
    remote set up. Repair = kickstart the job (loads it first if unloaded)."""
    base = os.path.join(HOME, "kalshi-commodity-agent")
    if not os.path.isdir(base):
        return note("kalshi commodity", "warn", "repo missing")
    if os.path.exists(os.path.join(base, "PAUSED.md")):
        return note("kalshi commodity", "ok", "paused (PAUSED.md)")
    label = "com.dhruv.kalshicommodity"
    if not job(label)["loaded"]:
        kick(label)
        alert("kalshi-commodity-unloaded", "Kalshi gold/WTI agent was not loaded", f"{label} was not loaded in launchd; the watchdog tried to load it.", every_hours=6)
        return note("kalshi commodity", "fail", "job was unloaded; reloaded")
    assets = jload(os.path.join(base, "assets.json"), {})
    stale = []
    for name, cfg in assets.items():
        f = os.path.join(base, "data", f"evals-{name}.jsonl")
        age = time.time() - os.path.getmtime(f) if os.path.exists(f) else 1e9
        if age <= 15 * 60:
            continue
        try:
            import urllib.request
            u = f"https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker={cfg['series']}&status=open&limit=1"
            opened = bool(json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15)).get("markets"))
        except Exception:
            opened = False   # cannot tell; do not alarm
        if opened:
            stale.append(f"{name} ({int(age // 60)}m old)" if age < 1e8 else f"{name} (never logged)")
    msgs = []
    if stale:
        kick(label)
        note("kalshi commodity", "fail", "evals log stale: " + ", ".join(stale))
        alert("kalshi-commodity-stale", "Kalshi gold/WTI agent not logging",
              "evals logs are stale while Kalshi has an open window: " + ", ".join(stale) +
              "\n\nThe watchdog kickstarted the job. See ~/kalshi-commodity-agent/agent.err.log.", every_hours=3)
        return
    gate = os.path.join(base, "gate-gold.json")
    if os.path.exists(gate) and time.time() - os.path.getmtime(gate) > 10 * 86400:
        msgs.append("weekly recalibration is over 10 days old")
    r = subprocess.run([GIT, "remote", "get-url", "origin"], cwd=base, capture_output=True, text=True)
    if r.returncode != 0:
        msgs.append("no GitHub remote yet (hourly auto-push cannot run)")
    note("kalshi commodity", "warn" if msgs else "ok", "; ".join(msgs) if msgs else "gold + WTI logging, recal current")


def check_perplab():
    """Daily perp setups email (~/market-lab/perp-lab/run-daily.sh, 08:30): must be sent every day by 09:30. Repair = re-run the job once."""
    base = os.path.join(HOME, "market-lab", "perp-lab"); marker = os.path.join(base, "results", "last-email.txt")
    if not os.path.isdir(base):
        return note("perp setups email", "warn", "perp-lab missing")
    if os.path.exists(os.path.join(base, "PAUSED.md")):
        return note("perp setups email", "ok", "paused (protocol runs inside Jev Majors)")
    now = dt.datetime.now()
    if now.hour * 60 + now.minute < 9 * 60 + 30:
        return note("perp setups email", "ok", "not due yet")
    last = open(marker).read().strip() if os.path.exists(marker) else ""
    if last == now.strftime("%Y-%m-%d"):
        return note("perp setups email", "ok", "sent today")
    subprocess.run([os.path.join(base, "run-daily.sh")], capture_output=True, timeout=600)
    last = open(marker).read().strip() if os.path.exists(marker) else ""
    if last == now.strftime("%Y-%m-%d"):
        return note("perp setups email", "ok", "was missing; re-ran and sent")
    note("perp setups email", "fail", "not sent today after one retry")
    alert("perplab-email", "Daily perp setups email not sent", "The 08:30 perp-lab job did not send today's email and one retry failed. See ~/market-lab/perp-lab/results/email.log.", every_hours=6)


def check_kalshi_btc_1h():
    """Kalshi 1-hour BTC caller (~/kalshi-btc-1h-agent, launchd every minute): job loaded, evals log under 15 min old
    (a KXBTCD hourly event is always open), weekly recalibration current, GitHub remote set. Repair = kickstart."""
    base = os.path.join(HOME, "kalshi-btc-1h-agent")
    if not os.path.isdir(base):
        return note("kalshi btc 1h", "warn", "repo missing")
    if os.path.exists(os.path.join(base, "PAUSED.md")):
        return note("kalshi btc 1h", "ok", "paused (PAUSED.md)")
    label = "com.dhruv.kalshibtc1h"
    if not job(label)["loaded"]:
        kick(label)
        alert("kalshi-btc1h-unloaded", "Kalshi 1-hour BTC agent was not loaded", f"{label} was not loaded in launchd; the watchdog tried to load it.", every_hours=6)
        return note("kalshi btc 1h", "fail", "job was unloaded; reloaded")
    f = os.path.join(base, "data", "evals.jsonl")
    age = time.time() - os.path.getmtime(f) if os.path.exists(f) else 1e9
    if age > 15 * 60:
        kick(label)
        note("kalshi btc 1h", "fail", f"evals log {int(age // 60)}m old" if age < 1e8 else "never logged")
        return alert("kalshi-btc1h-stale", "Kalshi 1-hour BTC agent not logging",
                     f"data/evals.jsonl is {int(age // 60)} minutes old. The watchdog kickstarted the job. See ~/kalshi-btc-1h-agent/agent.err.log.", every_hours=3)
    msgs = []
    gate = os.path.join(base, "gate.json")
    if os.path.exists(gate) and time.time() - os.path.getmtime(gate) > 10 * 86400:
        msgs.append("weekly recalibration is over 10 days old")
    if subprocess.run([GIT, "remote", "get-url", "origin"], cwd=base, capture_output=True, text=True).returncode != 0:
        msgs.append("no GitHub remote yet")
    note("kalshi btc 1h", "warn" if msgs else "ok", "; ".join(msgs) if msgs else "logging, recal current")


def check_sentiment():
    """Social sentiment refresh (coin-launch-agent sentiment.py, every 15 min): latest.json must be fresh."""
    f = os.path.join(HOME, "coin-launch-agent", "data", "sentiment", "latest.json")
    try:
        b = json.load(open(f))
    except (OSError, ValueError):
        return note("sentiment", "warn", "no readings yet")
    age = time.time() - b.get("updated", 0)
    n = sum(1 for t in b.get("tokens", {}).values() if t.get("score") is not None and time.time() - t.get("t", 0) < 3 * 3600)
    if age > 50 * 60:
        note("sentiment", "fail", f"readings {int(age // 60)}m old")
        alert("sentiment", "Social sentiment not refreshing", f"~/coin-launch-agent/data/sentiment/latest.json is {int(age // 60)} minutes old (runs every 15 min).", every_hours=3)
    else:
        note("sentiment", "ok", f"{n} tokens with a fresh reading")


def check_sweep():
    """Daily disk sweep (sweep.py): logs, old screenshots, candle archive + git push. Runs once a day after 03:00."""
    f = os.path.join(HERE, "data", "sweep.json")
    last = jload(f, {}).get("at", "")
    if last[:10] != str(TODAY) and NOW.hour >= 3 and state.get("sweep_started") != str(TODAY):
        state["sweep_started"] = str(TODAY)
        spawn([sys.executable, os.path.join(HERE, "sweep.py")], "sweep.log")
        return note("sweep", "ok", "started today's disk sweep")
    if last and (NOW - dt.datetime.fromisoformat(last)).days >= 2:
        note("sweep", "warn", f"last disk sweep {last[:10]}")
    else:
        c = jload(f, {}).get("candles", "")
        bad = "push_error" in c
        note("sweep", "warn" if bad else "ok", ("candle push failed: " + c[-120:]) if bad else f"last {last[:16] or 'never'}: {jload(f, {}).get('logs', '')}")


def daily_confirmation(trading):
    """Once a day, a short email confirming what was delivered: 12/12 on trading days
    (watchlist + bio/pharma + 10 sectors), the watchlist alone otherwise. Sent on the
    first pass after 12:05, after the watchdog's own catch-ups (11:45/11:50) have run."""
    if not after("12:05") or state.get("confirmed") == TODAY.isoformat():
        return
    done = sent_today()
    expected = ["watchlist"] + (["biopharma"] + sector_keys() if trading else [])
    missing = [e for e in expected if e not in done]
    ok = len(expected) - len(missing)
    subject = (f"{ok}/{len(expected)} emails delivered today" if not missing
               else f"{ok}/{len(expected)} emails delivered, missing: {', '.join(missing)}")
    body = "\n".join(f"{'OK     ' if e in done else 'MISSING'}  {e}" for e in expected)
    state["confirmed"] = TODAY.isoformat()
    state["alerted"].pop("daily-confirmation", None)
    spec = {"kind": "Daily delivery report", "status": {"text": "ALL DELIVERED" if not missing else f"{len(missing)} MISSING", "tone": "good" if not missing else "bad"},
            "title": (f"All {len(expected)} Scheduled Emails Were Delivered Today" if not missing else f"{ok} of {len(expected)} Scheduled Emails Delivered Today, {len(missing)} Missing"),
            "subtitle": f"Checked at {dt.datetime.now():%-I:%M %p} ET after the 11:45 and 11:50 catch-ups. Trading days expect the watchlist, bio/pharma and 10 sector emails.",
            "sections": [{"title": "Delivery status", "blocks": [{"type": "table", "columns": [{"key": "e", "label": "Email"}, {"key": "s", "label": "Status"}],
                         "rows": [{"e": {"v": e.replace("biopharma", "bio/pharma digest").replace("watchlist", "watchlist briefing"), "bold": True}, "s": {"v": "delivered" if e in done else "MISSING", "tone": "good" if e in done else "bad", "bold": e not in done}} for e in expected]}]}],
            "footer": "Sent by the Agent Watchdog (ops/watchdog.py)."}
    alert("daily-confirmation", ("✅ " if not missing else "") + subject, body, every_hours=0, spec=spec)
    note("daily confirmation", "ok" if not missing else "fail", subject)


def tail(path, n=15):
    try:
        with open(path, errors="ignore") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return f"({path} not found)"


def main():
    if "--status" in sys.argv:
        s = jload(STATUS, {})
        print(f"last pass {s.get('at')} · trading day {s.get('trading_day')}")
        for c in s.get("checks", []):
            print(f"  {c['level']:5} {c['check']:<18} {c['msg']}")
        return
    trading = marketday.is_trading_day(TODAY)
    for fn in (check_outbox, check_zillow) + ((check_watchlist,) if ARMED else ()):
        try:
            fn()
        except Exception as ex:
            note(fn.__name__, "fail", f"watchdog error {ex!r}")
    if not ARMED:
        note("email checks", "ok", f"arm on {ARMED_FROM} (installed today)")
    if trading and ARMED:
        for fn in (check_scans, check_market_emails):
            try:
                fn()
            except Exception as ex:
                note(fn.__name__, "fail", f"watchdog error {ex!r}")
    if ARMED:
        try:
            daily_confirmation(trading)
        except Exception as ex:
            note("daily confirmation", "fail", f"watchdog error {ex!r}")
    try:
        check_flip(trading)
    except Exception as ex:
        note("flip notifier", "fail", f"watchdog error {ex!r}")
    for fn in (check_agents, check_platform, check_git_storage, check_prelaunch, check_listed, check_jevdesk, check_jevdesk_grads, check_jevmajors, check_jevmarkets, check_ab, check_phone_access, check_jev_budget, check_kalshi_commodity, check_kalshi_btc_1h, check_perplab, check_sentiment, check_sweep):
        try:
            fn()
        except Exception as ex:
            note(fn.__name__, "fail", f"watchdog error {ex!r}")
    if not marketday.calendar_ok(TODAY):
        note("calendar", "fail", f"nyse_holidays.json has no {TODAY.year} dates")
        alert("calendar", f"NYSE holiday list needs {TODAY.year}",
              "Add this year's NYSE closures to ~/market-lab/ops/nyse_holidays.json.", every_hours=24 * 7)
    if not DRY:
        save(STATE, state)
        save(STATUS, {"at": NOW.isoformat(timespec="seconds"), "trading_day": trading, "checks": status})
    worst = "fail" if any(c["level"] == "fail" for c in status) else "warn" if any(c["level"] == "warn" for c in status) else "ok"
    log(f"pass done: {worst} ({len(status)} checks)")


if __name__ == "__main__":
    main()
