#!/usr/bin/env python3
"""Budget Manager - how many LOTS fit your max loss, your capital and your rules.

    python3 size.py                 # from trader/plan.json
    python3 size.py --write         # -> trader/orders.json
    python3 size.py --lots 2        # ask for 2 lots and see what the maths says

Index options are sized in whole lots, and one NIFTY lot is 65 units, so the
premium outlay is much larger than the US share equivalent. This tool finds the
biggest whole-lot size whose loss AT THE STOP (charges included) still fits
max_loss_per_trade, then checks it against capital, the premium-outlay cap and
max_lots. When nothing fits it says so instead of rounding up.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib")))

from indkit import cli, clock, costs, store, workflow  # noqa: E402


def size_plan(plan, config, lots_requested=None):
    entry = float(plan["entry_premium"])
    stop = float(plan["stop_premium"])
    target = float(plan["target_premium"])
    lot = int(plan["lot"])
    risk_per_unit = entry - stop
    if risk_per_unit <= 0:
        raise SystemExit("the plan's stop is not below the entry premium; fix /trader-exit first")

    max_loss = float(config.get("max_loss_per_trade", 2000))
    capital = float(config.get("capital", 200000))
    outlay_cap = capital * float(config.get("max_premium_pct_of_capital", 0.25))

    # loss for one lot at the stop, charges included
    one_lot = costs.round_trip(entry, stop, 1, lot, plan.get("exchange") or "NSE",
                               config.get("charges"), on=clock.now_ist())
    loss_one = risk_per_unit * lot + one_lot["total"]
    by_risk = int(max_loss // loss_one) if loss_one else 0
    by_outlay = int(outlay_cap // (entry * lot)) if entry > 0 else 0
    by_cash = int(capital // (entry * lot)) if entry > 0 else 0
    by_cap = int(config.get("max_lots", 4))
    allowed = max(0, min(by_risk, by_outlay, by_cash, by_cap))
    lots = allowed if lots_requested in (None, 0) else int(lots_requested)

    maths = workflow.risk_math(entry, stop, target, lots, lot, plan.get("exchange") or "NSE",
                               clock.now_ist(), config)
    return {
        "lots": lots,
        "lots_allowed": allowed,
        "requested": lots_requested,
        "lot": lot,
        "units": lots * lot,
        "loss_one_lot": round(loss_one, 2),
        "limits": {
            "by_max_loss": by_risk,
            "by_premium_outlay": by_outlay,
            "by_cash": by_cash,
            "by_max_lots": by_cap,
            "max_loss": max_loss,
            "premium_outlay_cap": round(outlay_cap, 2),
            "capital": capital,
        },
        "maths": maths,
        "over_limit": lots > allowed,
    }


def build_orders(plan, sized, config):
    """The order set an Indian broker actually takes for a bought option."""
    symbol = plan["symbol"]
    exchange = plan.get("order_exchange") or ("NFO" if plan.get("exchange") == "NSE" else "BFO")
    qty = sized["units"]
    entry_order = {
        "kind": "entry",
        "symbol": symbol,
        "underlying": plan["underlying"],
        "exchange": exchange,
        "side": "buy",
        "qty": qty,
        "lots": sized["lots"],
        "lot_size": sized["lot"],
        "order_type": "market",
        "product": "NRML",
        "validity": "DAY",
        "premium_entry": plan["entry_premium"],
        "premium_stop": plan["stop_premium"],
        "premium_target": plan["target_premium"],
        "stop_index": plan.get("stop_index"),
        "target_index": plan.get("target_index"),
        "expiry": plan.get("expiry"),
        "right": plan.get("right"),
        "strike": plan.get("strike"),
        "risk": sized["maths"]["net_risk"],
        "reward": sized["maths"]["net_reward"],
        "reward_risk": sized["maths"]["reward_risk"],
    }
    exits = [
        {"kind": "exit", "reason": "stop", "symbol": symbol, "exchange": exchange, "side": "sell",
         "qty": qty, "order_type": "sl-m", "trigger_price": plan["stop_premium"],
         "product": "NRML", "validity": "DAY"},
        {"kind": "exit", "reason": "target", "symbol": symbol, "exchange": exchange, "side": "sell",
         "qty": qty, "order_type": "limit", "limit_price": plan["target_premium"],
         "product": "NRML", "validity": "DAY"},
        {"kind": "exit", "reason": "flat-by", "symbol": symbol, "exchange": exchange, "side": "sell",
         "qty": qty, "order_type": "market", "product": "NRML", "validity": "DAY",
         "at": plan.get("flat_by")},
    ]
    return {
        "as_of": clock.now_ist().isoformat(timespec="seconds"),
        "orders": [entry_order],
        "exits": exits,
        "plan_ref": {"symbol": symbol, "entry_premium": plan["entry_premium"],
                     "stop_premium": plan["stop_premium"], "target_premium": plan["target_premium"]},
        "note": ("Buy the option, then place the stop and the target as separate exit orders. "
                 "Most Indian brokers do not offer OCO on options except through GTT; on expiry day "
                 "the exchange squares off anyway."),
    }


def render(plan, sized):
    maths = sized["maths"]
    limits = sized["limits"]
    lines = [f"Sizing {plan['symbol']} ({plan['direction']}, lot {sized['lot']})",
             f"  entry Rs {plan['entry_premium']:,.2f}, stop Rs {plan['stop_premium']:,.2f}, "
             f"target Rs {plan['target_premium']:,.2f}",
             f"  one lot risks Rs {sized['loss_one_lot']:,.0f} at the stop, charges included",
             f"  limits: max loss Rs {limits['max_loss']:,.0f} -> {limits['by_max_loss']} lots | "
             f"premium outlay cap Rs {limits['premium_outlay_cap']:,.0f} -> {limits['by_premium_outlay']} lots | "
             f"cash -> {limits['by_cash']} | max_lots {limits['by_max_lots']}"]
    if sized["lots"] == 0:
        lines.append("  NOTHING FITS: not one lot fits your rules.")
        lines.append("    options: buy a cheaper strike further out of the money (it moves less in rupees, "
                     "so a lot risks less), use a tighter premium stop, trade SENSEX instead "
                     "(lot 20, a fifth of the NIFTY lot), or skip the trade.")
        lines.append("    One at-the-money NIFTY lot usually risks Rs 3,000-6,000 at a sensible stop - "
                     "that is the real floor on an index-option trade.")
        return "\n".join(lines)
    lines.append(f"  SIZE: {sized['lots']} lot(s) = {sized['units']} units, premium outlay "
                 f"Rs {maths['premium_outlay']:,.0f}")
    lines.append(f"  at the stop: Rs {maths['net_risk']:,.0f} | at the target: Rs {maths['net_reward']:,.0f} | "
                 f"R:R {maths['reward_risk']} after charges (Rs {maths['charges_round_trip']:,.0f} round trip)")
    if sized["over_limit"]:
        lines.append(f"  ! {sized['requested']} lots is over what your rules allow ({sized['lots_allowed']})")
    return "\n".join(lines)


def main(argv=None):
    ap = cli.parser("Budget Manager - lots from your max loss, not from appetite")
    ap.add_argument("--lots", type=int, default=None, help="force a size and see the verdict")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    config, now = cli.context(args)
    plan = store.read_json("plan.json", config, default=None)
    if not plan:
        cli.fail("no trader/plan.json; run /trader-exit first")
    if plan.get("problems"):
        cli.fail("the plan has problems; fix them in /trader-exit first: " + "; ".join(plan["problems"]))

    sized = size_plan(plan, config, args.lots)
    payload = store.stamp({"sizing": sized, "plan": plan}, now)

    if args.write and not args.no_write and sized["lots"] > 0:
        orders = build_orders(plan, sized, config)
        store.write_json("orders.json", orders, config)
        payload["orders"] = orders

    if args.json:
        cli.emit(args, payload, "")
    else:
        print(render(plan, sized))
        if args.write and not args.no_write and sized["lots"] > 0:
            print("\nWrote trader/orders.json - next: /trader-risk")
    return 0 if sized["lots"] > 0 else 1


if __name__ == "__main__":
    sys.exit(cli.run(main))
