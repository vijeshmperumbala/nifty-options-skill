"""Index data from Yahoo Finance, normalised to the Indian session.

Yahoo is free, unofficial and lags; it is the only no-signup source that
carries NIFTY, BANK NIFTY, SENSEX and India VIX intraday. Two rules keep the
skills honest:

    * only COMPLETED bars count (a bar that started at 10:15 is final at 10:20),
    * a bar off the exchange's 5-minute grid is dropped (a live 10:47:13 print
      is a tick, not a bar).

Everything is converted to IST before it is returned, so no skill has to think
about time zones.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time as _time
import urllib.error
import urllib.parse
import urllib.request

from . import clock

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_BACKUP = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"

INDEX_SYMBOLS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "^CNXFIN",
    "SENSEX": "^BSESN",
    "BANKEX": "^BSEBANK",
    "INDIAVIX": "^INDIAVIX",
    "NIFTYIT": "^CNXIT",
    "^NSEI": "^NSEI",
    "^BSESN": "^BSESN",
}

USER_AGENT = "Mozilla/5.0"

# Offline mode for tests and for working on a saved day: point INDKIT_FIXTURE_DIR
# at a folder of Yahoo-shaped chart JSON files (nifty.json, sensex.json, vix.json)
# and no request leaves the machine.
FIXTURE_DIR = os.environ.get("INDKIT_FIXTURE_DIR")
FIXTURE_NAMES = {"^NSEI": "nifty", "^BSESN": "sensex", "^INDIAVIX": "vix", "^NSEBANK": "banknifty"}

RANGES = {"1m": "7d", "3m": "60d", "5m": "60d", "15m": "60d", "30m": "60d", "60m": "730d", "1d": "2y"}


class FeedError(RuntimeError):
    pass


_CACHE = {}          # (symbol, interval, range) -> (fetched_at, payload)
CACHE_SECONDS = 20   # polite: repeated skills in one sitting reuse one download


class Bar:
    __slots__ = ("time", "open", "high", "low", "close", "volume")

    def __init__(self, time, open, high, low, close, volume=0):
        self.time = time
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

    def __repr__(self):
        return f"Bar({self.time.strftime('%Y-%m-%d %H:%M')}, c={self.close})"

    def as_dict(self):
        return {"time": self.time.isoformat(), "open": self.open, "high": self.high,
                "low": self.low, "close": self.close, "volume": self.volume}


def resolve(symbol: str) -> str:
    return INDEX_SYMBOLS.get(symbol.upper(), symbol)


def _from_fixture(symbol, interval):
    if not FIXTURE_DIR:
        return None
    name = FIXTURE_NAMES.get(symbol)
    if not name:
        return None
    path = os.path.join(FIXTURE_DIR, f"{name}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)["chart"]["result"][0]
    if interval == "1d":
        return _aggregate_daily(payload)
    return payload


def _aggregate_daily(payload):
    """Roll 5-minute fixture bars into daily bars, so a fixture can serve both."""
    stamps = payload.get("timestamp") or []
    source = ((payload.get("indicators") or {}).get("quote") or [{}])[0]
    days = {}
    for index, stamp in enumerate(stamps):
        moment = dt.datetime.fromtimestamp(stamp, clock.IST)
        row = {k: (source.get(k) or [None] * len(stamps))[index]
               for k in ("open", "high", "low", "close", "volume")}
        if row["close"] is None:
            continue
        day = moment.date()
        bucket = days.setdefault(day, {"open": row["open"], "high": row["high"],
                                       "low": row["low"], "close": row["close"],
                                       "volume": row["volume"] or 0, "last": moment})
        bucket["high"] = max(bucket["high"] or row["close"], row["high"] or row["close"])
        bucket["low"] = min(bucket["low"] or row["close"], row["low"] or row["close"])
        bucket["close"] = row["close"]
        bucket["volume"] += row["volume"] or 0
        bucket["last"] = moment
    ordered = sorted(days.items())
    stamps_out, quotes = [], {"open": [], "high": [], "low": [], "close": [], "volume": []}
    for day, bucket in ordered:
        stamps_out.append(int(dt.datetime.combine(day, clock.SESSION_START, clock.IST).timestamp()))
        for key in ("open", "high", "low", "close", "volume"):
            quotes[key].append(bucket[key])
    meta = dict(payload.get("meta") or {})
    if ordered:
        meta["regularMarketPrice"] = ordered[-1][1]["close"]
        meta["chartPreviousClose"] = ordered[-2][1]["close"] if len(ordered) > 1 else ordered[-1][1]["open"]
        meta["regularMarketTime"] = int(ordered[-1][1]["last"].timestamp())
    return {"meta": meta, "timestamp": stamps_out, "indicators": {"quote": [quotes]}}


def _get(symbol: str, query: dict, timeout: int = 25) -> dict:
    symbol = resolve(symbol)
    fixture = _from_fixture(symbol, query.get("interval", "1d"))
    if fixture is not None:
        return fixture
    query = dict(query)
    query.setdefault("includePrePost", "false")
    query.setdefault("events", "div,split")
    params = "&".join(f"{k}={v}" for k, v in query.items())
    last_error = None
    for attempt, host in enumerate((YAHOO, YAHOO_BACKUP, YAHOO)):
        url = f"{host.format(symbol=urllib.parse.quote(symbol))}?{params}"
        # A short User-Agent is what Yahoo's edge accepts; a full Chrome UA and
        # an empty one both get 429 from datacentre IPs.
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:   # noqa: S310
                payload = json.loads(response.read().decode("utf-8", "replace"))
            chart = payload.get("chart") or {}
            if chart.get("error"):
                raise FeedError(str(chart["error"]))
            results = chart.get("result") or []
            if not results:
                raise FeedError("empty result")
            return results[0]
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429:
                _time.sleep(5)
            elif exc.code >= 500:
                _time.sleep(1)
            else:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            _time.sleep(1)
        except FeedError as exc:
            last_error = exc
            break
    raise FeedError(f"Yahoo did not return {symbol}: {last_error}")


def meta(symbol: str) -> dict:
    result = _get(symbol, {"range": "1d", "interval": "1d"})
    info = result.get("meta", {})
    return {
        "symbol": info.get("symbol", symbol),
        "price": info.get("regularMarketPrice"),
        "previous_close": info.get("chartPreviousClose") or info.get("previousClose"),
        "currency": info.get("currency"),
        "exchange": info.get("fullExchangeName"),
        "time": dt.datetime.fromtimestamp(info["regularMarketTime"], clock.IST) if info.get("regularMarketTime") else None,
    }


def quote(symbol: str) -> dict:
    """Last traded index level, stamped in IST."""
    info = meta(symbol)
    price = info.get("price")
    if price is None:
        raise FeedError(f"no price for {symbol}")
    previous = info.get("previous_close")
    change = (price - previous) if previous else 0.0
    return {
        "symbol": info["symbol"],
        "price": round(float(price), 2),
        "previous_close": round(float(previous), 2) if previous else None,
        "change": round(change, 2),
        "change_pct": round(100.0 * change / previous, 2) if previous else None,
        "at": info.get("time"),
    }


def raw_bars(symbol: str, interval: str = "5m", range_: str = None, now=None, fresh=False):
    range_ = range_ or RANGES.get(interval, "60d")
    key = (resolve(symbol), interval, range_)
    stamp = _CACHE.get(key)
    if stamp and not fresh and (_time.time() - stamp[0]) < CACHE_SECONDS:
        return stamp[1]
    payload = _get(resolve(symbol), {"range": range_, "interval": interval})
    _CACHE[key] = (_time.time(), payload)
    return payload


def _to_bars(result: dict, interval: str, now=None, complete_only=True):
    stamps = result.get("timestamp") or []
    quote_rows = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    grid = set(clock.bar_grid(interval)) if interval in clock.INTERVALS else None
    opening, closing = clock.SESSION_START, clock.SESSION_END
    bars = []
    seen = set()
    for index, stamp in enumerate(stamps):
        moment = dt.datetime.fromtimestamp(stamp, clock.IST)
        values = {k: (quote_rows.get(k) or [None] * len(stamps))[index]
                  for k in ("open", "high", "low", "close", "volume")}
        if values["close"] is None or values["open"] is None:
            continue
        local = moment.timetz().replace(tzinfo=None)
        if grid is not None and local not in grid:
            continue
        if interval in clock.INTERVALS:
            if not (opening <= local < closing):
                continue
            if complete_only and not clock.is_bar_complete(moment, interval, now):
                continue
        if moment in seen:
            continue
        seen.add(moment)
        bars.append(Bar(moment, float(values["open"]), float(values["high"] or values["close"]),
                        float(values["low"] or values["close"]), float(values["close"]),
                        float(values["volume"] or 0)))
    bars.sort(key=lambda b: b.time)
    return bars


def bars(symbol: str, interval: str = "5m", range_: str = None, now=None, complete_only=True):
    result = raw_bars(symbol, interval, range_, now)
    return _to_bars(result, interval, now, complete_only)


def session_bars(symbol: str, day: dt.date = None, interval: str = "5m", now=None):
    """Every completed bar of one session, in order."""
    moment = clock.now_ist(now)
    day = day or moment.date()
    rows = bars(symbol, interval, RANGES.get(interval, "60d"), now=moment)
    return [b for b in rows if b.time.date() == day]


def daily_bars(symbol: str, days: int = 90) -> list:
    rows = bars(symbol, "1d", "2y", complete_only=False)
    return rows[-days:] if days else rows


def daily_closes(symbol: str, days: int = 90):
    return [b.close for b in daily_bars(symbol, days)]


def opening_range(bars_list, minutes: int = 15, interval: str = "5m"):
    """High/low of the first `minutes` of the session (by default the 09:15-09:30 bars)."""
    cutoff = clock.opening_range_end(minutes)
    chosen = [b for b in bars_list if b.time.time() < cutoff]
    if not chosen:
        return None
    return {
        "high": max(b.high for b in chosen),
        "low": min(b.low for b in chosen),
        "bars": len(chosen),
        "from": chosen[0].time.time().strftime("%H:%M"),
        "to": cutoff.strftime("%H:%M"),
        "volume": sum(b.volume for b in chosen),
    }


def india_vix(now=None) -> dict:
    """India VIX - the market's 30-day expected move, in annualised vol points."""
    info = quote("INDIAVIX")
    return {
        "value": info["price"],
        "change_pct": info["change_pct"],
        "regime": ("calm" if info["price"] < 13 else
                   "normal" if info["price"] < 18 else
                   "elevated" if info["price"] < 25 else "stressed"),
        "at": info["at"],
        "daily_move_pct": round(info["price"] / (252 ** 0.5), 2),
    }
