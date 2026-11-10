#!/usr/bin/env python3
"""Risk Manager - the gate between a proposal and the broker.

    python3 check.py                  # check trader/orders.json, write state/risk.json
    python3 check.py --json
    python3 check.py --no-write
    python3 check.py --now "2026-11-09 10:45"

Every rule comes from config.json; nothing here decides what is a good trade.
A PASS means "this order is allowed to be shown to you", never "buy it".
A BLOCK is final for that order: it does not reach the ticket.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib")))

from indkit import cli, clock, costs, instruments, rules, store, workflow  # noqa: E402


def load_orders(config):
    payload = store.read_json("orders.json", config, default=None)
    if not payload:
        return []
    orders = payload.get("orders") if isinstance(payload, dict) else payload
    if isinstance(orders, dict):
        orders = [orders]
    return orders or []


def todays_placed(config, now):
    state = store.read_json("state/broker.json", config, default={}) or {}
    today = clock.now_ist(now).date().isoformat()
    placed = [o for o in state.get("placed", []) if str(o.get("at", "")).startswith(today)]
    return placed


def check_order(order, config, master, now, placed, batch_seen, phase):
    """Every rule for one order. Returns (verdict, reasons, warnings, facts)."""
    reasons, warnings = [], []
    moment = clock.now_ist(now)
    side = str(order.get("side", "")).lower()
    kind = str(order.get("kind", "entry")).lower()
    is_exit = kind == "exit" or side == "sell"
    # a 'sell' that closes a long is an exit; a 'sell' that opens a short option is writing
    writing = side == "sell" and kind != "exit"

    symbol = order.get("symbol")
    qty = order.get("qty")
    lot = int(order.get("lot_size") or 0)
    facts = {"symbol": symbol, "side": side, "qty": qty, "kind": kind}

    # ---- format
    if not symbol:
        reasons.append("format: no symbol")
    if side not in ("buy", "sell"):
        reasons.append(f"format: side '{order.get('side')}' is not buy or sell")
    if not isinstance(qty, (int, float)) or qty <= 0:
        reasons.append("format: quantity must be a positive number of units")
    elif lot and int(qty) % lot:
        reasons.append(f"lot: {int(qty)} units is not a multiple of the {lot}-unit lot "
                       "(index options trade in whole lots only)")
    if not lot and order.get("underlying"):
        try:
            lot = master.lot(order["underlying"])
        except Exception:                                          # noqa: BLE001
            pass

    contract = None
    underlying = (order.get("underlying") or "").upper()
    if symbol and not underlying:
        found = master.by_symbol(str(symbol))
        if found:
            contract = found
            underlying = found.underlying
    elif symbol and underlying:
        try:
            expiry = dt.date.fromisoformat(str(order.get("expiry"))[:10])
            contract = master.get(underlying, expiry, float(order.get("strike")), str(order.get("right", "CE")))
        except Exception:                                          # noqa: BLE001
            contract = None
        if contract is None:
            contract = master.by_symbol(str(symbol))

    if symbol and contract is None and not is_exit:
        warnings.append(f"the contract {symbol} is not in today's master: it may be a stale or "
                        "mistyped symbol, or an expired series")

    entry_premium = order.get("premium_entry") or order.get("limit_price") or order.get("premium")
    stop_premium = order.get("premium_stop")
    target_premium = order.get("premium_target")
    units = int(qty) if isinstance(qty, (int, float)) else 0
    try:
        entry_premium = float(entry_premium) if entry_premium is not None else None
        stop_premium = float(stop_premium) if stop_premium is not None else None
        target_premium = float(target_premium) if target_premium is not None else None
    except (TypeError, ValueError):
        entry_premium = stop_premium = target_premium = None

    # ---- what is allowed at all
    if not config.get("allow_options", True):
        reasons.append("asset: options are switched off in config (allow_options false)")
    if underlying and underlying in (config.get("blocked_underlyings") or []):
        reasons.append(f"blocked: {underlying} is on your blocked list")
    if writing and not config.get("allow_short_premium", False):
        reasons.append("short premium: selling an option to open a position is switched off "
                       "(allow_short_premium false). Buying a put is not a short - that is allowed.")
    if order.get("order_type") in ("sl", "sl-m") and not order.get("trigger_price"):
        reasons.append("format: an SL order needs a trigger price")

    # exits: getting out is never blocked by a day limit
    if is_exit:
        if entry_premium is not None and entry_premium <= 0:
            reasons.append("format: a sell needs a positive premium")
        verdict = "BLOCK" if reasons else ("WARN" if warnings else "PASS")
        facts.update({"exit": True})
        return verdict, reasons, warnings, facts

    # ---- the plan has to exist and make sense
    if config.get("require_stop", True) and stop_premium is None:
        reasons.append("stop: no stop on this order and require_stop is on")
    if stop_premium is not None and entry_premium is not None and stop_premium >= entry_premium:
        reasons.append(f"stop: the stop premium Rs {stop_premium:.2f} is not below the entry "
                       f"Rs {entry_premium:.2f} for a bought option")
    if target_premium is not None and entry_premium is not None and target_premium <= entry_premium:
        reasons.append("target: the target premium is not above the entry")

    # ---- the day
    if not phase["entries_ok"]:
        reasons.append(f"window: {phase['reason']}")
    if phase.get("vix") and phase["vix"].get("value") and phase["vix"]["value"] > float(config.get("max_vix", 28)):
        reasons.append(f"regime: India VIX {phase['vix']['value']:.2f} is above your {config.get('max_vix')} ceiling")
    if phase.get("holiday"):
        reasons.append(f"calendar: {phase['holiday']}")

    # ---- expiry distance
    days = None
    if contract is not None:
        days = (contract.expiry - moment.date()).days
        facts["expiry"] = contract.expiry.isoformat()
        facts["days_to_expiry"] = days
        if days < int(config.get("min_days_to_expiry", 1)):
            if not config.get("allow_expiry_day_entries", False):
                reasons.append(f"expiry: this contract expires in {days} day(s) and expiry-day entries are off")
            skip_after = _hhmm(config.get("skip_expiry_day_after", "14:00"))
            if days == 0 and moment.time() >= skip_after:
                reasons.append(f"expiry: on expiry day no new entries after {skip_after.strftime('%H:%M')}")

    # ---- size, cash and premium
    if entry_premium is None:
        reasons.append("premium: no entry premium on the order, so risk cannot be measured")
        maths = None
    else:
        maths = workflow.risk_math(entry_premium, stop_premium or 0.0, target_premium or entry_premium,
                                   units / lot if lot else 1, lot or 1,
                                   order.get("exchange") or "NSE", moment, config)
        facts.update({"premium": entry_premium, "outlay": maths["premium_outlay"],
                      "risk": maths["net_risk"], "reward": maths["net_reward"],
                      "reward_risk": maths["reward_risk"]})
        if entry_premium < float(config.get("min_premium", 20)):
            reasons.append(f"premium: Rs {entry_premium:.2f} is under your Rs {config.get('min_premium', 20)} floor "
                           "(illiquid strikes cost more in spread than they can pay)")
        if maths["premium_outlay"] > float(config.get("capital", 0)):
            reasons.append(f"cash: the premium outlay Rs {maths['premium_outlay']:,.0f} is more than your "
                           f"capital Rs {float(config.get('capital', 0)):,.0f}; option premium is paid upfront in full")
        cap = float(config.get("capital", 0)) * float(config.get("max_premium_pct_of_capital", 1.0))
        if maths["premium_outlay"] > cap:
            reasons.append(f"size: the premium outlay Rs {maths['premium_outlay']:,.0f} is over your "
                           f"{100*float(config.get('max_premium_pct_of_capital', 1.0)):.0f}% cap Rs {cap:,.0f}")
        if stop_premium is not None:
            if maths["net_risk"] > float(config.get("max_loss_per_trade", 0)):
                reasons.append(f"loss: Rs {maths['net_risk']:,.0f} at the stop (charges included) is over your "
                               f"Rs {float(config.get('max_loss_per_trade', 0)):,.0f} max loss per trade - "
                               "fewer lots, a cheaper strike, or a closer stop")
            if maths["reward_risk"] is not None and maths["reward_risk"] < float(config.get("min_reward_risk", 1.2)):
                reasons.append(f"reward:risk: {maths['reward_risk']:.2f} after charges is under your "
                               f"{float(config.get('min_reward_risk', 1.2)):.2f} minimum")
        if lot and units / lot > int(config.get("max_lots", 4)):
            reasons.append(f"size: {int(units/lot)} lots is over max_lots {config.get('max_lots')}")

    # ---- the day's budget
    day_risk = sum(float(o.get("risk") or 0) for o in placed if o.get("kind") == "entry")
    if maths:
        if day_risk + maths["net_risk"] > float(config.get("max_daily_loss", 0)):
            reasons.append(f"daily loss: Rs {day_risk:,.0f} already risked today + Rs {maths['net_risk']:,.0f} "
                           f"is over your Rs {float(config.get('max_daily_loss', 0)):,.0f} daily limit")
        facts["day_risk_before"] = round(day_risk, 2)
    entries_today = [o for o in placed if o.get("kind") == "entry"]
    if len(entries_today) >= int(config.get("max_trades_per_day", 2)):
        reasons.append(f"trades: {len(entries_today)} entries already placed today "
                       f"(max {config.get('max_trades_per_day')})")
    facts["trades_today"] = len(entries_today)

    # ---- duplicates
    key = (str(symbol), side)
    if key in batch_seen:
        reasons.append("duplicate: the same symbol and side appears twice in this batch")
    if any((str(o.get("symbol")), str(o.get("side")).lower()) == key for o in entries_today):
        reasons.append("duplicate: this symbol and side was already placed today")
    batch_seen.add(key)

    # ---- SEBI position limits (informational at retail size, so a warning)
    if contract is not None and entry_premium is not None:
        delta = order.get("delta")
        if delta is None and order.get("target_delta") is None:
            delta = 0.5
        usage = rules.position_limit_usage(units, float(order.get("spot") or 0) or 1.0, float(delta))
        if float(order.get("spot") or 0):
            facts["delta_notional"] = usage["delta_notional"]
            if usage["used_pct"] > 5:
                warnings.append(f"position: delta-adjusted exposure is {usage['used_pct']:.2f}% of the "
                                f"Rs {rules.INTRADAY_NET_INDEX_LIMIT_CR:,} crore intraday index limit")

    if days == 0:
        warnings.append("expiry day: the premium can halve in an hour and the exchange squares off open "
                        "positions at the close; be flat well before 15:30")
    verdict = "BLOCK" if reasons else ("WARN" if warnings else "PASS")
    return verdict, reasons, warnings, facts


def _hhmm(text):
    try:
        return dt.datetime.strptime(str(text), "%H:%M").time()
    except (TypeError, ValueError):
        return dt.time(14, 0)


def main(argv=None):
    ap = cli.parser("Risk Manager - PASS or BLOCK for every proposed order")
    ap.add_argument("orders_file", nargs="?", help="path to an orders.json (default trader/orders.json)")
    args = ap.parse_args(argv)

    config, now = cli.context(args)
    if args.orders_file:
        config["_orders_override"] = args.orders_file
    phase = workflow.day_context(now, config)
    orders = load_orders(config)
    if not orders:
        cli.fail("no trader/orders.json; run /trader-budget first")

    master = instruments.Master.load(config, now=now)
    placed = todays_placed(config, now)
    batch_seen = set()

    verdicts, passed, blocked = [], 0, 0
    for index, order in enumerate(orders, start=1):
        verdict, reasons, warnings, facts = check_order(order, config, master, now, placed, batch_seen, phase)
        if verdict == "BLOCK":
            blocked += 1
        else:
            passed += 1
        verdicts.append({
            "index": index,
            "fingerprint": store.fingerprint(order),
            "verdict": verdict,
            "reasons": reasons,
            "warnings": warnings,
            "facts": facts,
            "order": order,
        })

    record = store.stamp({
        "day": clock.now_ist(now).date().isoformat(),
        "session": {"state": phase["state"], "entries_ok": phase["entries_ok"], "reason": phase["reason"]},
        "vix": phase.get("vix"),
        "verdicts": verdicts,
        "passed": passed,
        "blocked": blocked,
        "day_risk": round(sum(float(o.get("risk") or 0) for o in placed if o.get("kind") == "entry"), 2),
    }, now)
    if not args.no_write:
        store.write_json("state/risk.json", record, config)

    if args.json:
        cli.emit(args, record, "")
        return 0 if blocked == 0 else 1

    print(f"Risk check - {clock.describe_session(now)}")
    if phase.get("vix"):
        vix = phase["vix"]
        print(f"India VIX {vix['value']:.2f} ({vix['regime']})")
    print()
    for item in verdicts:
        facts = item["facts"]
        order = item["order"]
        head = (f"{item['verdict']:<5} #{item['index']} {order.get('side','?')} "
                f"{order.get('qty','?')} x {order.get('symbol','?')}")
        print(head)
        if facts.get("premium") is not None:
            print(f"        premium Rs {facts['premium']:,.2f} | outlay Rs {facts.get('outlay', 0):,.0f} | "
                  f"risk at stop Rs {facts.get('risk', 0):,.0f} | reward Rs {facts.get('reward', 0):,.0f} | "
                  f"R:R {facts.get('reward_risk')}")
            print(f"        today before this: {facts.get('trades_today', 0)} of "
                  f"{config.get('max_trades_per_day')} trades, Rs {facts.get('day_risk_before', 0):,.0f} of "
                  f"Rs {float(config.get('max_daily_loss', 0)):,.0f} daily risk")
        for reason in item["reasons"]:
            print(f"      - BLOCK: {reason}")
        for warning in item["warnings"]:
            print(f"      - note: {warning}")
    print()
    print(f"{passed} passed, {blocked} blocked at {clock.now_ist(now).strftime('%Y-%m-%d %H:%M')} IST.")
    if config.get("paper", True):
        print("(paper mode: any fill from here is simulated)")
    if blocked == 0:
        print("A PASS is a rules check, not a recommendation. Next: /trader-broker.")
    else:
        print("Blocked orders do not go to the broker. Fix the reason or skip the trade.")
    return 0 if blocked == 0 else 1


if __name__ == "__main__":
    sys.exit(cli.run(main))
