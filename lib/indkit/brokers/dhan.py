"""Dhan (DhanHQ v2) - option chain, quotes, and real orders.

Why this module exists: the pack's weakest number was a MODELLED premium. With
Dhan the chain is real - LTP, open interest, IV, greeks, best bid/ask and the
exchange security id for every leg - and the same creds also reach the order
API, so the ticket can actually be filled.

Auth (never in the chat, never in config.json):

    DHAN_CLIENT_ID       your Dhan client id
    DHAN_ACCESS_TOKEN    the DhanHQ v2 access token (about 30 days)

Endpoints used (v2):
    POST   /optionchain                 the chain for one expiry
    POST   /optionchain/expirylist      every active expiry
    POST   /marketfeed/ltp              index / option last price
    GET    /fundlimit                   available cash
    GET    /positions                   open positions
    POST   /orders                      place
    GET    /orders/{id}                 status
    DELETE /orders/{id}                 cancel
    GET    /orders                      the day's book
    POST   /killswitch?status=...       the emergency stop

Rate limits, per Dhan's docs: option chain 1 request per 3 seconds (OI updates
slowly), data APIs 5/second, order APIs 10/second and 7000/day. This module
throttles the chain and caches it for 3 seconds, so a skill that asks twice in a
row does not burn a request.

Order placement needs a STATIC IP whitelisted on the Dhan account (SEBI's algo
rule). Without it the order API answers with an error, and this module says so
in plain words instead of guessing.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time as _time
import urllib.error
import urllib.parse
import urllib.request

from .. import clock

BASE = "https://api.dhan.co/v2"
USER_AGENT = "Mozilla/5.0 (compatible; nifty-options-skill/1.0)"

# Verified against Dhan's public scrip master (api-scrip-master.csv) on 2026-11-09:
# SEM_SMST_SECURITY_ID for segment "I", instrument INDEX.
UNDERLYINGS = {
    "NIFTY":      {"security_id": 13,  "seg": "IDX_I", "fno": "NSE_FNO", "exchange": "NSE"},
    "BANKNIFTY":  {"security_id": 25,  "seg": "IDX_I", "fno": "NSE_FNO", "exchange": "NSE"},
    "FINNIFTY":   {"security_id": 27,  "seg": "IDX_I", "fno": "NSE_FNO", "exchange": "NSE"},
    "NIFTYNXT50": {"security_id": 38,  "seg": "IDX_I", "fno": "NSE_FNO", "exchange": "NSE"},
    "MIDCPNIFTY": {"security_id": 442, "seg": "IDX_I", "fno": "NSE_FNO", "exchange": "NSE"},
    "SENSEX":     {"security_id": 51,  "seg": "IDX_I", "fno": "BSE_FNO", "exchange": "BSE"},
    "BANKEX":     {"security_id": 69,  "seg": "IDX_I", "fno": "BSE_FNO", "exchange": "BSE"},
}
INDIA_VIX_ID = 21

# Dhan's documented error codes, in plain words.
ERRORS = {
    "DH-901": "the Dhan client id or access token is invalid or expired",
    "DH-902": "this account has not subscribed to the Data APIs (or has no Trading API access)",
    "DH-903": "the account cannot place this: check segment activation and any account restriction",
    "DH-904": "rate limit: too many requests. Wait and retry",
    "DH-905": "the request was rejected for a missing or bad field",
    "DH-906": "Dhan refused the order itself (margin, freeze quantity, market closed, or an order-API restriction)",
    "DH-907": "no data for those parameters (wrong expiry or security id)",
    "DH-908": "Dhan had an internal error; retry shortly",
    "DH-909": "network error between Dhan's edge and its backend",
    "DH-910": "Dhan returned an unspecified error",
    "806": "Data APIs are not subscribed on this account",
    "807": "the access token has expired (they last about 30 days)",
    "808": "authentication failed: client id or token invalid",
    "809": "the access token is invalid",
    "810": "the client id is invalid",
    "811": "invalid expiry date for this underlying",
    "813": "invalid security id",
}

ORDER_TYPE_MAP = {
    "market": "MARKET",
    "limit": "LIMIT",
    "sl": "STOP_LOSS",
    "sl-l": "STOP_LOSS",
    "sl-m": "STOP_LOSS_MARKET",
    "stop_loss": "STOP_LOSS",
    "stop_loss_market": "STOP_LOSS_MARKET",
}


class DhanError(RuntimeError):
    def __init__(self, message, code=None, status=None, payload=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.payload = payload or {}

    @property
    def hint(self):
        if self.code in ("DH-901", "807", "808", "809", "810"):
            return ("Check DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN. Dhan tokens last about 30 days; "
                    "regenerate one in the Dhan web console.")
        if self.code in ("DH-902", "806"):
            return "Subscribe to the Dhan Data APIs (they are a paid add-on) and retry."
        if self.code == "DH-904":
            return "Back off: the option chain allows one request every 3 seconds."
        if self.code in ("DH-906", "DH-905"):
            return ("Order placement needs a STATIC IP whitelisted on your Dhan account "
                    "(DhanHQ docs > Authentication > Setup Static IP). Without it the order API refuses.")
        return ""


class Dhan:
    """A thin, synchronous, standard-library client for DhanHQ v2."""

    def __init__(self, client_id=None, access_token=None, timeout=20, transport=None, throttle=True):
        self._last_chain_call = 0.0
        self._chain_cache = {}          # (underlying, expiry) -> (fetched_at, chain), 3s TTL
        self._expiry_cache = {}         # underlying -> (fetched_at, [dates]), 1h TTL
        self.client_id = client_id or os.environ.get("DHAN_CLIENT_ID")
        self.access_token = access_token or os.environ.get("DHAN_ACCESS_TOKEN")
        self.timeout = timeout
        self._transport = transport          # injectable for tests: fn(method, url, headers, body) -> (status, bytes)
        self.throttle = throttle

    # ------------------------------------------------------------------ auth
    @classmethod
    def from_env(cls, config=None) -> "Dhan":
        return cls()

    def available(self) -> bool:
        return bool(self.client_id and self.access_token)

    def _headers(self):
        return {
            "access-token": self.access_token or "",
            "client-id": str(self.client_id or ""),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }

    # ------------------------------------------------------------- transport
    def _request(self, method, path, body=None, query=None):
        url = BASE + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        if body is not None:
            # Dhan's option-chain endpoints use capitalised keys; orders use camelCase.
            data = json.dumps(body).encode()
        if self._transport is not None:
            status, raw = self._transport(method, url, self._headers(), data)
        else:
            request = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:   # noqa: S310
                    status, raw = response.status, response.read()
            except urllib.error.HTTPError as exc:
                status, raw = exc.code, exc.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                raise DhanError(f"could not reach Dhan ({exc.__class__.__name__})") from None
        text = (raw or b"").decode("utf-8", "replace")
        payload = {}
        if text:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = {"raw": text[:300]}
        if status >= 400 or (isinstance(payload, dict)
                             and str(payload.get("status", "")).lower() in ("failure", "failed", "error")):
            code, message = self._error_from(payload, status)
            friendly = ERRORS.get(code, "")
            raise DhanError(f"{message}{' - ' + friendly if friendly else ''}",
                            code=code, status=status, payload=payload if isinstance(payload, dict) else {})
        return payload

    @staticmethod
    def _error_from(payload, status):
        """Dhan answers two shapes: {errorCode, errorMessage} and {"data":{"808":"..."}}."""
        if not isinstance(payload, dict):
            return "", f"HTTP {status}"
        code = str(payload.get("errorCode") or payload.get("error_code") or "")
        message = payload.get("errorMessage") or payload.get("error_message") or ""
        data = payload.get("data")
        if isinstance(data, dict):
            if not code:
                for key in data:                      # {"808": "Authentication Failed - ..."}
                    text = str(key)
                    if text.isdigit() or text.startswith("DH-"):
                        code = text
                        break
            if not message and code and isinstance(data.get(code), str):
                message = data[code]
            if not message and isinstance(data.get("errorMessage"), str):
                message = data["errorMessage"]
        if not message:
            message = payload.get("raw") or f"HTTP {status}"
        return code, message

    # ------------------------------------------------------------------ data
    def expiries(self, underlying, fresh=False):
        """Every active expiry for an index, as dates."""
        spec = self._spec(underlying)
        if not fresh:
            cached = self._expiry_cache.get(underlying)
            if cached and (_time.time() - cached[0]) < 3600:
                return list(cached[1])
        if not self.available():
            raise DhanError("Dhan credentials are missing: export DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN",
                            code="DH-901")
        payload = self._request("POST", "/optionchain/expirylist",
                                {"UnderlyingScrip": spec["security_id"], "UnderlyingSeg": spec["seg"]})
        days = [dt.date.fromisoformat(str(item)[:10]) for item in (payload.get("data") or [])]
        self._expiry_cache[underlying] = (_time.time(), days)
        return days

    def chain(self, underlying, expiry, fresh=False):
        """The full option chain for one expiry, as indkit.chain.Chain."""
        from ..chain import Chain, ChainError

        if not self.available():
            raise DhanError("Dhan credentials are missing: export DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN "
                            "(Data API subscription needed for the option chain)", code="DH-901")
        spec = self._spec(underlying)
        expiry = expiry if isinstance(expiry, dt.date) else dt.date.fromisoformat(str(expiry)[:10])
        key = (underlying, expiry.isoformat())
        if not fresh:
            cached = self._chain_cache.get(key)
            if cached and (_time.time() - cached[0]) < 3.0:
                return cached[1]
        if self.throttle:
            wait = 3.0 - (_time.time() - self._last_chain_call)
            if wait > 0:
                _time.sleep(wait)
        payload = self._request("POST", "/optionchain", {
            "UnderlyingScrip": spec["security_id"],
            "UnderlyingSeg": spec["seg"],
            "Expiry": expiry.isoformat(),
        })
        self._last_chain_call = _time.time()

        data = payload.get("data") or {}
        spot = data.get("last_price")
        rows = {}
        for strike_text, arms in (data.get("oc") or {}).items():
            try:
                strike = float(strike_text)
            except (TypeError, ValueError):
                continue
            row = {}
            for right, arm in (("CE", "ce"), ("PE", "pe")):
                leg = arms.get(arm) or {}
                if not leg:
                    continue
                iv = leg.get("implied_volatility")
                row[right] = {
                    "ltp": leg.get("last_price"),
                    "iv": (float(iv) / 100.0 if iv and float(iv) > 3 else iv),
                    "oi": leg.get("oi"),
                    "oi_change": (leg.get("oi") or 0) - (leg.get("previous_oi") or 0)
                    if leg.get("oi") is not None and leg.get("previous_oi") is not None else None,
                    "volume": leg.get("volume"),
                    "bid": leg.get("top_bid_price"),
                    "ask": leg.get("top_ask_price"),
                    "bid_qty": leg.get("top_bid_quantity"),
                    "ask_qty": leg.get("top_ask_quantity"),
                    "security_id": leg.get("security_id"),
                    "greeks": leg.get("greeks") or {},
                    "average_price": leg.get("average_price"),
                    "previous_close": leg.get("previous_close_price"),
                }
            if row:
                rows[strike] = row
        if not rows:
            raise ChainError(f"Dhan returned no strikes for {underlying} {expiry}")
        if spot is None:
            raise ChainError("Dhan's chain response had no underlying price")
        chain = Chain(underlying, spec["exchange"], expiry, float(spot), rows, "dhan", clock.now_ist())
        try:
            from ..instruments import Master
            master = Master.load({}, now=None)
            chain.lot = master.lot(underlying, expiry)
        except Exception:                                          # noqa: BLE001
            pass
        self._chain_cache[key] = (_time.time(), chain)
        return chain

    def spot(self, underlying):
        """Index last price from Dhan (consistent timestamps with the chain)."""
        spec = self._spec(underlying)
        payload = self._request("POST", "/marketfeed/ltp", {"IDX_I": [spec["security_id"]]})
        arm = ((payload.get("data") or {}).get("IDX_I") or {}).get(str(spec["security_id"])) \
            or ((payload.get("data") or {}).get("IDX_I") or {}).get(spec["security_id"])
        if not arm:
            raise DhanError(f"no last price for {underlying}")
        return float(arm.get("last_price"))

    def india_vix(self):
        payload = self._request("POST", "/marketfeed/ltp", {"IDX_I": [INDIA_VIX_ID]})
        arm = ((payload.get("data") or {}).get("IDX_I") or {}).get(str(INDIA_VIX_ID))
        return float(arm.get("last_price")) if arm else None

    # --------------------------------------------------------------- account
    def funds(self):
        return self._request("GET", "/fundlimit")

    def available_cash(self):
        funds = self.funds() or {}
        for key in ("availabelBalance", "availableBalance", "available_balance"):
            if funds.get(key) is not None:
                return float(funds[key])
        return None

    def positions(self):
        payload = self._request("GET", "/positions")
        return payload if isinstance(payload, list) else (payload.get("data") or [])

    def orders(self):
        payload = self._request("GET", "/orders")
        return payload if isinstance(payload, list) else (payload.get("data") or [])

    def order_status(self, order_id):
        return self._request("GET", f"/orders/{order_id}")

    # ---------------------------------------------------------------- trading
    def build_order(self, *, transaction, segment, security_id, quantity,
                    order_type="MARKET", product="CNC", validity="DAY",
                    price=None, trigger=None, correlation=None):
        """One order payload, in exactly the field names Dhan documents."""
        dtype = ORDER_TYPE_MAP.get(str(order_type).lower(), str(order_type).upper())
        payload = {
            "dhanClientId": str(self.client_id or ""),
            "transactionType": str(transaction).upper(),
            "exchangeSegment": segment,
            "productType": product,
            "orderType": dtype,
            "validity": validity,
            "securityId": str(int(security_id)),
            "quantity": int(quantity),
            "disclosedQuantity": "",
            "price": float(price) if (dtype == "LIMIT" and price) else "",
            "triggerPrice": float(trigger) if (dtype in ("STOP_LOSS", "STOP_LOSS_MARKET") and trigger is not None) else "",
            "afterMarketOrder": False,
        }
        if correlation:
            payload["correlationId"] = str(correlation)[:30]
        if dtype == "LIMIT" and not price:
            raise DhanError("a LIMIT order needs a price")
        if dtype in ("STOP_LOSS", "STOP_LOSS_MARKET") and trigger is None:
            raise DhanError("an SL order needs a trigger price")
        return payload

    def place(self, payload):
        response = self._request("POST", "/orders", payload)
        if not isinstance(response, dict) or response.get("orderId") is None:
            raise DhanError(f"Dhan did not return an order id: {response}")
        return {"orderId": str(response.get("orderId")), "orderStatus": response.get("orderStatus")}

    def cancel(self, order_id):
        return self._request("DELETE", f"/orders/{order_id}")

    def killswitch(self, activate=True):
        return self._request("POST", "/killswitch",
                             query={"status": "activate" if activate else "deactivate"})

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _spec(underlying):
        spec = UNDERLYINGS.get(str(underlying).upper())
        if not spec:
            raise DhanError(f"Dhan does not carry index options for {underlying} "
                            f"(known: {', '.join(sorted(UNDERLYINGS))})")
        return spec

    @classmethod
    def segment_for(cls, underlying):
        return cls._spec(underlying)["fno"]

    def wait_for_fill(self, order_id, tries=10, pause=1.0):
        """Poll an order until it is traded, rejected or out of tries."""
        status = {}
        for attempt in range(max(1, tries)):
            status = self.order_status(order_id) or {}
            state = str(status.get("orderStatus") or "").upper()
            if state in ("TRADED", "REJECTED", "CANCELLED", "EXPIRED", "PART_TRADED"):
                if state != "PART_TRADED" or attempt >= tries - 1:
                    break
            _time.sleep(pause)
        return status

    def describe(self):
        return (f"Dhan v2 ({self.client_id or 'no client id'}, "
                f"token {'present' if self.access_token else 'MISSING'})")
