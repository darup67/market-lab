"""Is a date an NYSE trading day? Weekdays minus nyse_holidays.json. Stdlib only."""
import datetime as dt, json, os

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nyse_holidays.json")


def closed_dates():
    try:
        with open(_FILE) as f:
            return set(json.load(f)["closed"])
    except (OSError, ValueError, KeyError):
        return set()


def is_trading_day(d=None):
    d = d or dt.date.today()
    return d.weekday() < 5 and d.isoformat() not in closed_dates()


def calendar_ok(d=None):
    """False once the holiday list no longer covers this year: time to extend it."""
    d = d or dt.date.today()
    return any(x.startswith(str(d.year)) for x in closed_dates())
