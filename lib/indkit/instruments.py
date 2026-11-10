"""The contract master: lots, expiries, strikes and broker symbols.

Nothing about Indian contracts is stable enough to hardcode. NIFTY's lot went
25 -> 75 -> 65; weekly expiries moved from Thursday to Tuesday and are pulled
forward whenever that Tuesday is a holiday; Sensex expires on a different day
from NIFTY. So this module reads the exchange's own contract list:

    primary   Kite's public instrument dumps (no key): /instruments/NFO, /BFO
    fallback  Upstox's public instrument master (NSE.json.gz, BSE.json.gz)
    offline   a saved copy of either, or a folder holding NFO.csv / BFO.csv

Whatever the source, the rest of the pack asks the same questions: what is the
lot size, which expiries trade, what strikes exist, what is the exact broker
symbol, and is this the monthly contract?
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import json
import os
import time as _time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from . import clock

KITE_NFO = "https://api.kite.trade/instruments/NFO"
KITE_BFO = "https://api.kite.trade/instruments/BFO"
UPSTOX = {
    "NSE": "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
    "BSE": "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz",
}

# The index underlyings this pack knows about. `spot` is the free Yahoo symbol
# used for the index itself; step is only a fallback (the master is asked first).
UNDERLYINGS = {
    "NIFTY":      {"name": "NIFTY 50",      "exchange": "NSE", "spot": "^NSEI",    "step": 50,  "weekly": True},
    "BANKNIFTY":  {"name": "NIFTY BANK",    "exchange": "NSE", "spot": "^NSEBANK", "step": 100, "weekly": False},
    "FINNIFTY":   {"name": "NIFTY FIN SERVICE", "exchange": "NSE", "spot": "^CNXFIN", "step": 50, "weekly": False},
    "MIDCPNIFTY": {"name": "NIFTY MIDCAP SELECT", "exchange": "NSE", "spot": None, "step": 25, "weekly": False},
    "NIFTYNXT50": {"name": "NIFTY NEXT 50", "exchange": "NSE", "spot": "^NSMIDCP", "step": 100, "weekly": False},
    "SENSEX":     {"name": "SENSEX",        "exchange": "BSE", "spot": "^BSESN",   "step": 100, "weekly": True},
    "BANKEX":     {"name": "BSE BANKEX",    "exchange": "BSE", "spot": "^BSEBANK", "step": 100, "weekly": False},
    "SENSEX50":   {"name": "SENSEX 50",     "exchange": "BSE", "spot": None,      "step": 100, "weekly": False},
}

DEFAULT_CONFIG = {
    "underlying": "NIFTY",
    "instruments_max_age_hours": 12,
    "risk_free_rate": 0.065,
    "dividend_yield": 0.012,
}


@dataclass(frozen=True)
class Contract:
    underlying: str
    exchange: str
    symbol: str
    expiry: dt.date
    strike: float
    right: str          # CE, PE or FUT
    lot: int
    tick: float

    @property
    def is_option(self) -> bool:
        return self.right in ("CE", "PE")

    def describe(self, today: Optional[dt.date] = None) -> str:
        """'NIFTY 17 Nov 2026 25000 CE (lot 65)' - for humans, not for the broker."""
        kind = {"CE": "call", "PE": "put", "FUT": "future"}[self.right]
        base = f"{self.underlying} {self.expiry.strftime('%d %b %Y').lstrip('0')}"
        if self.is_option:
            base += f" {int(self.strike)} {self.right} ({kind}, lot {self.lot})"
        else:
            base += f" futures (lot {self.lot})"
        if today:
            days = (self.expiry - today).days
            base += f" - {days} day{'s' if days != 1 else ''} to expiry"
        return base

    def as_dict(self) -> dict:
        d = asdict(self)
        d["expiry"] = self.expiry.isoformat()
        return d


class MasterError(RuntimeError):
    pass


class Master:
    def __init__(self, contracts: List[Contract], source: str = "?", fetched_at: Optional[str] = None):
        self.contracts = contracts
        self.source = source
        self.fetched_at = fetched_at
        self._by_underlying: Dict[str, List[Contract]] = {}
        for c in contracts:
            self._by_underlying.setdefault(c.underlying, []).append(c)

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, config=None, now=None, offline=None, refresh=False) -> "Master":
        cfg = _config(config)
        state_dir = os.path.join(_trader_dir(cfg), "state", "instruments")
        today = clock.now_ist(now).date()
        offline = offline or os.environ.get("INDKIT_MASTER_DIR")
        if offline:
            rows, source, fetched = _from_offline(offline)
            return cls(rows, source, fetched)
        cached = _read_cache(state_dir, cfg, today)
        if cached and not refresh and _cache_fresh(state_dir, cfg):
            return cached
        try:
            master = cls(*_from_kite())
            _write_cache(state_dir, master)
            return master
        except Exception as exc:                                   # noqa: BLE001
            if cached:
                cached.source += f" (stale cache; download failed: {exc.__class__.__name__})"
                return cached
            try:
                master = cls(*_from_upstox())
                _write_cache(state_dir, master)
                return master
            except Exception as exc2:                              # noqa: BLE001
                raise MasterError(
                    "could not load the contract master from any source "
                    f"(kite: {exc}; upstox: {exc2}). Check the network, or pass "
                    "--offline with a saved NFO.csv/BFO.csv."
                ) from None

    # ---------------------------------------------------------------- queries
    def underlyings(self) -> List[str]:
        return sorted(self._by_underlying)

    def exists(self, underlying: str) -> bool:
        return underlying.upper() in self._by_underlying

    def contracts_for(self, underlying: str, right: str = None, expiry: dt.date = None) -> List[Contract]:
        rows = self._by_underlying.get(underlying.upper(), [])
        if right:
            rows = [c for c in rows if c.right == right.upper()]
        if expiry:
            rows = [c for c in rows if c.expiry == expiry]
        return rows

    def expiries(self, underlying: str, after: dt.date = None, include_today=True) -> List[dt.date]:
        rows = self._by_underlying.get(underlying.upper(), [])
        days = sorted({c.expiry for c in rows})
        if after:
            days = [d for d in days if (d >= after if include_today else d > after)]
        return days

    def nearest_expiry(self, underlying: str, after: dt.date = None, min_days: int = 0) -> Optional[dt.date]:
        after = after or dt.date.today()
        for day in self.expiries(underlying, after):
            if (day - after).days >= min_days:
                return day
        return None

    def lot(self, underlying: str, expiry: dt.date = None) -> int:
        rows = self._by_underlying.get(underlying.upper(), [])
        if not rows:
            raise MasterError(f"no contracts for {underlying} in the master")
        if expiry:
            rows = [c for c in rows if c.expiry == expiry] or rows
        # nearest expiry carries the current lot size (exchanges change it by series)
        nearest = min(c.expiry for c in rows)
        lots = {c.lot for c in rows if c.expiry == nearest}
        return sorted(lots)[0]

    def strikes(self, underlying: str, expiry: dt.date) -> List[float]:
        rows = [c for c in self.contracts_for(underlying, expiry=expiry) if c.is_option]
        return sorted({c.strike for c in rows})

    def step(self, underlying: str, expiry: dt.date = None) -> float:
        expiry = expiry or self.nearest_expiry(underlying)
        strikes = self.strikes(underlying, expiry) if expiry else []
        if len(strikes) > 2:
            diffs = [round(b - a, 4) for a, b in zip(strikes, strikes[1:])]
            diffs = [d for d in diffs if d > 0]
            if diffs:
                # the typical spacing: a far-month series can list sparse strikes,
                # so the smallest gap would lie about the ladder near the money
                counts = {}
                for diff in diffs:
                    counts[diff] = counts.get(diff, 0) + 1
                best = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))
                return best[0]
        return float(UNDERLYINGS.get(underlying.upper(), {}).get("step", 50))

    def is_monthly(self, underlying: str, expiry: dt.date) -> bool:
        same_month = [d for d in self.expiries(underlying) if (d.year, d.month) == (expiry.year, expiry.month)]
        return bool(same_month) and expiry == max(same_month)

    def series_kind(self, underlying: str, expiry: dt.date) -> str:
        return "monthly" if self.is_monthly(underlying, expiry) else "weekly"

    def get(self, underlying: str, expiry: dt.date, strike: float, right: str) -> Optional[Contract]:
        for c in self.contracts_for(underlying, right, expiry):
            if abs(c.strike - float(strike)) < 1e-6:
                return c
        return None

    def snap_strike(self, underlying: str, expiry: dt.date, strike: float) -> float:
        """The nearest strike that actually trades."""
        strikes = self.strikes(underlying, expiry)
        if not strikes:
            step = self.step(underlying, expiry)
            return round(float(strike) / step) * step
        return min(strikes, key=lambda s: abs(s - float(strike)))

    def atm(self, underlying: str, spot: float, expiry: dt.date, right: str = "CE",
            offset_steps: int = 0) -> Optional[Contract]:
        """At-the-money (or N steps out of the money) contract."""
        step = self.step(underlying, expiry)
        base = self.snap_strike(underlying, expiry, spot)
        target = base + offset_steps * step * (1 if right.upper() == "CE" else -1)
        strike = self.snap_strike(underlying, expiry, target)
        return self.get(underlying, expiry, strike, right)

    def by_symbol(self, symbol: str) -> Optional[Contract]:
        for rows in self._by_underlying.values():
            for c in rows:
                if c.symbol == symbol:
                    return c
        return None

    # ------------------------------------------------------------- context
    def context(self, underlying: str, now=None) -> dict:
        """Everything a skill prints about the contract universe, in one dict."""
        today = clock.now_ist(now).date()
        expiry = self.nearest_expiry(underlying, today)
        if expiry is None:
            raise MasterError(f"no live expiry for {underlying}")
        return {
            "underlying": underlying.upper(),
            "exchange": UNDERLYINGS.get(underlying.upper(), {}).get("exchange", "NSE"),
            "name": UNDERLYINGS.get(underlying.upper(), {}).get("name", underlying),
            "spot_symbol": UNDERLYINGS.get(underlying.upper(), {}).get("spot"),
            "lot": self.lot(underlying, expiry),
            "expiry": expiry,
            "days_to_expiry": (expiry - today).days,
            "series": self.series_kind(underlying, expiry),
            "strike_step": self.step(underlying, expiry),
            "is_expiry_day": expiry == today,
        }


# ------------------------------------------------------------------ sources
def _config(config) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if isinstance(config, dict):
        cfg.update({k: v for k, v in config.items() if not isinstance(v, dict)})
    return cfg


def _trader_dir(cfg) -> str:
    path = cfg.get("trader_dir") or "trader"
    return path if os.path.isabs(path) else os.path.join(os.getcwd(), path)


def _fetch(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; nifty-options-skill/1.0)",
        "Accept": "text/csv,application/json,*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as response:      # noqa: S310
        return response.read()


def _from_kite():
    rows: List[Contract] = []
    for segment, url in (("NFO", KITE_NFO), ("BFO", KITE_BFO)):
        raw = _fetch(url).decode("utf-8", "replace")
        for contract in _parse_kite(io.StringIO(raw)):
            rows.append(contract)
    if not rows:
        raise MasterError("kite returned no index contracts")
    return rows, "kite public instrument dump", dt.datetime.now(clock.IST).isoformat(timespec="seconds")


def _parse_kite(handle) -> List[Contract]:
    out: List[Contract] = []
    for row in csv.DictReader(handle):
        name = (row.get("name") or "").strip().upper()
        if name not in UNDERLYINGS:
            continue
        kind = (row.get("instrument_type") or "").strip().upper()
        if kind not in ("CE", "PE", "FUT"):
            continue
        try:
            expiry = dt.date.fromisoformat(row["expiry"])
            strike = float(row.get("strike") or 0)
            lot = int(float(row.get("lot_size") or 0))
            tick = float(row.get("tick_size") or 0.05)
        except (ValueError, KeyError):
            continue
        out.append(Contract(
            underlying=name,
            exchange=(row.get("exchange") or UNDERLYINGS[name]["exchange"]).upper(),
            symbol=row["tradingsymbol"],
            expiry=expiry,
            strike=strike,
            right=kind,
            lot=lot,
            tick=tick,
        ))
    return out


def _from_upstox():
    rows: List[Contract] = []
    for exchange, url in UPSTOX.items():
        raw = _fetch(url)
        try:
            payload = json.loads(gzip.decompress(raw))
        except OSError:
            payload = json.loads(raw)
        for item in payload:
            name = (item.get("underlying_symbol") or item.get("name") or "").upper()
            if name not in UNDERLYINGS:
                continue
            kind = (item.get("instrument_type") or "").upper()
            if kind not in ("ce", "CE", "pe", "PE", "fut", "FUT"):
                continue
            expiry_ms = item.get("expiry")
            if not expiry_ms:
                continue
            expiry = dt.datetime.fromtimestamp(expiry_ms / 1000, clock.IST).date()
            rows.append(Contract(
                underlying=name,
                exchange=exchange,
                symbol=item.get("trading_symbol") or item.get("tradingsymbol") or "",
                expiry=expiry,
                strike=float(item.get("strike_price") or 0),
                right=kind.upper(),
                lot=int(float(item.get("lot_size") or 0)),
                tick=float(item.get("tick_size") or 0.05),
            ))
    if not rows:
        raise MasterError("upstox returned no index contracts")
    return rows, "upstox public instrument master", dt.datetime.now(clock.IST).isoformat(timespec="seconds")


def _from_offline(path: str):
    if os.path.isdir(path):
        rows: List[Contract] = []
        for filename in ("NFO.csv", "BFO.csv", "nfo.csv", "bfo.csv"):
            full = os.path.join(path, filename)
            if os.path.exists(full):
                with open(full, "r", encoding="utf-8", errors="replace") as handle:
                    rows.extend(_parse_kite(handle))
        if rows:
            return rows, f"offline {path}", None
        raise MasterError(f"no NFO.csv or BFO.csv in {path}")
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            return _parse_upstox_csv(handle), f"offline {path}", None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    if text.lstrip().startswith("["):
        payload = json.loads(text)
        return _from_upstox_payload(payload), f"offline {path}", None
    return _parse_kite(io.StringIO(text)), f"offline {path}", None


def _parse_upstox_csv(handle):
    return _from_upstox_payload(json.load(handle))


def _from_upstox_payload(payload):
    rows = []
    for item in payload:
        name = (item.get("underlying_symbol") or item.get("name") or "").upper()
        kind = (item.get("instrument_type") or "").upper()
        if name not in UNDERLYINGS or kind not in ("CE", "PE", "FUT") or not item.get("expiry"):
            continue
        expiry = dt.datetime.fromtimestamp(item["expiry"] / 1000, clock.IST).date()
        rows.append(Contract(name, item.get("exchange", UNDERLYINGS[name]["exchange"]),
                             item.get("trading_symbol", ""), expiry,
                             float(item.get("strike_price") or 0), kind,
                             int(float(item.get("lot_size") or 0)),
                             float(item.get("tick_size") or 0.05)))
    return rows


# ------------------------------------------------------------------- cache
def _cache_paths(state_dir: str):
    return os.path.join(state_dir, "master.json"), os.path.join(state_dir, "stamp.json")


def _cache_fresh(state_dir: str, cfg: dict) -> bool:
    _, stamp_path = _cache_paths(state_dir)
    if not os.path.exists(stamp_path):
        return False
    try:
        with open(stamp_path, "r", encoding="utf-8") as handle:
            stamp = json.load(handle)
        age_hours = (dt.datetime.now(clock.IST) - dt.datetime.fromisoformat(stamp["fetched_at"])).total_seconds() / 3600
        return age_hours <= float(cfg["instruments_max_age_hours"])
    except Exception:                                              # noqa: BLE001
        return False


def _read_cache(state_dir: str, cfg: dict, today: dt.date):
    path, _ = _cache_paths(state_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        rows = [Contract(c["underlying"], c["exchange"], c["symbol"], dt.date.fromisoformat(c["expiry"]),
                         c["strike"], c["right"], c["lot"], c["tick"]) for c in payload["contracts"]]
        rows = [c for c in rows if c.expiry >= today]
        if not rows:
            return None
        return Master(rows, payload.get("source", "cache"), payload.get("fetched_at"))
    except Exception:                                              # noqa: BLE001
        return None


def _write_cache(state_dir: str, master: "Master") -> None:
    try:
        os.makedirs(state_dir, exist_ok=True)
        path, stamp_path = _cache_paths(state_dir)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"source": master.source, "fetched_at": master.fetched_at,
                       "contracts": [c.as_dict() for c in master.contracts]}, handle)
        with open(stamp_path, "w", encoding="utf-8") as handle:
            json.dump({"fetched_at": master.fetched_at or dt.datetime.now(clock.IST).isoformat()}, handle)
    except OSError:
        pass


def save_offline_copy(destination: str) -> str:
    """Download both segments into a folder, for offline use."""
    os.makedirs(destination, exist_ok=True)
    for name, url in (("NFO.csv", KITE_NFO), ("BFO.csv", KITE_BFO)):
        with open(os.path.join(destination, name), "wb") as handle:
            handle.write(_fetch(url))
        _time.sleep(0.2)
    return destination
