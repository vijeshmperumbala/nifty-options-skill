"""The Indian session clock.

NSE and BSE run the same equity-derivatives session: pre-open 09:00-09:15,
continuous trading 09:15-15:30 IST. There is no after-hours session for
options. Expiry days are NOT hardcoded here on purpose: the expiry calendar is
read from the contract master (indkit.instruments), because the exchange moves
expiries whenever a Tuesday/Thursday is a holiday.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

PREOPEN_START = dt.time(9, 0)
SESSION_START = dt.time(9, 15)
SESSION_END = dt.time(15, 30)

# Exchange trading holidays (equity + equity derivatives). 2026 is the
# published NSE/BSE list; 2027 dates are tentative and announced by the
# exchanges each December - verify before relying on them.
HOLIDAYS = {
    # 2026
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
    # 2027 (tentative)
    "2027-01-26": "Republic Day",
    "2027-03-22": "Holi",
    "2027-04-09": "Dr. Baba Saheb Ambedkar Jayanti",
    "2027-04-16": "Good Friday",
    "2027-05-03": "Maharashtra Day",
    "2027-11-01": "Diwali-Balipratipada",
}

# One-hour special sessions (Muhurat trading). Not a normal session: the skills
# refuse to treat one as a trading day.
SPECIAL_SESSIONS = {"2026-11-08": "Muhurat trading (Diwali Laxmi Pujan)"}


def now_ist(now=None) -> dt.datetime:
    """The clock, in IST. `now` may be a datetime (naive = IST) or a string."""
    if now is None:
        return dt.datetime.now(IST)
    if isinstance(now, dt.datetime):
        return now.replace(tzinfo=IST) if now.tzinfo is None else now.astimezone(IST)
    if isinstance(now, dt.date):
        return dt.datetime.combine(now, SESSION_START, IST)
    text = str(now).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=IST)
    raise ValueError(f"cannot read the clock from {now!r} (try '2026-11-09 10:15')")


def holiday_name(day: dt.date):
    key = day.isoformat()
    if key in HOLIDAYS:
        return HOLIDAYS[key]
    if key in SPECIAL_SESSIONS:
        return SPECIAL_SESSIONS[key]
    return None


def is_trading_day(day: dt.date) -> bool:
    """A normal full session: Monday to Friday, not an exchange holiday."""
    return day.weekday() < 5 and day.isoformat() not in HOLIDAYS


def previous_trading_day(day: dt.date) -> dt.date:
    cursor = day - dt.timedelta(days=1)
    for _ in range(30):
        if is_trading_day(cursor):
            return cursor
        cursor -= dt.timedelta(days=1)
    raise ValueError("no trading day found in the last 30 days")


def next_trading_day(day: dt.date) -> dt.date:
    cursor = day + dt.timedelta(days=1)
    for _ in range(30):
        if is_trading_day(cursor):
            return cursor
        cursor += dt.timedelta(days=1)
    raise ValueError("no trading day found in the next 30 days")


def trading_days(start: dt.date, end: dt.date):
    cursor = start
    while cursor <= end:
        if is_trading_day(cursor):
            yield cursor
        cursor += dt.timedelta(days=1)


def market_state(now=None) -> str:
    """closed | preopen | open | post - for the cash/F&O session."""
    moment = now_ist(now)
    if not is_trading_day(moment.date()):
        return "closed"
    clock = moment.time()
    if clock < PREOPEN_START:
        return "closed"
    if clock < SESSION_START:
        return "preopen"
    if clock <= SESSION_END:
        return "open"
    return "post"


def is_open(now=None) -> bool:
    return market_state(now) == "open"


INTERVALS = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "60m": 60}


def bar_grid(interval: str = "5m"):
    """Every bar START time in a full session, on the exchange's own grid.

    5-minute bars run 09:15 to 15:25 (the 15:25 bar covers 15:25-15:30), so a
    full session is 75 bars. This is how NSE prices print; a 15:30-stamped
    intraday bar from a data vendor is off the grid and is dropped.
    """
    minutes = INTERVALS[interval]
    if 1440 % minutes:
        raise ValueError(f"{interval} does not divide a day")
    start = dt.datetime.combine(dt.date(2000, 1, 1), SESSION_START)
    end = dt.datetime.combine(dt.date(2000, 1, 1), SESSION_END)
    grid = []
    cursor = start
    while cursor < end:
        grid.append(cursor.time())
        cursor += dt.timedelta(minutes=minutes)
    return grid


def bar_close_time(bar_start: dt.datetime, interval: str = "5m") -> dt.datetime:
    """When a bar that STARTED at bar_start is final."""
    return bar_start + dt.timedelta(minutes=INTERVALS[interval])


def is_bar_complete(bar_start, interval: str, now=None) -> bool:
    """A bar counts only once its window has closed AND the clock has caught up."""
    moment = now_ist(now)
    start = bar_start if isinstance(bar_start, dt.datetime) else now_ist(bar_start)
    if start.tzinfo is None:
        start = start.replace(tzinfo=IST)
    return moment >= bar_close_time(start.astimezone(IST), interval)


def opening_range_end(or_minutes: int = 15) -> dt.time:
    """The clock time the opening range finishes forming."""
    start = dt.datetime.combine(dt.date(2000, 1, 1), SESSION_START)
    return (start + dt.timedelta(minutes=or_minutes)).time()


def phase(now=None, config=None) -> dict:
    """Everything a skill needs to know about 'now' in one dict."""
    config = config or {}
    moment = now_ist(now)
    state = market_state(moment)
    before = _parse_time(config.get("no_entries_before", "09:30"), dt.time(9, 30))
    after = _parse_time(config.get("no_entries_after", "14:30"), dt.time(14, 30))
    flat_by = _parse_time(config.get("flat_by", "15:15"), dt.time(15, 15))
    clock = moment.time()
    entries_ok = state == "open" and before <= clock <= after
    reason = ""
    if state == "closed":
        reason = "the market is closed"
    elif state == "preopen":
        reason = "the pre-open call auction is still running (09:00-09:15)"
    elif state == "post":
        reason = "the session is over (15:30)"
    elif clock < before:
        reason = f"no new entries before {before.strftime('%H:%M')} IST"
    elif clock > after:
        reason = f"no new entries after {after.strftime('%H:%M')} IST"
    return {
        "now": moment,
        "state": state,
        "entries_ok": entries_ok,
        "reason": reason,
        "no_entries_before": before,
        "no_entries_after": after,
        "flat_by": flat_by,
        "past_flat_by": clock >= flat_by,
        "is_trading_day": is_trading_day(moment.date()),
        "holiday": holiday_name(moment.date()),
        "minutes_to_close": _minutes_between(moment, dt.datetime.combine(moment.date(), SESSION_END, IST)),
    }


def _parse_time(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, dt.time):
        return value
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return dt.datetime.strptime(str(value), fmt).time()
        except ValueError:
            continue
    return default


def _minutes_between(a: dt.datetime, b: dt.datetime) -> int:
    return int((b - a).total_seconds() // 60)


def describe_session(now=None) -> str:
    """One plain line about the session, for the top of any tool's output."""
    moment = now_ist(now)
    state = market_state(moment)
    day = moment.date()
    if state == "closed":
        holiday = holiday_name(day)
        if holiday:
            return f"{day.strftime('%a %d %b %Y')}: market closed ({holiday})."
        if day.weekday() >= 5:
            return f"{day.strftime('%a %d %b %Y')}: weekend, market closed."
        return f"{moment.strftime('%a %d %b %Y %H:%M')} IST: outside the session (09:15-15:30)."
    return f"{moment.strftime('%a %d %b %Y %H:%M')} IST: {state}."
