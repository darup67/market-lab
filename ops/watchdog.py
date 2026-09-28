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
def alert(key, subject, body, every_hours=6):
    """Email once per `key` per `every_hours`; a Mac notification if Gmail itself fails."""
    last = state["alerted"].get(key, 0)
    if time.time() - last < every_hours * 3600:
        return
    state["alerted"][key] = time.time()
    if DRY:
        return log(f"DRY would alert: {subject}")
    ok = False
    try:
        pw = subprocess.run(["security", "find-generic-password", "-a", MAIL["account"], "-s", MAIL["service"], "-w"],
                            capture_output=True, text=True, check=True).stdout.strip()
        msg = MIMEText(f"<pre style='font-size:13px'>{body}</pre>", "html", "utf-8")
        msg["Subject"] = Header(subject if subject.startswith("✅") else f"⚠️ Watchdog: {subject}", "utf-8")
        msg["From"] = f"Agent Watchdog <{MAIL['account']}>"
        msg["To"] = MAIL["to"]
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
            s.login(MAIL["account"], pw)
            s.sendmail(MAIL["account"], [MAIL["to"]], msg.as_string())
        ok = True
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
}
PINS = {    # symlink: exact target it must resolve to
    os.path.join(HOME, ".local", "bin", "node"): os.path.join(HOME, ".local/opt/node-v22.22.3/bin/node"),
    os.path.join(HOME, ".venvs", "market-ml", "bin", "python"): os.path.join(HOME, ".local/share/uv/python/cpython-3.11.15-macos-aarch64-none/bin/python3.11"),
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


REPOS = ["asset-agents", "coin-launch-agent", "flip-notifier", "flux-lab", "jev-client", "kalshi-btc-agent",
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
    alert("daily-confirmation", ("✅ " if not missing else "") + subject, body, every_hours=0)
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
    for fn in (check_agents, check_platform, check_git_storage):
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
