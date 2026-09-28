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
  flip notifier healthcheck.js --repair every pass 09:15-16:15 on trading days, hourly
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
        return note("flip notifier", "ok", "DRY: healthcheck not run")
    if os.path.exists(os.path.join(FLIP, "CHART_WATCHER_PAUSED")):
        # Chart watcher retired in favour of headless-flip.js (runs :01 :03 :31 :33).
        # healthcheck.js --repair would reload TradingView and re-flag the missing Scanner.
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
        # Matrix report at 08:00 and 16:30 daily: re-send if the latest slot was missed.
        slots = [s for s in ("08:00", "16:30") if after(s)]
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
    try:
        r = subprocess.run([NODE, "healthcheck.js", "--repair"], cwd=FLIP, capture_output=True, text=True, timeout=120)
        code, out = r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        code, out = 2, "healthcheck.js timed out after 120s"
    if "reloaded by --repair" in out:
        note("flip notifier", "warn", "LaunchAgent had stopped; healthcheck reloaded it")
    if code == 0:
        note("flip notifier", "ok", "HEALTHY")
    elif code == 1:
        note("flip notifier", "warn", "DEGRADED (see healthcheck)")
    else:
        note("flip notifier", "fail", "BROKEN")
        if market_hours:
            alert("flip-broken", "Flip notifier BROKEN during market hours",
                  "healthcheck.js --repair could not fix it:\n\n" + out[-2500:], every_hours=2)


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
