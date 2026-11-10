#!/usr/bin/env python3
"""Price Alerts - the stop, the target, the flat-by time, and anything else.

    python3 alerts.py add --from-plan          # stop, target and flat-by in one go
    python3 alerts.py add --underlying NIFTY --above 25150
    python3 alerts.py add --underlying NIFTY --below 24850 --note "range low"
    python3 alerts.py add --premium NIFTY26N1724850PE --below 178
    python3 alerts.py add --time 15:15 --note "flat by"
    python3 alerts.py check                    # rings what hit, once
    python3 alerts.py list
    python3 alerts.py clear

Designed to be run on a loop: `python3 alerts.py check` every minute, or a
cron/Task Scheduler job. It only reads prices; it never places an order, and it
will not fire an order no matter how the rule is written.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib")))

from indkit import cli, clock, feed, instruments, store, workflow  # noqa: E402

STATE = "state/alerts.json"


def load(config):
    return store.read_json(STATE, config, default={"alerts": [], "fired": []}) or {"alerts": [], "fired": []}


def save(config, payload):
    store.write_json(STATE, payload, config)


def add_alert(config, alert, now):
    payload = load(config)
    alert["id"] = alert.get("id") or f"a{len(payload['alerts']) + 1}-{clock.now_ist(now).strftime('%H%M%S')}"
    alert["created_at"] = clock.now_ist(now).isoformat(timespec="seconds")
    alert.setdefault("fired", False)
    payload["alerts"].append(alert)
    save(config, payload)
    return alert


def from_plan(config, now):
    plan = store.read_json("plan.json", config, default=None)
    if not plan:
        cli.fail("no trader/plan.json; run /trader-exit first")
    added = []
    underlying = plan["underlying"]
    direction = plan["right"]
    stop, target = plan.get("stop_premium"), plan.get("target_premium")
    if stop:
        added.append(add_alert(config, {"kind": "premium", "symbol": plan["symbol"],
                                        "underlying": underlying, "below": stop,
                                        "note": f"STOP: {plan.get('stop_rule')} at index "
                                                f"{plan.get('stop_index') or '-'}"}, now))
    if target:
        added.append(add_alert(config, {"kind": "premium", "symbol": plan["symbol"],
                                        "underlying": underlying, "above": target,
                                        "note": "TARGET"}, now))
    if plan.get("flat_by"):
        added.append(add_alert(config, {"kind": "time", "at": plan["flat_by"],
                                        "note": "flat by: close whatever is open"}, now))
    if plan.get("expiry"):
        added.append(add_alert(config, {"kind": "date", "on": plan["expiry"],
                                        "note": "expiry day: no new entries, be flat early"}, now))
    return added


def evaluate(alert, config, now):
    """Has it hit? Returns (hit, message, value)."""
    moment = clock.now_ist(now)
    kind = alert.get("kind")
    if kind == "time":
        target = _hhmm(alert["at"])
        if target and moment.time() >= target:
            return True, f"the clock reached {target.strftime('%H:%M')} IST", None
        return False, "", None
    if kind == "date":
        if moment.date().isoformat() == alert.get("on"):
            return True, f"today is {alert['on']}", None
        return False, "", None
    if kind == "index":
        underlying = alert.get("underlying", "NIFTY")
        level = feed.quote(instruments.UNDERLYINGS[underlying]["spot"])["price"]
        if alert.get("above") is not None and level >= float(alert["above"]):
            return True, f"{underlying} {level:,.2f} at or above {float(alert['above']):,.2f}", level
        if alert.get("below") is not None and level <= float(alert["below"]):
            return True, f"{underlying} {level:,.2f} at or below {float(alert['below']):,.2f}", level
        return False, "", level
    if kind == "premium":
        premium = _premium_now(alert, config, now)
        if premium is None:
            return False, "no premium available", None
        if alert.get("above") is not None and premium >= float(alert["above"]):
            return True, f"{alert['symbol']} Rs {premium:,.2f} at or above {float(alert['above']):,.2f}", premium
        if alert.get("below") is not None and premium <= float(alert["below"]):
            return True, f"{alert['symbol']} Rs {premium:,.2f} at or below {float(alert['below']):,.2f}", premium
        return False, "", premium
    return False, f"unknown alert kind {kind}", None


def _premium_now(alert, config, now):
    """Best available premium for a symbol: a real chain if we can, else the model."""
    try:
        master = instruments.Master.load(config, now=now)
        contract = master.by_symbol(alert["symbol"])
        if contract is None:
            return None
        spot = workflow.spot_for(contract.underlying, now)
        info = {
            "underlying": contract.underlying, "strike": contract.strike, "right": contract.right,
            "expiry": contract.expiry.isoformat(), "iv": 0.15, "spot": spot,
            "premium_source": "model",
        }
        return workflow.premium_at_index(info, spot, config, now)
    except Exception:                                              # noqa: BLE001
        return None


def _hhmm(text):
    try:
        return dt.datetime.strptime(str(text), "%H:%M").time()
    except (TypeError, ValueError):
        return None


def main(argv=None):
    ap = cli.parser("Price Alerts - stop, target, flat-by")
    ap.add_argument("command", nargs="?", default="check", choices=["add", "check", "list", "clear"])
    ap.add_argument("--from-plan", action="store_true", help="add the stop, target and flat-by from plan.json")
    ap.add_argument("--underlying", help="index alert: NIFTY, BANKNIFTY, SENSEX")
    ap.add_argument("--premium", help="premium alert: the exact trading symbol")
    ap.add_argument("--above", type=float)
    ap.add_argument("--below", type=float)
    ap.add_argument("--time", help="time alert, HH:MM IST")
    ap.add_argument("--note")
    ap.add_argument("--all", action="store_true", help="check: also re-show alerts that already fired")
    args = ap.parse_args(argv)

    config, now = cli.context(args)

    if args.command == "add":
        if args.from_plan:
            added = from_plan(config, now)
            for alert in added:
                print(f"added {alert['kind']:<8} {alert.get('symbol') or alert.get('underlying') or alert.get('at') or alert.get('on')} "
                      f"{_describe(alert)}  {alert.get('note','')}")
            return 0
        if args.time:
            alert = add_alert(config, {"kind": "time", "at": args.time, "note": args.note or "time alert"}, now)
        elif args.premium:
            alert = add_alert(config, {"kind": "premium", "symbol": args.premium,
                                       "above": args.above, "below": args.below,
                                       "note": args.note or ""}, now)
        elif args.underlying:
            alert = add_alert(config, {"kind": "index", "underlying": args.underlying.upper(),
                                       "above": args.above, "below": args.below,
                                       "note": args.note or ""}, now)
        else:
            cli.fail("give --from-plan, --time, --premium or --underlying")
        print(f"added {alert['kind']} alert: {_describe(alert)}")
        return 0

    if args.command == "list":
        payload = load(config)
        if not payload["alerts"]:
            print("No alerts. Add some: alerts.py add --from-plan")
            return 1
        for alert in payload["alerts"]:
            stamp = "FIRED" if alert.get("fired") else " armed"
            print(f"{stamp:<6} {alert['id']:<12} {alert['kind']:<8} {_describe(alert):<42} {alert.get('note','')}")
        return 0

    if args.command == "clear":
        save(config, {"alerts": [], "fired": []})
        print("Alerts cleared.")
        return 0

    payload = load(config)
    if not payload["alerts"]:
        print("No alerts configured. Add some: alerts.py add --from-plan")
        return 1
    rings, checked = [], 0
    for alert in payload["alerts"]:
        if alert.get("fired") and not args.all:
            continue
        try:
            hit, message, value = evaluate(alert, config, now)
        except Exception as exc:                                    # noqa: BLE001
            hit, message, value = False, f"could not check ({exc})", None
        checked += 1
        if hit:
            rings.append((alert, message))
            if not alert.get("fired"):
                alert["fired"] = True
                alert["fired_at"] = clock.now_ist(now).isoformat(timespec="seconds")
    if checked:
        save(config, payload)

    if args.json:
        cli.emit(args, {"at": clock.now_ist(now).isoformat(timespec="seconds"),
                        "rang": [{"id": a["id"], "message": m, "note": a.get("note")} for a, m in rings],
                        "checked": checked}, "")
    else:
        print(f"Alert check at {clock.now_ist(now).strftime('%H:%M:%S')} IST ({checked} alert(s) armed)")
        if not rings:
            print("  nothing hit.")
        for alert, message in rings:
            print(f"  RING  {message}  {('[' + alert['note'] + ']') if alert.get('note') else ''}")
        if rings:
            print("\nAn alert is information: check the price in your terminal, then act yourself.")
    return 0 if rings else 1


def _describe(alert):
    if alert["kind"] == "index":
        bits = []
        if alert.get("above") is not None:
            bits.append(f">= {float(alert['above']):,.2f}")
        if alert.get("below") is not None:
            bits.append(f"<= {float(alert['below']):,.2f}")
        return f"{alert.get('underlying')} {' or '.join(bits)}"
    if alert["kind"] == "premium":
        bits = []
        if alert.get("above") is not None:
            bits.append(f">= Rs {float(alert['above']):,.2f}")
        if alert.get("below") is not None:
            bits.append(f"<= Rs {float(alert['below']):,.2f}")
        return f"{alert['symbol']} {' or '.join(bits)}"
    if alert["kind"] == "time":
        return f"at {alert['at']} IST"
    if alert["kind"] == "date":
        return f"on {alert['on']}"
    return str(alert)


if __name__ == "__main__":
    sys.exit(cli.run(main))
