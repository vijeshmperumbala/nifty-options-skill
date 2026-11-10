#!/usr/bin/env python3
"""Exit Planner - the stop and the target, decided before the trade exists.

    python3 exit.py plan                      # from trader/entry.json, midpoint stop, 1.5R
    python3 exit.py plan --stop or-low --target 2R --write
    python3 exit.py plan --stop premium-pct --premium-pct 25
    python3 exit.py plan --stop index-points --points 45
    python3 exit.py plan --stop atr --atr-multiple 1.2
    python3 exit.py show

An index level is not a tradable price. The stop is set on the INDEX (that is
where the setup fails), then translated into a PREMIUM with the option's own
greeks, because the premium is what you actually lose. Both are written down:
trader/plan.json is what the Time Machine, the sizing and the alerts read.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib")))

from indkit import cli, clock, costs, feed, indicators, instruments, store, workflow  # noqa: E402


def load_contract(args, config, now):
    """The contract being planned: from trader/entry.json, or from flags."""
    entry = store.read_json("entry.json", config, default=None)
    if entry and entry.get("contract") and not args.underlying:
        info = dict(entry["contract"])
        info["expiry"] = _as_date(info.get("expiry"))
        info["signal_price"] = _last_price(entry)
        info["range"] = entry.get("range")
        info["setup"] = entry.get("setup")
        return info

    underlying = (args.underlying or config.get("underlying") or "NIFTY").upper()
    spot = args.spot or workflow.spot_for(underlying, now)
    direction = (args.right or "CE").upper()
    info = workflow.pick_contract(underlying, spot, config=config, now=now, direction=direction,
                                  strike_mode="atm", chain_source=args.chain,
                                  chain_path=args.chain_file)
    if args.premium:
        info["premium"] = float(args.premium)
        info["premium_source"] = "manual"
    info["signal_price"] = args.entry or spot
    info["range"] = None
    info["setup"] = args.setup or "orb"
    return info


def _last_price(entry):
    contract = entry.get("contract") or {}
    if entry.get("price"):
        return float(entry["price"])
    if contract.get("spot"):
        return float(contract["spot"])
    return None


def _as_date(value):
    if isinstance(value, dt.date):
        return value
    if value:
        return dt.date.fromisoformat(str(value)[:10])
    return None


def build_plan(info, args, config, now):
    """Turn a rule into two premiums and a verdict, refusing the impossible ones."""
    moment = clock.now_ist(now)
    right = info["right"]
    entry_index = float(info.get("signal_price") or info["spot"])
    entry_premium = float(info["premium"])
    rule = (args.stop or config.get("stop_rule", "or-mid")).lower()
    notes, problems = [], []

    atr_value = None
    if rule == "atr":
        try:
            atr_value = indicators.atr(feed.daily_bars(info.get("spot_symbol") or _symbol(info), 40), 14)
        except Exception:                                          # noqa: BLE001
            atr_value = None
        if not atr_value:
            problems.append("no daily bars for an ATR stop")

    or_high = (info.get("range") or {}).get("high")
    or_low = (info.get("range") or {}).get("low")
    sign = 1.0 if right == "CE" else -1.0

    if rule in ("or-mid", "or-low") and not (or_high and or_low):
        problems.append(f"a {rule} stop needs the opening range; none is in entry.json "
                        "(use --stop premium-pct, index-points or atr)")
    stop_index = None
    stop_premium = None
    if rule == "or-mid" and or_high and or_low:
        stop_index = (or_high + or_low) / 2.0
    elif rule == "or-low" and or_high and or_low:
        stop_index = or_low if right == "CE" else or_high
    elif rule == "index-points":
        points = float(args.points or 0) or 40.0
        stop_index = entry_index - sign * points
    elif rule == "atr" and atr_value:
        stop_index = entry_index - sign * float(args.atr_multiple or 1.0) * atr_value
    elif rule == "premium-pct":
        pct = float(args.premium_pct or config.get("stop_premium_pct", 30))
        stop_premium = round(entry_premium * (1 - pct / 100.0), 2)
        notes.append(f"premium stop at -{pct:.0f}% of the entry premium (Rs {stop_premium:.2f})")

    if stop_index is not None:
        stop_index = round(float(stop_index), 2)
        stop_premium = workflow.premium_at_index(info, stop_index, config, moment)
        if stop_premium <= 0.05:
            problems.append(f"the {rule} stop at index {stop_index:,.2f} values the option at almost zero: "
                            "that is a 100% loss, not a stop. Use a premium stop (-stop premium-pct).")
    if stop_premium is None or stop_premium <= 0:
        problems.append("could not work out a premium for the stop")

    risk_per_unit = round(entry_premium - (stop_premium or 0), 2)

    # ---- the target, in R of the premium
    target_spec = str(args.target or f"{config.get('target_r', 1.5)}R")
    target_index = None
    if target_spec.lower().endswith("r"):
        target_r = float(target_spec[:-1])
        target_premium = round(entry_premium + target_r * risk_per_unit, 2)
    elif target_spec.lower().startswith("premium:"):
        target_premium = float(target_spec.split(":", 1)[1])
        target_r = None
    elif target_spec.lower().startswith("index:"):
        target_index = float(target_spec.split(":", 1)[1])
        target_premium = workflow.premium_at_index(info, target_index, config, moment)
        target_r = None
    else:
        target_r = float(target_spec)
        target_premium = round(entry_premium + target_r * risk_per_unit, 2)
    if target_index is None and target_premium and target_premium > entry_premium:
        target_index = workflow.index_for_premium(info, target_premium, config, moment)

    if target_premium is not None and target_premium <= entry_premium:
        problems.append(f"the target premium Rs {target_premium:.2f} is not above the entry Rs {entry_premium:.2f}")
    if stop_premium is not None and stop_premium >= entry_premium:
        problems.append(f"the stop premium Rs {stop_premium:.2f} is at or above the entry Rs {entry_premium:.2f}")

    # ---- direction sanity on the index
    if stop_index is not None and target_index is not None:
        if right == "CE" and not (stop_index < entry_index < target_index):
            problems.append(f"for a call the index has to go stop {stop_index:,.0f} < entry "
                            f"{entry_index:,.0f} < target {target_index:,.0f}")
        if right == "PE" and not (stop_index > entry_index > target_index):
            problems.append(f"for a put the index has to go stop {stop_index:,.0f} > entry "
                            f"{entry_index:,.0f} > target {target_index:,.0f}")

    lots = int(args.lots or 1)
    maths = workflow.risk_math(entry_premium, stop_premium or 0, target_premium or entry_premium,
                               lots, info["lot"], info["exchange"], moment, config) \
        if stop_premium and target_premium else None

    if maths:
        if risk_per_unit <= maths["breakeven_move"]:
            problems.append(
                f"the stop is {risk_per_unit:.2f} a unit away and the round trip costs "
                f"{maths['breakeven_move']:.2f} a unit: charges are a large share of this trade")
        min_rr = float(config.get("min_reward_risk", 1.2))
        if maths["reward_risk"] is not None and maths["reward_risk"] < min_rr:
            problems.append(f"reward:risk after charges is {maths['reward_risk']:.2f}, "
                            f"under your {min_rr:.2f} minimum")

    flat_by = _hhmm(args.flat_by or config.get("flat_by", "15:15"))
    expiry = _as_date(info["expiry"])
    days = (expiry - moment.date()).days if expiry else None
    if days == 0:
        notes.append(
            "this contract expires TODAY: theta and gamma dominate, the premium can halve in an hour, "
            f"and it must be closed before 15:30 (flat-by {flat_by.strftime('%H:%M')}).")

    plan = store.stamp({
        "underlying": info["underlying"],
        "name": info.get("name"),
        "exchange": info.get("exchange"),
        "order_exchange": info.get("order_exchange"),
        "symbol": info.get("symbol"),
        "strike": info.get("strike"),
        "right": right,
        "expiry": expiry.isoformat() if expiry else None,
        "days_to_expiry": days,
        "series": info.get("series"),
        "lot": info["lot"],
        "direction": "long " + ("call" if right == "CE" else "put"),
        "entry_index": round(entry_index, 2),
        "entry_premium": round(entry_premium, 2),
        "premium_source": info.get("premium_source"),
        "iv": info.get("iv"),
        "delta": info.get("delta"),
        "stop_rule": rule,
        "stop_index": stop_index,
        "stop_premium": stop_premium,
        "target_r": target_r,
        "target_index": target_index,
        "target_premium": target_premium,
        "risk_per_unit": risk_per_unit,
        "reward_per_unit": round((target_premium or 0) - entry_premium, 2),
        "flat_by": flat_by.strftime("%H:%M"),
        "opening_range": info.get("range"),
        "maths": maths,
        "notes": notes,
        "problems": problems,
        "ok": not problems,
    }, moment)
    return plan


def _symbol(info):
    return instruments.UNDERLYINGS.get(info.get("underlying", "NIFTY"), {}).get("spot")


def _hhmm(text):
    try:
        return dt.datetime.strptime(str(text), "%H:%M").time()
    except (ValueError, TypeError):
        return dt.time(15, 15)


def render(plan):
    maths = plan.get("maths") or {}
    lines = []
    source = plan.get("premium_source")
    flag = " (MODELLED - not a quote)" if source == "model" else "" if source else ""
    lines.append(f"Plan - {plan['symbol']}  ({plan['direction']}, {plan.get('series') or ''} expiry "
                 f"{plan.get('expiry')}, lot {plan['lot']})")
    lines.append(f"  entry   index {plan['entry_index']:,.2f} -> premium Rs {plan['entry_premium']:,.2f}{flag}")
    if plan.get("stop_index") is not None:
        lines.append(f"  stop    index {plan['stop_index']:,.2f} ({plan['stop_rule']}) -> premium "
                     f"Rs {plan['stop_premium']:,.2f}   risk Rs {plan['risk_per_unit']:,.2f} a unit")
    else:
        lines.append(f"  stop    premium Rs {plan['stop_premium']:,.2f} ({plan['stop_rule']})   "
                     f"risk Rs {plan['risk_per_unit']:,.2f} a unit")
    if plan.get("target_index"):
        lines.append(f"  target  premium Rs {plan['target_premium']:,.2f} -> index {plan['target_index']:,.2f}   "
                     f"reward Rs {plan['reward_per_unit']:,.2f} a unit")
    else:
        lines.append(f"  target  premium Rs {plan['target_premium']:,.2f}   reward Rs {plan['reward_per_unit']:,.2f} a unit")
    if maths:
        lines.append(f"  one lot ({maths['units']} units): risk Rs {maths['net_risk']:,.0f}, reward "
                     f"Rs {maths['net_reward']:,.0f}, charges Rs {maths['charges_round_trip']:.0f} round trip, "
                     f"R:R {maths['reward_risk']}")
        lines.append(f"  premium outlay Rs {maths['premium_outlay']:,.0f}; flat by {plan['flat_by']} IST")
    for note in plan.get("notes") or []:
        lines.append(f"  note: {note}")
    if plan.get("problems"):
        lines.append("  REFUSED:")
        for problem in plan["problems"]:
            lines.append(f"    - {problem}")
    return "\n".join(lines)


def main(argv=None):
    ap = cli.parser("Exit Planner - the stop and the target, before the trade")
    ap.add_argument("command", nargs="?", default="plan", choices=["plan", "show"])
    ap.add_argument("--stop", choices=["or-mid", "or-low", "premium-pct", "index-points", "atr"])
    ap.add_argument("--premium-pct", type=float, help="premium-pct stop: how far the premium may fall, in percent")
    ap.add_argument("--points", type=float, help="index-points stop distance")
    ap.add_argument("--atr-multiple", type=float, default=1.0)
    ap.add_argument("--target", help="1.5R, 2R, premium:150 or index:25200")
    ap.add_argument("--flat-by", help="HH:MM IST (default from config, 15:15)")
    ap.add_argument("--underlying", help="plan without entry.json")
    ap.add_argument("--right", choices=["CE", "PE"], help="call or put")
    ap.add_argument("--premium", type=float, help="the premium you are paying a unit")
    ap.add_argument("--spot", type=float, help="the index level now")
    ap.add_argument("--entry", type=float, help="the index level of the entry")
    ap.add_argument("--setup", default="orb")
    ap.add_argument("--lots", type=int, default=1, help="for the rupee maths (default 1)")
    ap.add_argument("--chain", default="auto", choices=["auto", "dhan", "nse", "bse", "model", "csv"])
    ap.add_argument("--chain-file")
    ap.add_argument("--write", action="store_true", help="save trader/plan.json")
    args = ap.parse_args(argv)

    config, now = cli.context(args)

    if args.command == "show":
        plan = store.read_json("plan.json", config, default=None)
        if not plan:
            cli.fail("no trader/plan.json yet; run: exit.py plan")
        print(render(plan))
        return 0

    info = load_contract(args, config, now)
    if info.get("premium") is None:
        cli.fail("no premium for this contract; pass --premium or use --chain-file")

    plan = build_plan(info, args, config, now)
    if args.write and not args.no_write:
        store.write_json("plan.json", plan, config)

    if args.json:
        cli.emit(args, plan, "")
    else:
        print(render(plan))
        if args.write and not args.no_write:
            print("\nWrote trader/plan.json")
    return 0 if plan["ok"] else 1


if __name__ == "__main__":
    sys.exit(cli.run(main))
