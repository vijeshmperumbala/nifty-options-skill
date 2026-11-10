"""The option chain: live adapters, a CSV import, and a modelled fallback.

Premium is the tradable thing in index options, so every skill downstream needs
a chain. Indian chains are not on Yahoo; NSE and BSE both serve them from
endpoints that block datacentre IPs. This module therefore has an honest order
of preference, and always SAYS which one it used:

    1. csv     a chain you exported from your broker terminal or the NSE site
               (the most reliable path, and the only one that is truly yours)
    2. nse     https://www.nseindia.com/api/option-chain-indices (NIFTY,
               BANKNIFTY, FINNIFTY, MIDCPNIFTY) - needs a browser-like session
    3. bse     the BSE derivatives chain endpoint (SENSEX, BANKEX)
    4. model   Black-Scholes priced from the live index level and India VIX.
               Labelled MODELLED everywhere it appears: it has no open interest,
               no bid/ask, and no IV smile from the market.

Analytics (PCR, max pain, OI walls) need real open interest, so they refuse to
publish a number unless the chain came from a real source.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import os
import urllib.error
import urllib.request

from . import bs, clock, feed

NSE_HOME = "https://www.nseindia.com"
NSE_CHAIN = "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
NSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
}
BSE_CHAIN = ("https://api.bseindia.com/BseIndiaAPI/api/DerivOptionChain/w"
             "?scrip_cd={scrip}&expiry_dt={expiry}&type=")


class ChainError(RuntimeError):
    pass


class Chain:
    """A strike ladder for one underlying and one expiry."""

    def __init__(self, underlying, exchange, expiry, spot, rows, source, as_of=None, lot=None):
        self.underlying = underlying.upper()
        self.exchange = exchange
        self.expiry = expiry
        self.spot = float(spot)
        self.rows = {float(k): v for k, v in rows.items()}   # strike -> {"CE":{...},"PE":{...}}
        self.source = source
        self.as_of = as_of or clock.now_ist()
        self.lot = lot

    # ---------------------------------------------------------------- basics
    @property
    def real(self) -> bool:
        """True when the quotes came from a market source, not from a model."""
        return self.source in ("nse", "bse", "csv", "broker", "dhan")

    def security_id(self, strike, right):
        """The exchange security id for one leg - what an order API needs."""
        return self.leg(strike, right).get("security_id")

    def strikes(self):
        return sorted(self.rows)

    def leg(self, strike, right) -> dict:
        return self.rows.get(float(strike), {}).get(right.upper(), {})

    def premium(self, strike, right):
        value = self.leg(strike, right).get("ltp")
        return float(value) if value not in (None, "") else None

    def nearest_strike(self, spot=None) -> float:
        spot = self.spot if spot is None else float(spot)
        if not self.rows:
            raise ChainError("empty chain")
        return min(self.strikes(), key=lambda k: abs(k - spot))

    def contract(self, spot=None, right="CE", offset=0, step=None) -> dict:
        """The strike `offset` steps out of the money, with its quote."""
        right = right.upper()
        atm = self.nearest_strike(spot)
        step = step or self.step()
        target = atm + offset * step * (1 if right == "CE" else -1)
        strike = min(self.strikes(), key=lambda k: abs(k - target))
        quote = self.leg(strike, right)
        return {"strike": strike, "right": right, "premium": self.premium(strike, right),
                "iv": quote.get("iv"), "oi": quote.get("oi"), "volume": quote.get("volume"),
                "bid": quote.get("bid"), "ask": quote.get("ask"), "quote": quote}

    def step(self) -> float:
        strikes = self.strikes()
        if len(strikes) < 3:
            return 50.0
        diffs = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
        return min(diffs) if diffs else 50.0

    def atm_iv(self, now=None) -> float:
        """Average implied vol of the ATM call and put; falls back to the model IV."""
        atm = self.nearest_strike()
        values = [self.leg(atm, "CE").get("iv"), self.leg(atm, "PE").get("iv")]
        values = [float(v) for v in values if v]
        if values:
            return sum(values) / len(values)
        return self.model_iv

    @property
    def model_iv(self) -> float:
        return getattr(self, "_model_iv", 0.14)

    @model_iv.setter
    def model_iv(self, value):
        self._model_iv = float(value)

    # ------------------------------------------------------------- analytics
    def pcr(self, by="oi") -> dict:
        if not self.real:
            raise ChainError("PCR needs real open interest; this chain is modelled")
        key = "oi" if by == "oi" else "volume"
        calls = sum(float(r.get("CE", {}).get(key) or 0) for r in self.rows.values())
        puts = sum(float(r.get("PE", {}).get(key) or 0) for r in self.rows.values())
        if not calls:
            raise ChainError("no call open interest in the chain")
        return {"calls": calls, "puts": puts, "ratio": round(puts / calls, 3),
                "bias": ("put-heavy" if puts / calls > 1.2 else
                         "call-heavy" if puts / calls < 0.8 else "balanced")}

    def max_pain(self) -> float:
        """The strike where option writers lose least - the classic expiry magnet."""
        if not self.real:
            raise ChainError("max pain needs real open interest; this chain is modelled")
        best = None
        for candidate in self.strikes():
            pain = 0.0
            for strike, row in self.rows.items():
                call_oi = float(row.get("CE", {}).get("oi") or 0)
                put_oi = float(row.get("PE", {}).get("oi") or 0)
                pain += call_oi * max(0.0, candidate - strike)
                pain += put_oi * max(0.0, strike - candidate)
            if best is None or pain < best[0]:
                best = (pain, candidate)
        return best[1] if best else self.nearest_strike()

    def oi_walls(self, count=3) -> dict:
        if not self.real:
            raise ChainError("open-interest walls need a real chain")
        calls = sorted(((float(r.get("CE", {}).get("oi") or 0), k) for k, r in self.rows.items()), reverse=True)
        puts = sorted(((float(r.get("PE", {}).get("oi") or 0), k) for k, r in self.rows.items()), reverse=True)
        return {"call_walls": [k for _, k in calls[:count]],
                "put_walls": [k for _, k in puts[:count]]}

    def straddle(self, now=None) -> dict:
        atm = self.nearest_strike()
        call = self.premium(atm, "CE")
        put = self.premium(atm, "PE")
        if call is None or put is None:
            raise ChainError(f"no quotes at the ATM strike {atm}")
        total = call + put
        return {"strike": atm, "call": call, "put": put, "total": round(total, 2),
                "expected_move": round(total * 0.85, 2),
                "expected_move_pct": round(100 * total * 0.85 / self.spot, 2)}

    def expected_move(self, now=None, sigma_multiple=1.0) -> dict:
        """One-sigma move to expiry from the ATM implied vol, in points and %."""
        atm_iv = self.atm_iv(now)
        years = bs.years_to_expiry(clock.now_ist(now), self.expiry)
        move = self.spot * atm_iv * math.sqrt(max(years, 0.0)) * sigma_multiple
        return {"points": round(move, 1), "pct": round(100 * move / self.spot, 2),
                "iv": round(atm_iv, 4), "days": max(0.0, years * 365)}

    def skew(self) -> dict:
        """25-delta-ish skew: are puts bid relative to calls?"""
        if not self.real:
            raise ChainError("skew needs real implied vols")
        atm = self.nearest_strike()
        step = self.step()
        put_leg = self.leg(atm - 2 * step, "PE").get("iv")
        call_leg = self.leg(atm + 2 * step, "CE").get("iv")
        if not put_leg or not call_leg:
            raise ChainError("not enough IVs around the money")
        return {"put_iv": round(float(put_leg), 4), "call_iv": round(float(call_leg), 4),
                "skew": round(float(put_leg) - float(call_leg), 4),
                "read": "puts bid (fear)" if put_leg > call_leg + 0.005 else
                        "calls bid (greed)" if call_leg > put_leg + 0.005 else "flat"}

    # --------------------------------------------------------------- repricing
    def reprice(self, spot: float, right: str, strike: float, now=None, iv=None) -> float:
        """Option value at a new index level, holding IV and time fixed."""
        years = bs.years_to_expiry(clock.now_ist(now), self.expiry)
        sigma = iv or self.atm_iv(now)
        return round(bs.price(spot, strike, years, _rate(), sigma, right, _div()), 2)

    def describe(self) -> str:
        label = {"model": "MODELLED (Black-Scholes from the index level and India VIX - not a quote)",
                 "nse": "NSE option chain (live)", "bse": "BSE option chain (live)",
                 "dhan": "Dhan option chain (live - OI, IV, greeks, bid/ask)",
                 "csv": "imported chain file"}.get(self.source, self.source)
        return (f"{self.underlying} {self.expiry.strftime('%d %b %Y').lstrip('0')} chain, "
                f"spot {self.spot:,.2f}, {len(self.rows)} strikes - {label}")


# ----------------------------------------------------------------- loaders
def load(underlying, spot=None, expiry=None, config=None, now=None,
         source="auto", path=None, width=12, lot=None) -> Chain:
    """Get a chain for `underlying`. `source` = auto | dhan | csv | nse | bse | model."""
    moment = clock.now_ist(now)
    underlying = underlying.upper()

    from . import brokers
    if source in ("auto", "dhan") and (brokers.data_available(config or {}) or source == "dhan"):
        try:
            adapter = brokers.get_adapter("dhan", config or {})
            target_expiry = expiry or _expiry(underlying, moment, config)
            return adapter.chain(underlying, target_expiry)
        except Exception as exc:                                   # noqa: BLE001
            if source == "dhan":
                raise ChainError(f"dhan: {exc}") from None
    exchange = "BSE" if underlying in ("SENSEX", "BANKEX", "SENSEX50") else "NSE"
    spot = float(spot) if spot is not None else _spot(underlying, moment)
    expiry = expiry or _expiry(underlying, moment, config)
    rate, div = _rate(config), _div(config)
    model_iv = _vix_iv(moment)

    def modelled(reason=""):
        chain = _modelled_chain(underlying, exchange, expiry, spot, moment, model_iv,
                                width=width, lot=lot, config=config)
        if reason:
            chain.source = "model"
            chain.as_of = moment
            chain._reason = reason
        return chain

    if source == "model":
        return modelled()
    if source == "csv" or path:
        if not path:
            raise ChainError("source=csv needs a file path (--chain-file)")
        return _from_csv(path, underlying, exchange, expiry, spot, lot)

    errors = []
    if source in ("auto", "nse") and exchange == "NSE":
        try:
            return _from_nse(underlying, expiry, spot, moment, lot)
        except Exception as exc:                                   # noqa: BLE001
            errors.append(f"nse: {exc}")
    if source in ("auto", "bse") and exchange == "BSE":
        try:
            return _from_bse(underlying, expiry, spot, moment, lot)
        except Exception as exc:                                   # noqa: BLE001
            errors.append(f"bse: {exc}")
    if source in ("nse", "bse"):
        raise ChainError("; ".join(errors) or f"no live chain adapter for {underlying}")
    return modelled("; ".join(errors))


def _spot(underlying, now=None) -> float:
    symbol = _spot_symbol(underlying)
    if not symbol:
        raise ChainError(f"no free index feed for {underlying}; pass --spot")
    return feed.quote(symbol)["price"]


def _spot_symbol(underlying):
    from .instruments import UNDERLYINGS
    return UNDERLYINGS.get(underlying.upper(), {}).get("spot")


def _expiry(underlying, now=None, config=None):
    from .instruments import Master
    master = Master.load(config, now=now)
    expiry = master.nearest_expiry(underlying, clock.now_ist(now).date())
    if expiry is None:
        raise ChainError(f"no live expiry for {underlying}")
    return expiry


def _rate(config=None) -> float:
    return float((config or {}).get("risk_free_rate", 0.065))


def _div(config=None) -> float:
    return float((config or {}).get("dividend_yield", 0.012))


def _vix_iv(now=None) -> float:
    try:
        return max(0.05, feed.india_vix(now)["value"] / 100.0)
    except Exception:                                              # noqa: BLE001
        return 0.14


# ------------------------------------------------------------------ modelled
def _modelled_chain(underlying, exchange, expiry, spot, now, atm_iv, width=12, lot=None, config=None):
    """A full ladder priced off the live index level - clearly labelled MODELLED."""
    from .instruments import Master
    try:
        master = Master.load(config, now=now)
        step = master.step(underlying, expiry)
        lot = lot or master.lot(underlying, expiry)
    except Exception:                                              # noqa: BLE001
        step, lot = (50.0 if spot > 20000 else 100.0), lot
    years = bs.years_to_expiry(now, expiry)
    atm = round(spot / step) * step
    rate, div = _rate(config), _div(config)
    rows = {}
    for offset in range(-width, width + 1):
        strike = atm + offset * step
        z = math.log(strike / spot) / max(1e-6, atm_iv * math.sqrt(max(years, 1e-6)))
        iv = atm_iv * (1.0 + 0.020 * z * z - 0.015 * z)
        iv = min(max(iv, 0.04), 3.0)
        call = bs.price(spot, strike, years, rate, iv, "CE", div)
        put = bs.price(spot, strike, years, rate, iv, "PE", div)
        rows[strike] = {
            "CE": {"ltp": round(call, 2), "iv": round(iv, 4), "oi": None, "volume": None,
                   "bid": round(call * 0.995, 2), "ask": round(call * 1.005 + 0.05, 2)},
            "PE": {"ltp": round(put, 2), "iv": round(iv, 4), "oi": None, "volume": None,
                   "bid": round(put * 0.995, 2), "ask": round(put * 1.005 + 0.05, 2)},
        }
    chain = Chain(underlying, exchange, expiry, spot, rows, "model", now, lot)
    chain.model_iv = atm_iv
    return chain


# ----------------------------------------------------------------------- NSE
def _http(url, headers=None, timeout=20, cookies=None):
    request = urllib.request.Request(url, headers={**(headers or NSE_HEADERS)})
    if cookies:
        request.add_header("Cookie", cookies)
    with urllib.request.urlopen(request, timeout=timeout) as response:        # noqa: S310
        return response.read()


def _from_nse(underlying, expiry, spot, now, lot):
    """NSE's public chain endpoint. Needs a warm-up request for the cookies."""
    if underlying not in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"):
        raise ChainError(f"NSE's index chain does not cover {underlying}")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar()))
    try:
        opener.open(urllib.request.Request(NSE_HOME, headers=NSE_HEADERS), timeout=20).read()  # noqa: S310
    except Exception as exc:                                       # noqa: BLE001
        raise ChainError(f"could not open an NSE session ({exc.__class__.__name__})") from None
    payload = json.loads(opener.open(urllib.request.Request(
        NSE_CHAIN.format(symbol=underlying), headers=NSE_HEADERS), timeout=25).read())
    records = ((payload.get("records") or {}).get("data") or [])
    stamp = ((payload.get("records") or {}).get("timestamp"))
    rows = {}
    for item in records:
        if item.get("expiryDate") is None:
            continue
        exp = dt.datetime.strptime(item["expiryDate"], "%d-%b-%Y").date()
        if exp != expiry:
            continue
        strike = float(item["strikePrice"])
        row = rows.setdefault(strike, {})
        for right in ("CE", "PE"):
            if right in item:
                row[right] = _nse_leg(item[right])
    if not rows:
        raise ChainError(f"NSE returned no rows for the {expiry} expiry")
    chain = Chain(underlying, "NSE", expiry, spot, rows, "nse", now, lot)
    if stamp:
        chain.as_of = stamp
    return chain


def _jar():
    import http.cookiejar
    return http.cookiejar.CookieJar()


def _nse_leg(payload: dict) -> dict:
    return {
        "ltp": payload.get("lastPrice"),
        "iv": (payload.get("impliedVolatility") or 0) / 100.0 or None,
        "oi": payload.get("openInterest"),
        "oi_change": payload.get("changeinOpenInterest"),
        "volume": payload.get("totalTradedVolume"),
        "bid": payload.get("bidprice"),
        "ask": payload.get("askPrice"),
    }


# ----------------------------------------------------------------------- BSE
def _from_bse(underlying, expiry, spot, now, lot):
    scrip = {"SENSEX": "1", "BANKEX": "12"}.get(underlying)
    if not scrip:
        raise ChainError(f"BSE's chain does not cover {underlying}")
    payload = json.loads(_http(
        BSE_CHAIN.format(scrip=scrip, expiry=expiry.strftime("%d/%m/%Y")),
        headers={"User-Agent": NSE_HEADERS["User-Agent"],
                 "Referer": "https://www.bseindia.com/", "Accept": "application/json"},
    ))
    rows = {}
    for item in payload if isinstance(payload, list) else payload.get("Table", []):
        try:
            strike = float(item.get("StrikePrice") or item.get("strike"))
        except (TypeError, ValueError):
            continue
        row = rows.setdefault(strike, {})
        for right, prefix in (("CE", "C"), ("PE", "P")):
            value = item.get(f"{prefix}LTP") or item.get(f"{right}LTP")
            if value is None:
                continue
            row[right] = {"ltp": value, "oi": item.get(f"{prefix}OI") or item.get("OI"),
                          "volume": item.get(f"{prefix}Volume"), "iv": None,
                          "bid": item.get(f"{prefix}BidRate"), "ask": item.get(f"{prefix}AskRate")}
    if not rows:
        raise ChainError("BSE returned no rows")
    return Chain(underlying, "BSE", expiry, spot, rows, "bse", now, lot)


# ----------------------------------------------------------------------- CSV
CSV_ALIASES = {
    "strike": ("strike", "strikeprice", "strike_price", "strikeprice(₹)"),
    "ce_ltp": ("ce_ltp", "call_ltp", "close", "ce_close", "call"),
    "pe_ltp": ("pe_ltp", "put_ltp", "pe_close", "put"),
    "ce_oi": ("ce_oi", "call_oi", "ce_openinterest"),
    "pe_oi": ("pe_oi", "put_oi", "pe_openinterest"),
    "ce_vol": ("ce_vol", "call_volume", "ce_volume"),
    "pe_vol": ("pe_vol", "put_volume", "pe_volume"),
    "ce_iv": ("ce_iv", "call_iv", "ce_impliedvolatility"),
    "pe_iv": ("pe_iv", "put_iv", "pe_impliedvolatility"),
    "ce_bid": ("ce_bid", "call_bid"), "ce_ask": ("ce_ask", "call_ask"),
    "pe_bid": ("pe_bid", "put_bid"), "pe_ask": ("pe_ask", "put_ask"),
}


def _from_csv(path, underlying, exchange, expiry, spot, lot):
    if not os.path.exists(path):
        raise ChainError(f"no such chain file: {path}")
    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        text = handle.read()
    if path.endswith(".json"):
        payload = json.loads(text)
        rows = {float(k): v for k, v in (payload.get("rows") or payload).items()}
        expiry = dt.date.fromisoformat(payload["expiry"]) if payload.get("expiry") else expiry
        spot = float(payload.get("spot", spot))
        return Chain(underlying, exchange, expiry, spot, rows, "csv", clock.now_ist(), lot)

    reader = csv.DictReader(io.StringIO(text))
    columns = {_slug(name): name for name in (reader.fieldnames or [])}
    lookup = {}
    for key, aliases in CSV_ALIASES.items():
        for alias in aliases:
            if _slug(alias) in columns:
                lookup[key] = columns[_slug(alias)]
                break
    if "strike" not in lookup:
        raise ChainError("the chain file needs a strike column")
    rows = {}
    for raw in reader:
        try:
            strike = float(str(raw[lookup["strike"]]).replace(",", ""))
        except (KeyError, TypeError, ValueError):
            continue
        row = {}
        for right, prefix in (("CE", "ce"), ("PE", "pe")):
            leg = {}
            for field, suffix in (("ltp", "ltp"), ("oi", "oi"), ("volume", "vol"),
                                  ("iv", "iv"), ("bid", "bid"), ("ask", "ask")):
                column = lookup.get(f"{prefix}_{suffix}")
                if column and raw.get(column) not in (None, "", "-"):
                    try:
                        leg[field] = float(str(raw[column]).replace(",", "").replace("%", ""))
                    except ValueError:
                        continue
            if leg:
                if leg.get("iv", 0) and leg["iv"] > 3:
                    leg["iv"] = leg["iv"] / 100.0
                row[right] = leg
        if row:
            rows[strike] = row
    if not rows:
        raise ChainError(f"no usable rows in {path}")
    return Chain(underlying, exchange, expiry, spot, rows, "csv", clock.now_ist(), lot)


def _slug(name: str) -> str:
    return "".join(ch for ch in str(name).strip().lower() if ch.isalnum())
