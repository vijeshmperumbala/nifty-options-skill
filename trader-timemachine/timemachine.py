#!/usr/bin/env python3
"""Time Machine - replay the rest of the session, in premium, before you risk a rupee.

    python3 timemachine.py run                 # from trader/plan.json, 10,000 replays
    python3 timemachine.py run --write
    python3 timemachine.py run --replays 4000 --seed 19
    python3 timemachine.py run --until 15:15

Two questions, two answers:

  * 10,000 simulated sessions. Each 5-minute bar of the rest of the day is
    drawn from the index's own bars at that time of day over the last 60
    sessions. At every bar the OPTION IS REPRICED with Black-Scholes, so theta
    burns the premium and the index move is translated into rupees by the
    option's own greeks. A bar that touches both the stop and the target counts
    as the stop.
  * The history. The same rule (same opening range, same stop rule, same R) is
    run again on each of those past sessions, with the same repricing model.

It is a simulation built from the index's own recent bars. It is not a
prediction: IV can crush, the exchange can halt, and news does what news does.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import random
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib")))

from indkit import bs, cli, clock, feed, instruments, store, workflow  # noqa: E402


def slot_of(moment: dt.datetime) -> int:
    """Minutes from the 09:15 open, in 5-minute steps (0-74)."""
    start = dt.datetime.combine(moment.date(), clock.SESSION_START, clock.IST)
    return int((moment - start).total_seconds() // 300)


def slot_time(day: dt.date, slot: int) -> dt.datetime:
    return dt.datetime.combine(day, clock.SESSION_START, clock.IST) + dt.timedelta(minutes=5 * slot)


def build_pool(symbol, before: dt.date):
    """Full sessions of 5-minute bars, indexed by time-of-day slot."""
    bars = feed.bars(symbol, "5m", "60d", complete_only=False)
    sessions = {}
    for bar in bars:
        sessions.setdefault(bar.time.date(), []).append(bar)
    pool = {}
    used = 0
    for day, rows in sessions.items():
        if day >= before:
            continue
        rows.sort(key=lambda b: b.time)
        if len(rows) < 70:
            continue
        used += 1
        previous_close = rows[0].open
        for bar in rows:
            slot = slot_of(bar.time)
            if previous_close <= 0:
                previous_close = bar.close
                continue
            pool.setdefault(slot, []).append({
                "o": bar.open / previous_close,
                "h": bar.high / previous_close,
                "l": bar.low / previous_close,
                "c": bar.close / previous_close,
            })
            previous_close = bar.close
    return pool, used


def _premium(level, plan, at, config):
    years = bs.years_to_expiry(at, dt.date.fromisoformat(plan["expiry"]))
    return bs.price(level, float(plan["strike"]), years,
                    float(config.get("risk_free_rate", 0.065)), float(plan.get("iv") or 0.15),
                    plan["right"], float(config.get("dividend_yield", 0.012)))


def replay_once(pool, plan, config, entry_slot, end_slot, entry_index, rng, window=2):
    """One simulated session. Returns (outcome, r, premium_at_end, index_at_end)."""
    level = float(entry_index)
    day = dt.date.fromisoformat(plan["date"]) if plan.get("date") else clock.now_ist().date()
    entry_premium = float(plan["entry_premium"])
    stop_premium = float(plan["stop_premium"])
    target_premium = float(plan["target_premium"])
    risk = entry_premium - stop_premium
    call = plan["right"] == "CE"

    for slot in range(entry_slot + 1, end_slot + 1):
        source_slot = max(0, min(74, slot + rng.randint(-window, window)))
        candidates = pool.get(source_slot)
        if not candidates:
            continue
        pick = candidates[rng.randrange(len(candidates))]
        high = level * pick["h"]
        low = level * pick["l"]
        close = level * pick["c"]
        at = slot_time(day, slot)

        adverse_level = low if call else high
        favourable_level = high if call else low
        adverse = _premium(adverse_level, plan, at, config)
        favourable = _premium(favourable_level, plan, at, config)
        if adverse <= stop_premium:
            return "stop", -1.0, stop_premium, adverse_level
        if favourable >= target_premium:
            return "target", (target_premium - entry_premium) / risk if risk else 0.0, target_premium, favourable_level
        level = close

    at = slot_time(day, end_slot)
    final = _premium(level, plan, at, config)
    return "neither", ((final - entry_premium) / risk if risk else 0.0), final, level


def history(plan, config, sessions=60, window=None):
    """The same rule, on the index's own past sessions (model repricing, stated)."""
    symbol = plan.get("spot_symbol") or instruments.UNDERLYINGS[plan["underlying"]]["spot"]
    bars = feed.bars(symbol, "5m", "60d", complete_only=False)
    grouped = {}
    for bar in bars:
        grouped.setdefault(bar.time.date(), []).append(bar)
    today = dt.date.fromisoformat(plan["date"]) if plan.get("date") else clock.now_ist().date()
    open_slot = slot_of(slot_time(today, 0))
    or_slots = int(config.get("opening_range_minutes", 15)) // 5
    end_slot = _end_slot(plan, config)
    results = {"fired": 0, "target": 0, "stop": 0, "neither": 0, "r_sum": 0.0}
    risks = []
    for day, rows in sorted(grouped.items()):
        if day >= today or len(rows) < 70:
            continue
        rows.sort(key=lambda b: b.time)
        rng_range = feed.opening_range(rows, int(config.get("opening_range_minutes", 15)), "5m")
        if not rng_range:
            continue
        entry = None
        for bar in rows:
            slot = slot_of(bar.time)
            if slot < or_slots:
                continue
            if bar.close > rng_range["high"]:
                entry = (bar, "CE")
                break
            if bar.close < rng_range["low"]:
                entry = (bar, "PE")
                break
        if not entry:
            continue
        bar, right = entry
        results["fired"] += 1
        entry_slot = slot_of(bar.time)
        step = float(plan.get("strike_step") or 50)
        strike = round(bar.close / step) * step
        expiry = day + dt.timedelta(days=int(plan.get("days_to_expiry") or 7))
        base = {
            "strike": strike, "right": right, "expiry": expiry.isoformat(),
            "entry_premium": 0.0, "stop_premium": 0.0, "target_premium": 0.0,
        }
        at_entry = slot_time(day, entry_slot)
        entry_premium = _premium(bar.close, base, at_entry, config)
        if entry_premium <= 0:
            continue
        # Like for like: each past session uses the PLAN's premium-risk ratio, so the
        # history compares the same amount of risk, not a coincidentally tight range.
        ratio = float(plan.get("risk_per_unit") or 0) / float(plan["entry_premium"] or 1)
        ratio = min(max(ratio, 0.02), 0.95)
        stop_premium = max(0.05, round(entry_premium * (1.0 - ratio), 2))
        risk = entry_premium - stop_premium
        if risk <= 0:
            continue
        target_premium = entry_premium + float(plan.get("target_r") or 1.5) * risk
        base.update({"entry_premium": entry_premium, "stop_premium": stop_premium,
                     "target_premium": target_premium})
        risks.append(ratio_value(entry_premium, plan))
        outcome = "neither"
        level = bar.close
        for nxt in [r for r in rows if slot_of(r.time) > entry_slot]:
            slot = slot_of(nxt.time)
            if slot > end_slot:
                break
            at = slot_time(day, slot)
            adverse = _premium(nxt.low if right == "CE" else nxt.high, base, at, config)
            favourable = _premium(nxt.high if right == "CE" else nxt.low, base, at, config)
            if adverse <= stop_premium:
                outcome = "stop"
                break
            if favourable >= target_premium:
                outcome = "target"
                break
            level = nxt.close
        results[outcome] += 1
        if outcome == "target":
            results["r_sum"] += float(plan.get("target_r") or 1.5)
        elif outcome == "stop":
            results["r_sum"] -= 1.0
        else:
            final = _premium(level, base, slot_time(day, end_slot), config)
            results["r_sum"] += (final - entry_premium) / risk
    if results["fired"]:
        results["avg_r"] = round(results["r_sum"] / results["fired"], 3)
        results["hit_rate"] = round(100.0 * results["target"] / results["fired"], 1)
        risks.sort()
        results["median_risk_pct"] = round(100.0 * risks[len(risks) // 2], 1)
        results["basis"] = ("the same opening-range entry on each past session, with the plan's own "
                            f"{results['median_risk_pct']}% premium stop and {plan.get('target_r')}R target")
    return results


def ratio_value(entry_premium, plan):
    ratio = float(plan.get("risk_per_unit") or 0) / float(plan["entry_premium"] or 1)
    return min(max(ratio, 0.02), 0.95)


def _end_slot(plan, config):
    flat = plan.get("flat_by") or config.get("flat_by", "15:15")
    try:
        moment = dt.datetime.strptime(str(flat), "%H:%M").time()
    except ValueError:
        moment = dt.time(15, 15)
    return max(1, min(75, int((moment.hour * 60 + moment.minute - 555) // 5)))


def verdict(mc, plan):
    """One honest line, weighted by what the target is worth."""
    target_r = float(plan.get("target_r") or 1.5)
    resolved = mc["target_first"] + mc["stop_first"]
    if resolved <= 0:
        return "No replay resolved: the odds cannot be read from this run."
    share = 100.0 * mc["target_first"] / resolved
    breakeven = 100.0 / (1.0 + target_r)
    gap = share - breakeven
    label = f"Close to a coin flip" if target_r == 1.0 else "Close to break-even"
    if abs(gap) < 5:
        line = f"{label}: the target came first in {share:.1f}% of the resolved replays, break-even needs {breakeven:.0f}%."
    elif abs(gap) < 15:
        lean = "toward the target" if gap > 0 else "toward the stop"
        line = f"Leans {lean}: target first {share:.1f}% against a {breakeven:.0f}% break-even."
    else:
        lean = "strongly toward the target" if gap > 0 else "strongly toward the stop"
        line = f"{lean.capitalize()}: target first {share:.1f}% against a {breakeven:.0f}% break-even."
    return line


def load_plan(args, config, now):
    if any([args.entry, args.stop, args.target]):
        if not all([args.entry, args.stop, args.target]):
            cli.fail("give --entry, --stop and --target together, or use trader/plan.json")
        underlying = (args.underlying or config.get("underlying", "NIFTY")).upper()
        master = instruments.Master.load(config, now=now)
        expiry = args.expiry or workflow.tradable_expiry(master, underlying, now, config)
        step = master.step(underlying, expiry)
        spot = args.spot or workflow.spot_for(underlying, now)
        strike = args.strike or round(spot / step) * step
        return {
            "underlying": underlying, "right": args.right or "CE", "strike": strike,
            "strike_step": step, "expiry": expiry.isoformat(),
            "entry_index": args.spot or spot, "entry_premium": args.entry,
            "stop_premium": args.stop, "target_premium": args.target,
            "target_r": round((args.target - args.entry) / max(1e-9, args.entry - args.stop), 2),
            "stop_rule": args.stop_rule or "or-mid", "iv": args.iv or 0.15,
            "days_to_expiry": (expiry - clock.now_ist(now).date()).days,
            "flat_by": args.until or config.get("flat_by", "15:15"),
            "date": clock.now_ist(now).date().isoformat(),
        }
    plan = store.read_json("plan.json", config, default=None)
    if not plan:
        cli.fail("no trader/plan.json; run /trader-exit first, or pass --entry/--stop/--target")
    return plan


def main(argv=None):
    ap = cli.parser("Time Machine - 10,000 replays of the rest of the session, in premium")
    ap.add_argument("command", nargs="?", default="run", choices=["run"])
    ap.add_argument("--replays", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--window", type=int, default=2, help="how many slots either side to draw from")
    ap.add_argument("--until", help="end each replay at this IST time (default: the plan's flat-by)")
    ap.add_argument("--symbol", help="index symbol (default: from the plan)")
    ap.add_argument("--date", help="the session being replayed (default: today)")
    ap.add_argument("--underlying")
    ap.add_argument("--right", choices=["CE", "PE"])
    ap.add_argument("--strike", type=float)
    ap.add_argument("--expiry")
    ap.add_argument("--iv", type=float)
    ap.add_argument("--spot", type=float)
    ap.add_argument("--entry", type=float, help="entry premium")
    ap.add_argument("--stop", type=float, help="stop premium")
    ap.add_argument("--target", type=float, help="target premium")
    ap.add_argument("--stop-rule")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    config, now = cli.context(args)
    plan = load_plan(args, config, now)
    if args.until:
        plan["flat_by"] = args.until
    day = dt.date.fromisoformat(args.date) if args.date else clock.now_ist(now).date()
    plan["date"] = day.isoformat()
    plan.setdefault("spot_symbol", instruments.UNDERLYINGS[plan["underlying"]]["spot"])

    entry_slot = slot_of(clock.now_ist(now)) if clock.now_ist(now).date() == day else 0
    if plan.get("signal_time"):
        try:
            entry_slot = slot_of(dt.datetime.fromisoformat(str(plan["signal_time"])))
        except ValueError:
            pass
    end_slot = _end_slot(plan, config)
    if end_slot <= entry_slot:
        cli.fail(f"the flat-by time is at or before the entry bar (slot {entry_slot}); nothing to replay")

    pool, sessions_used = build_pool(plan["spot_symbol"], day)
    if sessions_used < 20:
        cli.fail(f"only {sessions_used} full past sessions of 5-minute bars; 20 are needed for a fair pool")

    rng = random.Random(args.seed)
    outcomes = {"target": 0, "stop": 0, "neither": 0}
    r_values, finals, levels = [], [], []
    for _ in range(args.replays):
        outcome, r, premium, level = replay_once(pool, plan, config, entry_slot, end_slot,
                                                 plan["entry_index"], rng, args.window)
        outcomes[outcome] += 1
        r_values.append(r)
        finals.append(premium)
        levels.append(level)

    total = max(1, args.replays)
    mc = {
        "replays": total,
        "seed": args.seed,
        "window": args.window,
        "session": day.isoformat(),
        "entry_slot": entry_slot,
        "end_slot": end_slot,
        "from": slot_time(day, entry_slot).strftime("%H:%M"),
        "to": slot_time(day, end_slot).strftime("%H:%M"),
        "sessions_in_pool": sessions_used,
        "target_first": round(100.0 * outcomes["target"] / total, 1),
        "stop_first": round(100.0 * outcomes["stop"] / total, 1),
        "neither": round(100.0 * outcomes["neither"] / total, 1),
        "avg_r": round(sum(r_values) / total, 3),
    }
    green = sum(1 for r in r_values if r > 0) + outcomes["target"]
    mc["green"] = round(min(100.0, 100.0 * green / total), 1)
    finals_sorted = sorted(finals)
    levels_sorted = sorted(levels)
    mc["premium_percentiles"] = {
        "p5": round(finals_sorted[int(0.05 * (len(finals_sorted) - 1))], 2),
        "p50": round(finals_sorted[len(finals_sorted) // 2], 2),
        "p95": round(finals_sorted[int(0.95 * (len(finals_sorted) - 1))], 2),
    }
    mc["index_percentiles"] = {
        "p5": round(levels_sorted[int(0.05 * (len(levels_sorted) - 1))], 2),
        "p50": round(levels_sorted[len(levels_sorted) // 2], 2),
        "p95": round(levels_sorted[int(0.95 * (len(levels_sorted) - 1))], 2),
    }

    hist = history(plan, config)
    result = store.stamp({
        "plan": plan, "mc": mc, "history": hist,
        "verdict": verdict(mc, plan),
        "disclaimer": ("simulated from the index's own last 60 sessions; not a prediction, "
                       "and IV crush, halts and news can do things those sessions never did"),
    }, now)

    if args.write and not args.no_write:
        store.write_json("timemachine.json", result, config)

    if args.json:
        cli.emit(args, result, "")
        return 0

    print(f"{args.replays:,} replays of the rest of the session, {mc['from']} to {mc['to']} IST "
          f"({end_slot - entry_slot} five-minute bars), seed {args.seed}.")
    print(f"  pool: {sessions_used} full past sessions of {plan['underlying']}, drawn bar by bar at the same time of day")
    print(f"  option repriced every bar: strike {plan['strike']:,.0f} {plan['right']}, IV {float(plan.get('iv') or 0)*100:.1f}%, "
          f"theta burning to {plan['expiry']}")
    print()
    print(f"  target first   {mc['target_first']:5.1f} %")
    print(f"  stop first     {mc['stop_first']:5.1f} %")
    print(f"  neither        {mc['neither']:5.1f} %   still open at {mc['to']}")
    print(f"  green          {mc['green']:5.1f} %   target first, or above the entry premium at {mc['to']}")
    print(f"  Premium at {mc['to']}: median Rs {mc['premium_percentiles']['p50']:,.2f}, "
          f"90% between Rs {mc['premium_percentiles']['p5']:,.2f} and Rs {mc['premium_percentiles']['p95']:,.2f}")
    print(f"  Index at {mc['to']}: median {mc['index_percentiles']['p50']:,.2f}, "
          f"90% between {mc['index_percentiles']['p5']:,.2f} and {mc['index_percentiles']['p95']:,.2f}")
    print(f"  Average R: {mc['avg_r']:+.3f}")
    print()
    if hist.get("fired"):
        print(f"History: the same rule fired on {hist['fired']} of those past sessions: "
              f"{hist['target']} target, {hist['stop']} stop, {hist['neither']} neither. "
              f"Average R {hist.get('avg_r', 0):+.3f}.")
        if hist.get("basis"):
            print(f"         {hist['basis']}")
    else:
        print("History: the same rule never fired on those past sessions.")
    print(result["verdict"])
    if mc["neither"] > 40:
        print(f"{mc['neither']:.1f}% of the replays hit neither: the premium at {mc['to']} decides those.")
    print()
    print(f"Simulation, not a prediction. Seed {args.seed}, so anyone can repeat it.")
    if args.write and not args.no_write:
        print("Wrote trader/timemachine.json")
    return 0


if __name__ == "__main__":
    sys.exit(cli.run(main))
