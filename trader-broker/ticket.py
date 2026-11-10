#!/usr/bin/env python3
"""Order Executor - the ticket, the paper fill, and the real Dhan order.

    python3 ticket.py show        # what is in trader/orders.json, READY or not
    python3 ticket.py preflight   # every check the live path makes, sending nothing
    python3 ticket.py place --yes # paper fill, or a real Dhan order when live
    python3 ticket.py status      # open positions, premium P&L and R
    python3 ticket.py flatten --yes  # close everything at the current premium
    python3 ticket.py book        # today's Dhan order book

Locks, all of which must be open before anything leaves the machine:
  1. trader-risk PASSED (or WARNed) this exact order today, by fingerprint,
  2. you typed --yes for this exact ticket,
  3. config.json says paper=false, live_broker="dhan", live_trading_enabled=true,
  4. preflight passes: token present, market open, quote not slipped past your cap,
     cash for the premium, and a security id for the exact contract,
  5. on a real order, SEBI's route: a static IP whitelisted on your Dhan account.

Paper stays the default. Turning it off is a config edit you make on purpose.

Two things this tool will not pretend about:
  * Dhan has no native OCO. The stop and the target are two separate orders, so
    one filling leaves the other working - `status` cancels the orphan, and so
    does `flatten`.
  * A rejected order is reported with Dhan's own error code and what it means.
    It never silently retries a partially filled position.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib")))

from indkit import cli, clock, costs, store, workflow  # noqa: E402
from indkit import chain as chains                     # noqa: E402
from indkit import brokers                             # noqa: E402

LIVE_STATUSES = ("TRADED", "PART_TRADED")
DEAD_STATUSES = ("REJECTED", "CANCELLED", "EXPIRED")


# --------------------------------------------------------------------- tickets
def todays_verdicts(config, now):
    record = store.read_json("state/risk.json", config, default={}) or {}
    today = clock.now_ist(now).date().isoformat()
    if record.get("day") != today:
        return {}
    return {v["fingerprint"]: v for v in record.get("verdicts", [])}


def tickets(config, now):
    payload = store.read_json("orders.json", config, default=None)
    if not payload:
        return []
    orders = payload.get("orders") if isinstance(payload, dict) else payload
    verdicts = todays_verdicts(config, now)
    out = []
    for order in orders or []:
        fingerprint = store.fingerprint(order)
        verdict = verdicts.get(fingerprint)
        out.append({
            "order": order,
            "fingerprint": fingerprint,
            "verdict": (verdict or {}).get("verdict", "NO VERDICT"),
            "reasons": (verdict or {}).get("reasons", []),
            "ready": bool(verdict) and verdict.get("verdict") in ("PASS", "WARN"),
        })
    return out


def render_ticket(item):
    order = item["order"]
    lots = order.get("lots")
    print(f"{'READY' if item['ready'] else 'NOT READY':<9} {order.get('side','?').upper()} "
          f"{order.get('qty')} units" + (f" ({lots} lot{'s' if lots != 1 else ''})" if lots else "") +
          f" of {order.get('symbol')} on {order.get('exchange')}")
    print(f"          premium Rs {float(order.get('premium_entry') or 0):,.2f} a unit  ->  "
          f"Rs {float(order.get('premium_entry') or 0) * float(order.get('qty') or 0):,.0f} paid upfront")
    if order.get("premium_stop"):
        print(f"          stop   Rs {order['premium_stop']:,.2f}  (index {order.get('stop_index') or '-'})")
    if order.get("premium_target"):
        print(f"          target Rs {order['premium_target']:,.2f}  (index {order.get('target_index') or '-'})")
    print(f"          risk at the stop Rs {float(order.get('risk') or 0):,.0f}, "
          f"reward Rs {float(order.get('reward') or 0):,.0f}, R:R {order.get('reward_risk')}")
    print(f"          order type {order.get('order_type')}, product {order.get('product')}, "
          f"validity {order.get('validity')}, expiry {order.get('expiry')}")
    print(f"          premium source {order.get('premium_source', '-')}")
    print(f"          fingerprint {item['fingerprint']} - verdict {item['verdict']}")
    for reason in item["reasons"]:
        print(f"          BLOCK: {reason}")
    if not item["ready"] and item["verdict"] == "NO VERDICT":
        print("          no risk verdict for this exact order today: run /trader-risk")


# ---------------------------------------------------------------- the live path
def adapter_for(config):
    """The configured broker adapter, or None - with a readable reason."""
    if not config.get("live_broker"):
        return None
    return brokers.get_adapter(config=config)


def live_mode(config):
    return (not config.get("paper", True)
            and str(config.get("live_broker") or "").lower() == "dhan"
            and bool(config.get("live_trading_enabled", False)))


def contract_quote(order, config, now):
    """Real premium, security id and segment for this exact contract, from Dhan."""
    expiry = order.get("expiry")
    if isinstance(expiry, str):
        expiry = dt.date.fromisoformat(expiry[:10])
    chain = chains.load(str(order.get("underlying")).upper(), expiry=expiry,
                        config=config, now=now, source="auto")
    strike = float(order.get("strike"))
    right = str(order.get("right", "CE")).upper()
    leg = chain.leg(strike, right)
    if leg is None:
        raise chains.ChainError(f"no {strike:g} {right} in the {chain.underlying} chain")
    premium = leg.get("ltp")
    if premium is None:
        raise chains.ChainError(f"Dhan returned no last price for {strike:g} {right}")
    return {"premium": float(premium), "security_id": leg.get("security_id"),
            "bid": leg.get("bid"), "ask": leg.get("ask"), "iv": leg.get("iv"),
            "chain": chain, "spot": chain.spot, "real_chain": chain.real}


def preflight(item, config, now):
    """Every gate the live path applies, in the order they can fail."""
    order = item["order"]
    moment = clock.now_ist(now)
    report = {"blockers": [], "warnings": [], "quote": None, "security_id": None,
              "segment": None, "cash": None, "outlay": None, "fingerprint": item["fingerprint"]}

    if not item["ready"]:
        report["blockers"].append(f"no risk PASS for this order today ({item['verdict']})")

    if config.get("paper", True):
        report["warnings"].append("paper is on, so place would only paper-fill")
    if not config.get("live_trading_enabled", False):
        report["blockers"].append("config live_trading_enabled is false")
    adapter = adapter_for(config)
    if adapter is None:
        report["blockers"].append("config live_broker is not set to a broker")
        return report
    if not adapter.available():
        report["blockers"].append("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN are not both set")
        return report

    if not clock.is_trading_day(moment.date()):
        report["blockers"].append(f"{moment.date()} is not a trading day ({clock.holiday_name(moment.date())})")
    elif not clock.is_open(moment):
        report["blockers"].append(f"market is closed ({clock.market_state(moment)})")
    elif moment.time() > clock._parse_time(config.get("flat_by", "15:15"), dt.time(15, 15)):
        report["blockers"].append(f"past flat_by {config.get('flat_by', '15:15')}: no new entries")

    try:
        quote = contract_quote(order, config, now)
    except Exception as exc:                                       # noqa: BLE001
        report["blockers"].append(f"no real quote for this contract: {exc}")
        return report

    plan = float(order.get("premium_entry") or 0)
    live = quote["premium"]
    slip_pct = ((live - plan) / plan * 100.0) if plan else 0.0
    cap = float(config.get("max_entry_slippage_pct", 2.0))
    report.update({"quote": live, "plan": plan, "slippage_pct": round(slip_pct, 2),
                   "security_id": quote["security_id"], "segment": brokers.dhan.Dhan.segment_for(order["underlying"])})

    if live <= 0:
        report["blockers"].append(f"Dhan last price is {live}: not tradable")
    if slip_pct > cap:
        report["blockers"].append(f"premium slipped {slip_pct:+.1f}% (plan Rs {plan:,.2f}, now Rs {live:,.2f}); "
                                  f"cap is {cap:.1f}% - re-run /trader-entry if you still want it")
    elif slip_pct > cap * 0.5:
        report["warnings"].append(f"premium slipped {slip_pct:+.1f}% from the plan")
    if slip_pct < -8:
        report["warnings"].append(f"premium is {slip_pct:+.1f}% below the plan: the level may have moved. "
                                  f"The stop and target were sized for Rs {plan:,.2f}")
    if not quote["security_id"]:
        report["blockers"].append("Dhan's chain gave no security_id for this leg")
    if not quote["real_chain"]:
        report["blockers"].append("the quote came from a model, not from Dhan")

    units = int(order.get("qty") or 0)
    report["outlay"] = round(live * units, 2)
    if report["blockers"]:
        # Already unpromising: do not spend an API call on the fund limit.
        report["warnings"].append("order placement needs a STATIC IP whitelisted on your Dhan account")
        return report
    try:
        cash = adapter.available_cash()
        report["cash"] = cash
        if cash is not None and cash < report["outlay"] * 1.02:
            report["blockers"].append(f"available cash Rs {cash:,.0f} < premium Rs {report['outlay']:,.0f}")
    except Exception as exc:                                       # noqa: BLE001
        report["warnings"].append(f"could not read fund limit ({exc}); check margin in the Dhan app")

    report["warnings"].append("order placement needs a STATIC IP whitelisted on your Dhan account")
    return report


def render_preflight(report):
    print(f"fingerprint {report['fingerprint']}")
    if report.get("quote") is not None:
        print(f"  real premium Rs {report['quote']:,.2f} vs plan Rs {report.get('plan', 0):,.2f} "
              f"({report.get('slippage_pct', 0):+.2f}%)")
        print(f"  security id {report['security_id']} on {report['segment']}, "
              f"premium outlay Rs {report['outlay']:,.0f}, cash Rs {report['cash'] if report['cash'] is None else format(report['cash'], ',.0f')}")
    for warning in report["warnings"]:
        print(f"  WARN  {warning}")
    for blocker in report["blockers"]:
        print(f"  BLOCK {blocker}")
    print("  preflight PASS - nothing has been sent" if not report["blockers"] else "  preflight FAIL")


def _correlation(fingerprint, leg):
    return f"nt{fingerprint[:14]}-{leg}"[:30]


def _status_field(status, *names, default=None):
    for name in names:
        if isinstance(status, dict) and status.get(name) not in (None, "", "0.00"):
            return status.get(name)
    return default


def _fill_price(status, fallback):
    value = _status_field(status, "averageTradedPrice", "averagePrice", "avgTradedPrice", "tradedPrice")
    try:
        price = float(value)
        return price if price > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _filled_qty(status, fallback):
    value = _status_field(status, "filledQty", "filledQuantity", "tradedQuantity")
    try:
        qty = int(float(value))
        return qty if qty > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _broker_state(config):
    return store.read_json("state/broker.json", config, default={}) or {}


def _save_broker_state(state, config):
    store.write_json("state/broker.json", state, config)


def place_live(item, adapter, report, config, now):
    """Buy the option, then attach a stop and a target. Returns (position, exit code)."""
    order = item["order"]
    moment = clock.now_ist(now)
    units = int(order.get("qty") or 0)
    currency = float(report["quote"])
    product = str(config.get("live_product", "CNC")).upper()

    entry_payload = adapter.build_order(
        transaction="BUY", segment=report["segment"], security_id=report["security_id"],
        quantity=units, order_type=str(order.get("order_type") or "market"),
        product=product, validity="DAY",
        price=order.get("limit_price"), correlation=_correlation(item["fingerprint"], "e"))
    print("sending BUY:", json.dumps({k: v for k, v in entry_payload.items() if k != "dhanClientId"}))
    placed = adapter.place(entry_payload)
    order_id = placed["orderId"]
    print(f"  entry order {order_id} -> {placed.get('orderStatus')}")

    status = adapter.wait_for_fill(order_id,
                                   tries=int(config.get("order_poll_tries", 10)),
                                   pause=float(config.get("order_poll_seconds", 1.0)))
    state = str(status.get("orderStatus") or "").upper()
    if state in DEAD_STATUSES:
        print(f"  REJECTED by Dhan: {status.get('omsErrorDescription') or state}")
        store.write_json("state/live.json", {"last": {"at": moment.isoformat(timespec="seconds"),
                                                     "kind": "entry", "order_id": order_id,
                                                     "status": status}}, config)
        return None, 1
    if state not in LIVE_STATUSES:
        print(f"  order is {state or 'unknown'} after polling - check the Dhan app. NOT placing a stop "
              f"until it is filled: order {order_id}")
        return None, 1

    filled = _filled_qty(status, units)
    premium = _fill_price(status, currency)
    buy = costs.leg(premium, filled, "NSE" if report["segment"] == "NSE_FNO" else "BSE",
                    "buy", config.get("charges"), moment)
    print(f"  FILLED {filled} units at Rs {premium:,.2f} (charges about Rs {buy['total']:.2f})")

    # --- the exit legs: Dhan has no OCO, so stop and target are two orders ----
    exits = {}
    stop_premium = float(order.get("premium_stop") or 0) or None
    target_premium = float(order.get("premium_target") or 0) or None
    for leg, trigger, limit, order_type in (
            ("stop", stop_premium, None, "STOP_LOSS_MARKET"),
            ("target", None, target_premium, "LIMIT")):
        wanted = trigger if leg == "stop" else limit
        if not wanted:
            continue
        try:
            payload = adapter.build_order(
                transaction="SELL", segment=report["segment"], security_id=report["security_id"],
                quantity=filled, order_type=order_type, product=product, validity="DAY",
                price=limit, trigger=trigger, correlation=_correlation(item["fingerprint"], leg[0]))
            placed_exit = adapter.place(payload)
            exits[leg] = placed_exit["orderId"]
            print(f"  {leg:<6} order {placed_exit['orderId']} at Rs {wanted:,.2f} "
                  f"({'trigger' if leg == 'stop' else 'limit'}), qty {filled}")
        except Exception as exc:                                   # noqa: BLE001
            exits[leg] = None
            print(f"  !!! {leg} ORDER FAILED: {exc}")
            print(f"  !!! You are holding {filled} units of {order['symbol']} with one exit leg missing.")
            print(f"  !!! Place it by hand in the Dhan app, or flatten now: ticket.py flatten --yes")

    position = {
        "symbol": order["symbol"], "underlying": order.get("underlying"), "side": "long",
        "right": order.get("right"), "strike": order.get("strike"), "expiry": order.get("expiry"),
        "qty": filled, "lots": order.get("lots"), "entry_premium": premium,
        "stop_premium": stop_premium, "target_premium": target_premium,
        "risk": round((premium - stop_premium) * filled, 2) if stop_premium else order.get("risk"),
        "reward": round((target_premium - premium) * filled, 2) if target_premium else order.get("reward"),
        "entry_at": moment.isoformat(timespec="seconds"), "charges_entry": buy["total"],
        "premium_source": "dhan", "fingerprint": item["fingerprint"],
        "live": True, "broker": "dhan", "security_id": report["security_id"],
        "segment": report["segment"], "order_ids": {"entry": order_id, **exits},
        "entry_order_status": {}
    }
    state = _broker_state(config)
    state.setdefault("positions", []).append(position)
    state.setdefault("placed", []).append({
        "at": moment.isoformat(timespec="seconds"), "kind": "entry", "symbol": order["symbol"],
        "side": "buy", "qty": filled, "premium": premium, "risk": position["risk"],
        "charges": buy["total"], "paper": False, "broker": "dhan", "order_id": order_id,
        "fingerprint": item["fingerprint"],
    })
    _save_broker_state(state, config)
    store.append_jsonl("journal.jsonl", {
        "event": "entry", "at": moment.isoformat(timespec="seconds"), "symbol": order["symbol"],
        "underlying": order.get("underlying"), "right": order.get("right"),
        "strike": order.get("strike"), "expiry": order.get("expiry"), "qty": filled,
        "lots": order.get("lots"), "entry_premium": premium, "stop_premium": stop_premium,
        "target_premium": target_premium, "risk": position["risk"], "charges": buy["total"],
        "paper": False, "broker": "dhan", "order_id": order_id,
    }, config)
    store.write_json("state/live.json", {
        "last": {"at": moment.isoformat(timespec="seconds"), "kind": "entry", "order_id": order_id,
                 "status": status, "exits": exits},
        "orders": {order_id: status}, "updated": moment.isoformat(timespec="seconds")}, config)
    return position, 0


def flatten_live(position, adapter, config, now):
    """Cancel the working exits, then sell the position at market."""
    moment = clock.now_ist(now)
    ids = position.get("order_ids") or {}
    for leg in ("stop", "target"):
        order_id = ids.get(leg)
        if not order_id:
            continue
        try:
            status = adapter.order_status(order_id) or {}
            if str(status.get("orderStatus") or "").upper() in ("PENDING", "TRIGGERED", "TRANSIT"):
                adapter.cancel(order_id)
                print(f"  cancelled {leg} order {order_id}")
        except Exception as exc:                                   # noqa: BLE001
            print(f"  could not cancel {leg} order {order_id} ({exc}) - check the Dhan app")

    payload = adapter.build_order(
        transaction="SELL", segment=position.get("segment") or brokers.dhan.Dhan.segment_for(position["underlying"]),
        security_id=position["security_id"], quantity=int(position["qty"]), order_type="MARKET",
        product=str(config.get("live_product", "CNC")).upper(), validity="DAY",
        correlation=_correlation(str(position.get("fingerprint") or "flat"), "x"))
    placed = adapter.place(payload)
    order_id = placed["orderId"]
    status = adapter.wait_for_fill(order_id, tries=int(config.get("order_poll_tries", 10)),
                                   pause=float(config.get("order_poll_seconds", 1.0)))
    state = str(status.get("orderStatus") or "").upper()
    if state not in LIVE_STATUSES:
        print(f"  exit order {order_id} is {state or 'unknown'}: {status.get('omsErrorDescription') or ''}")
        print("  The position may still be open. Sell it in the Dhan app and re-check: ticket.py status")
        return None
    filled = _filled_qty(status, int(position["qty"]))
    exit_premium = _fill_price(status, float(position["entry_premium"]))
    exchange = "NSE" if str(position.get("segment", "NSE_FNO")) == "NSE_FNO" else "BSE"
    sell = costs.leg(exit_premium, filled, exchange, "sell", config.get("charges"), moment)
    pnl = ((exit_premium - float(position["entry_premium"])) * filled
           - float(position.get("charges_entry") or 0) - sell["total"])
    risk = float(position.get("risk") or 0)
    store.append_jsonl("journal.jsonl", {
        "event": "exit", "at": moment.isoformat(timespec="seconds"), "symbol": position["symbol"],
        "qty": filled, "entry_premium": position["entry_premium"], "exit_premium": exit_premium,
        "pnl": round(pnl, 2), "charges": round(sell["total"], 2),
        "r_multiple": round(pnl / risk, 3) if risk else None,
        "reason": "flatten", "paper": False, "broker": "dhan", "order_id": order_id,
    }, config)
    print(f"  SOLD {filled} x {position['symbol']} at Rs {exit_premium:,.2f}: P&L Rs {pnl:,.0f} "
          f"({(pnl / risk):+.2f}R)" if risk else f"  SOLD at Rs {exit_premium:,.2f}: P&L Rs {pnl:,.0f}")
    return {"exit_premium": exit_premium, "pnl": pnl, "order_id": order_id}


# --------------------------------------------------------------- paper trading
def paper_fill(item, config, now):
    """Fill the order at the plan premium, charge the real Indian costs."""
    order = item["order"]
    moment = clock.now_ist(now)
    premium = float(order.get("premium_entry") or 0)
    units = int(order.get("qty") or 0)
    exchange = "NSE" if str(order.get("exchange", "NFO")).upper() in ("NFO", "NSE") else "BSE"
    buy = costs.leg(premium, units, exchange, "buy", config.get("charges"), moment)
    position = {
        "symbol": order["symbol"],
        "underlying": order.get("underlying"),
        "side": "long",
        "right": order.get("right"),
        "strike": order.get("strike"),
        "expiry": order.get("expiry"),
        "qty": units,
        "lots": order.get("lots"),
        "entry_premium": premium,
        "stop_premium": order.get("premium_stop"),
        "target_premium": order.get("premium_target"),
        "risk": order.get("risk"),
        "reward": order.get("reward"),
        "entry_at": moment.isoformat(timespec="seconds"),
        "charges_entry": buy["total"],
        "premium_source": order.get("premium_source"),
        "fingerprint": item["fingerprint"],
        "live": False,
    }
    state = store.read_json("state/broker.json", config, default={}) or {}
    state.setdefault("placed", []).append({
        "at": moment.isoformat(timespec="seconds"),
        "kind": "entry",
        "symbol": order["symbol"],
        "side": "buy",
        "qty": units,
        "premium": premium,
        "risk": order.get("risk"),
        "charges": buy["total"],
        "paper": bool(config.get("paper", True)),
        "fingerprint": item["fingerprint"],
    })
    state.setdefault("positions", []).append(position)
    store.write_json("state/broker.json", state, config)
    store.append_jsonl("journal.jsonl", {
        "event": "entry",
        "at": moment.isoformat(timespec="seconds"),
        "symbol": order["symbol"],
        "underlying": order.get("underlying"),
        "right": order.get("right"),
        "strike": order.get("strike"),
        "expiry": order.get("expiry"),
        "qty": units,
        "lots": order.get("lots"),
        "entry_premium": premium,
        "stop_premium": order.get("premium_stop"),
        "target_premium": order.get("premium_target"),
        "risk": order.get("risk"),
        "charges": buy["total"],
        "paper": bool(config.get("paper", True)),
    }, config)
    return position, buy


def broker_payload(order, config):
    """The same order in three brokers' field names, to copy by hand."""
    symbol = order.get("symbol")
    qty = order.get("qty")
    return {
        "zerodha_kite": {
            "tradingsymbol": symbol, "exchange": order.get("exchange", "NFO"),
            "transaction_type": "BUY" if str(order.get("side")).lower() == "buy" else "SELL",
            "order_type": "MARKET" if order.get("order_type") == "market" else "LIMIT",
            "quantity": qty, "product": order.get("product", "NRML"), "validity": "DAY",
            **({"price": order.get("limit_price")} if order.get("limit_price") else {}),
        },
        "upstox_v2": {
            "instrument_token": f"{order.get('exchange','NFO')}_FO|{symbol}",
            "quantity": qty,
            "transaction_type": "BUY" if str(order.get("side")).lower() == "buy" else "SELL",
            "order_type": "MARKET", "product": "D", "validity": "DAY",
        },
        "angel_one": {
            "tradingsymbol": symbol, "symboltoken": "<from the scrip master>",
            "exchange": order.get("exchange", "NFO"),
            "transactiontype": "BUY", "ordertype": "MARKET",
            "quantity": str(qty), "producttype": "CARRYFORWARD", "duration": "DAY",
        },
        "note": ("Field names differ between brokers, and symbol tokens must come from the broker's own "
                 "scrip master. Check the contract in your terminal before you send anything."),
    }


def dhan_payload(order, config, now):
    """The exact Dhan v2 request this ticket would send, security id included."""
    try:
        quote = contract_quote(order, config, now)
    except Exception as exc:                                       # noqa: BLE001
        return {"error": f"no real quote for this contract: {exc}"}
    if not quote.get("security_id"):
        return {"error": ("this contract has no Dhan security id: the quote came from a model, not from "
                          "Dhan. Set DHAN_ACCESS_TOKEN and DHAN_CLIENT_ID (Data API subscription) first.")}
    adapter = adapter_for(config) or brokers.dhan.Dhan(client_id="<client id>", access_token="<token>")
    return adapter.build_order(
        transaction="BUY", segment=brokers.dhan.Dhan.segment_for(order["underlying"]),
        security_id=quote["security_id"], quantity=int(order.get("qty") or 0),
        order_type=str(order.get("order_type") or "market"),
        product=str(config.get("live_product", "CNC")).upper(), validity="DAY",
        price=order.get("limit_price"),
        correlation=_correlation(store.fingerprint(order), "e"))


# ---------------------------------------------------------------------- status
def _current_premium(position, config, now):
    """Real premium when Dhan can give one, else the model at the current index level."""
    underlying = position.get("underlying")
    if not underlying:
        return None
    expiry = position.get("expiry")
    if isinstance(expiry, str):
        expiry = dt.date.fromisoformat(expiry[:10])
    try:
        chain = chains.load(underlying, expiry=expiry, config=config, now=now, source="auto")
        leg = chain.leg(float(position.get("strike")), str(position.get("right", "CE")))
        if leg and leg.get("ltp"):
            return float(leg["ltp"])
    except Exception:                                              # noqa: BLE001
        pass
    spot = workflow.spot_for(underlying, now)
    info = dict(position)
    info.update({"spot": spot, "iv": 0.15, "expiry": expiry,
                 "strike": float(position.get("strike") or round(spot / 50) * 50)})
    return workflow.premium_at_index(info, spot, config, now)


def main(argv=None):
    ap = cli.parser("Order Executor - ticket, paper fill, and real Dhan orders")
    ap.add_argument("command", nargs="?", default="show",
                    choices=["show", "place", "payload", "status", "flatten", "preflight", "book"])
    ap.add_argument("--yes", action="store_true", help="your explicit yes for this exact ticket")
    ap.add_argument("--symbol", help="flatten: only this symbol")
    args = ap.parse_args(argv)

    config, now = cli.context(args)

    if args.command == "show":
        items = tickets(config, now)
        if not items:
            cli.fail("no trader/orders.json; run /trader-budget and /trader-risk first")
        for item in items:
            render_ticket(item)
            print()
        mode = "LIVE (dhan)" if live_mode(config) else "paper"
        print(f"mode: {mode}")
        if any(item["ready"] for item in items):
            print(f"To check it end to end: ticket.py preflight")
            print(f"To fill it: ticket.py place --yes")
        return 0 if any(item["ready"] for item in items) else 1

    if args.command == "preflight":
        items = tickets(config, now)
        if not items:
            cli.fail("no trader/orders.json; run /trader-budget and /trader-risk first")
        failures = 0
        reports = []
        for item in items:
            report = preflight(item, config, now)
            reports.append(report)
            render_preflight(report)
            print()
            failures += bool(report["blockers"])
        cli.emit(args, reports, "")
        return 0 if not failures else 1

    if args.command == "payload":
        items = tickets(config, now)
        if not items:
            cli.fail("no orders to translate")
        cli.emit(args, [{"ticket": item["order"], "ready": item["ready"],
                         "payload": broker_payload(item["order"], config),
                         "dhan": dhan_payload(item["order"], config, now)} for item in items], "")
        if not args.json:
            for item in items:
                print(json.dumps({"order": item["order"], "ready": item["ready"],
                                  "broker": broker_payload(item["order"], config),
                                  "dhan_v2": dhan_payload(item["order"], config, now)}, indent=2))
        print("\nThis command sends nothing. `place --yes` is the only command that can.")
        return 0

    if args.command == "book":
        adapter = adapter_for(config)
        if adapter is None or not adapter.available():
            cli.fail("no Dhan credentials (DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN) and no live_broker")
        try:
            orders = adapter.orders()
            positions = adapter.positions()
        except Exception as exc:                                   # noqa: BLE001
            cli.fail(f"could not read the Dhan book: {exc}")
        cli.emit(args, {"orders": orders, "positions": positions}, "")
        if not args.json:
            print("TODAY'S DHAN ORDERS")
            for row in orders or []:
                print(f"  {row.get('orderId')}  {row.get('transactionType','?'):<4} "
                      f"{row.get('tradingSymbol','?'):<22} {row.get('filledQty',0)}/"
                      f"{row.get('quantity','?')}  {row.get('orderStatus','?')}")
            if not orders:
                print("  (none)")
            print("\nDHAN POSITIONS")
            for row in positions or []:
                print(f"  {row.get('tradingSymbol','?'):<22} net {row.get('netQty', 0)}  "
                      f"avg {row.get('costPrice') or row.get('buyAvg')}  "
                      f"LTP {row.get('lastTradedPrice')}")
            if not positions:
                print("  (none)")
        return 0

    if args.command == "place":
        items = tickets(config, now)
        if not items:
            cli.fail("no orders to place; run /trader-budget and /trader-risk first")
        blocked = [item for item in items if not item["ready"]]
        if blocked:
            print("Not placing: these tickets are not READY.")
            for item in blocked:
                render_ticket(item)
            return 1
        if not args.yes:
            print("Nothing placed. Read the ticket above and say yes explicitly:")
            for item in items:
                print(f"  python3 ticket.py place --yes    # {item['order']['symbol']}")
            return 1

        if live_mode(config):
            return _place_live_all(items, config, now)

        if not config.get("paper", True):
            print("Paper is OFF but live trading is not fully enabled "
                  "(live_broker='dhan' and live_trading_enabled=true are both required).")
            print("Here is the exact Dhan v2 request this ticket would send:")
            print(json.dumps(dhan_payload(items[0]["order"], config, now), indent=2))
            print("\nNothing was sent. Machine-placed orders also need a static IP whitelisted on your "
                  "Dhan account (SEBI's algo rule). Turn it on in config.json when that is in place.")
            return 1

        for item in items:
            position, buy = paper_fill(item, config, now)
            print(f"PAPER FILLED  {position['qty']} units of {position['symbol']} at "
                  f"Rs {position['entry_premium']:,.2f} (charges Rs {buy['total']:.2f})")
            print(f"          stop Rs {position['stop_premium']}, target Rs {position['target_premium']}, "
                  f"risk Rs {float(position['risk'] or 0):,.0f}")
        print("\nNothing went to a broker: paper mode. Next: /trader-alerts to watch the levels.")
        return 0

    if args.command == "status":
        state = _broker_state(config)
        positions = state.get("positions", [])
        if not positions:
            print("No open positions.")
            return 1
        adapter = adapter_for(config)
        total = 0.0
        for position in positions:
            try:
                premium = _current_premium(position, config, now)
            except Exception:                                      # noqa: BLE001
                premium = None
            tag = "LIVE" if position.get("live") else "paper"
            if premium is None:
                print(f"  {position['symbol']}: no current premium available")
                continue
            pnl = (premium - float(position["entry_premium"])) * int(position["qty"]) \
                - float(position.get("charges_entry") or 0)
            total += pnl
            risk = float(position.get("risk") or 0)
            r_multiple = pnl / risk if risk else 0
            print(f"  [{tag}] {position['symbol']:<22} entry Rs {float(position['entry_premium']):,.2f} -> "
                  f"Rs {premium:,.2f}  {position['qty']} units  P&L Rs {pnl:,.0f} ({r_multiple:+.2f}R)")
            if position.get("live"):
                _reconcile_live(position, adapter, config)
        print(f"\nOpen premium P&L: Rs {total:,.0f} (before exit charges)")
        return 0

    if args.command == "flatten":
        state = _broker_state(config)
        positions = state.get("positions", [])
        if args.symbol:
            positions = [p for p in positions if p["symbol"] == args.symbol]
        if not positions:
            print("Nothing to flatten.")
            return 1
        adapter = adapter_for(config)
        if not args.yes:
            for position in positions:
                try:
                    premium = _current_premium(position, config, now)
                except Exception:                                  # noqa: BLE001
                    premium = None
                tag = "LIVE via dhan" if position.get("live") else "paper"
                print(f"  would sell {position['qty']} x {position['symbol']} at about Rs {premium} ({tag})")
            print("Say yes explicitly: ticket.py flatten --yes")
            return 1

        closed, realised, live_failed = [], 0.0, 0
        for position in positions:
            if position.get("live"):
                if adapter is None or not adapter.available():
                    cli.fail("this position is live but Dhan credentials are missing")
                result = flatten_live(position, adapter, config, now)
                if result is None:
                    live_failed += 1
                    continue
                realised += result["pnl"]
                closed.append(position)
                continue
            premium = _current_premium(position, config, now) or position["entry_premium"]
            exchange = "NSE"
            sell = costs.leg(premium, position["qty"], exchange, "sell", config.get("charges"), now)
            pnl = (premium - position["entry_premium"]) * position["qty"] \
                - position["charges_entry"] - sell["total"]
            realised += pnl
            closed.append(position)
            store.append_jsonl("journal.jsonl", {
                "event": "exit",
                "at": clock.now_ist(now).isoformat(timespec="seconds"),
                "symbol": position["symbol"],
                "qty": position["qty"],
                "entry_premium": position["entry_premium"],
                "exit_premium": premium,
                "pnl": round(pnl, 2),
                "charges": round(sell["total"], 2),
                "r_multiple": round(pnl / float(position["risk"]), 3) if position.get("risk") else None,
                "reason": "flatten",
                "paper": bool(config.get("paper", True)),
            }, config)
            print(f"PAPER CLOSED {position['qty']} x {position['symbol']} at Rs {premium:,.2f}: "
                  f"P&L Rs {pnl:,.0f}")
        state["positions"] = [p for p in state.get("positions", []) if p not in closed]
        _save_broker_state(state, config)
        print(f"\nRealised: Rs {realised:,.0f}")
        if live_failed:
            print(f"{live_failed} live position(s) could NOT be closed: sell them in the Dhan app now.")
            return 1
        return 0
    return 0


def _place_live_all(items, config, now):
    adapter = adapter_for(config)
    if adapter is None:
        cli.fail("live_broker is set but the adapter could not be created")
    failures = 0
    for item in items:
        report = preflight(item, config, now)
        render_preflight(report)
        if report["blockers"]:
            failures += 1
            continue
        # Re-check the quote at the moment of sending, not when preflight was printed.
        position, code = place_live(item, adapter, report, config, now)
        failures += code
        if position:
            print(f"  position open: {position['qty']} x {position['symbol']}, "
                  f"stop {position['order_ids'].get('stop') or 'MISSING'}, "
                  f"target {position['order_ids'].get('target') or 'MISSING'}")
        print()
    if failures:
        print("Some tickets were not placed. Read the BLOCK/FAIL lines above; nothing is retried automatically.")
        return 1
    print("Done. Watch it with ticket.py status; close it with ticket.py flatten --yes.")
    return 0


def _reconcile_live(position, adapter, config):
    """Report the broker's view of a live position, and cancel orphaned exits."""
    ids = position.get("order_ids") or {}
    if adapter is None or not adapter.available():
        print("        (no Dhan credentials: showing the plan's own numbers)")
        return
    states = {}
    for leg in ("entry", "stop", "target"):
        order_id = ids.get(leg)
        if not order_id:
            continue
        try:
            status = adapter.order_status(order_id) or {}
        except Exception as exc:                                   # noqa: BLE001
            print(f"        {leg}: could not read order {order_id} ({exc})")
            continue
        state = str(status.get("orderStatus") or "?").upper()
        states[leg] = state
        print(f"        {leg:<6} order {order_id}: {state}"
              + (f" at Rs {status.get('averageTradedPrice')}" if state in LIVE_STATUSES else ""))
    filled = [leg for leg in ("stop", "target") if states.get(leg) in LIVE_STATUSES]
    working = [leg for leg in ("stop", "target") if states.get(leg) in ("PENDING", "TRIGGERED", "TRANSIT")]
    if filled and working:
        for leg in working:
            try:
                adapter.cancel(ids[leg])
                print(f"        {leg} order {ids[leg]} cancelled: {filled[0]} already filled (no OCO on Dhan)")
            except Exception as exc:                               # noqa: BLE001
                print(f"        !!! could not cancel the orphaned {leg} order {ids[leg]}: {exc}")
        print("        the position is closed at the broker; run ticket.py flatten --yes to book it")


if __name__ == "__main__":
    sys.exit(cli.run(main))
