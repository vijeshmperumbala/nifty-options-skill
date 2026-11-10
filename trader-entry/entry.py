#!/usr/bin/env python3
"""Entry Timer - the bar that confirms the index setup, and the option it implies.

    python3 entry.py check NIFTY                 # 15-minute opening range, 5-minute bars
    python3 entry.py check NIFTY --write         # also save trader/entry.json
    python3 entry.py check NIFTY --setup level --level 25100
    python3 entry.py check NIFTY --strike delta --interval 1m
    python3 entry.py watch NIFTY --until 14:30   # one line per closed bar
    python3 entry.py watchlist                   # every symbol in trader/watchlist.json

The signal is a CLOSED bar beyond the level, never a wick. It says what
happened and what option it points at; it never places anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time as _time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib")))

from indkit import cli, clock, feed, instruments, store, workflow  # noqa: E402


def check_one(underlying, args, config, now, quiet=False):
    """One status line per index: waiting, triggered, expired or closed."""
    moment = clock.now_ist(now)
    phase = clock.phase(moment, config)
    setup = args.setup or "orb"
    interval = args.interval or "5m"
    result = {
        "underlying": underlying.upper(),
        "setup": setup,
        "status": "unknown",
        "at": moment.isoformat(timespec="seconds"),
        "interval": interval,
    }

    if not phase["is_trading_day"]:
        result.update(status="market-closed", reason=f"market closed ({phase['holiday'] or 'weekend'})")
        return result
    if phase["state"] == "preopen":
        result.update(status="not-open", reason="pre-open call auction; the session starts at 09:15")
        return result
    if phase["state"] in ("closed", "post"):
        result.update(status="closed", reason="the session is over; run this during market hours")
        return result

    or_minutes = int(config.get("opening_range_minutes", 15))
    symbol = instruments.UNDERLYINGS[underlying.upper()]["spot"]
    bars = feed.session_bars(symbol, now=moment, interval=interval)
    if not bars:
        result.update(status="no-data", reason="no completed bars for today yet")
        return result

    if setup == "level":
        if args.level is None:
            result.update(status="error", reason="--setup level needs --level PRICE")
            return result
        level = float(args.level)
        after = [b for b in bars if b.time.time() >= clock.opening_range_end(or_minutes)]
        signal = {"status": "waiting", "range": None, "last_close": bars[-1].close,
                  "at": bars[-1].time, "to_high": round(level - bars[-1].close, 2),
                  "to_low": round(bars[-1].close - level, 2),
                  "reason": f"waiting for a close beyond {level:,.2f}"}
        for bar in after:
            if bar.close > level:
                signal = {"status": "triggered", "direction": "CE", "level": level, "price": bar.close,
                          "at": bar.time, "why": f"closed above the level {level:,.2f}"}
                break
            if bar.close < level:
                signal = {"status": "triggered", "direction": "PE", "level": level, "price": bar.close,
                          "at": bar.time, "why": f"closed below the level {level:,.2f}"}
                break
        range_info = feed.opening_range(bars, or_minutes, interval)
    else:
        cutoff = phase["no_entries_after"] if not args.at else _time_from(args.at)
        signal = workflow.orb_signal(bars, or_minutes, interval, cutoff=cutoff, now=moment)
        range_info = signal.get("range")

    result.update({
        "range": range_info,
        "last_close": signal.get("last_close"),
        "level": signal.get("level"),
        "direction": signal.get("direction"),
        "signal_time": signal.get("at").isoformat(timespec="seconds") if signal.get("at") else None,
        "reason": signal.get("reason") or signal.get("why"),
        "status": signal["status"],
    })

    if signal["status"] == "triggered":
        if phase["state"] == "open" and moment.time() > phase["no_entries_after"]:
            result["warning"] = (f"this trigger is older than the {phase['no_entries_after'].strftime('%H:%M')} "
                                 "entry cutoff; a late entry is a different trade")
        trigger_age = (moment - signal["at"]).total_seconds() / 60.0
        result["trigger_age_minutes"] = round(trigger_age, 1)

        spot = signal["price"]
        try:
            info = workflow.pick_contract(
                underlying, spot, config=config, now=moment, direction=signal["direction"],
                strike_mode=args.strike, offset=args.offset, chain_source=args.chain,
                chain_path=args.chain_file)
            result["contract"] = info
            refusal = workflow.refusal_reason(info, config)
            if refusal:
                result["refusal"] = refusal
        except Exception as exc:                                   # noqa: BLE001
            result["contract_error"] = str(exc)

        # expiry-day guard
        active = instruments.Master.load(config, now=moment)
        expiry = workflow.tradable_expiry(active, underlying.upper(), moment, config)
        result["expiry"] = expiry.isoformat() if expiry else None
        if expiry == moment.date():
            skip_after = _time_from(config.get("skip_expiry_day_after", "14:00"))
            if moment.time() >= skip_after:
                result["refusal"] = (f"expiry day: no new entries after {skip_after.strftime('%H:%M')} "
                                     "on the contract that settles today")
        result["vix"] = _vix(moment)
    return result


def _vix(now):
    try:
        return feed.india_vix(now)
    except Exception:                                              # noqa: BLE001
        return None


def _time_from(text):
    try:
        return dt.datetime.strptime(str(text), "%H:%M").time()
    except (ValueError, TypeError):
        return None


def render(result, config):
    """The human answer: one line of status, then the detail that matters."""
    lines = []
    label = result["underlying"]
    status = result["status"]
    if status in ("market-closed", "closed", "not-open", "no-data", "error"):
        return f"{label:<10} {status.upper():<12} {result.get('reason', '')}"

    if status in ("waiting", "range-forming"):
        result.setdefault("distance", "")
        if result.get("to_high") is not None:
            result["distance"] = (f"{abs(result['to_high']):,.2f} under the range high, "
                                  f"{abs(result.get('to_low', 0)):,.2f} over the range low")
        rng = result.get("range") or {}
        spot = result.get("last_close") or 0
        span = f"{rng.get('low', 0):,.2f} to {rng.get('high', 0):,.2f}" if rng else "not formed yet"
        return (f"{label:<10} WAITING      last close {spot:,.2f}; opening range {span}; "
                f"{result.get('distance', '')}")

    signal_time = result.get("signal_time")
    if signal_time:
        stamp = dt.datetime.fromisoformat(signal_time).strftime("%H:%M")
    else:
        stamp = "-"
    contract = result.get("contract") or {}
    lines.append(f"{label:<10} TRIGGERED    {stamp} IST - {result.get('reason', '')}")
    if contract:
        side = "CALL" if contract["right"] == "CE" else "PUT"
        lines.append(f"           option: {contract['symbol']}  ({contract['name']} {contract['strike']:,.0f} {side}, "
                     f"{contract['series']} expiry {contract['expiry'].strftime('%d %b %Y').lstrip('0')}, "
                     f"lot {contract['lot']}, {contract['days_to_expiry']}d)")
        premium = contract.get("premium")
        if premium is not None:
            lines.append(f"           premium: Rs {premium:,.2f} x {contract['lot']} = "
                         f"Rs {premium * contract['lot']:,.0f} a lot - {workflow.describe_premium_source(contract)}")
        lines.append(f"           greeks: delta {contract['delta']:+.2f}, gamma {contract['gamma']:.4f}, "
                     f"theta {contract['theta']:+.2f}/day, IV {contract['iv']*100:.1f}%")
        if contract.get("spread_pct") is not None:
            lines.append(f"           spread: {contract['spread_pct']:.2f}% of the premium")
    if result.get("warning"):
        lines.append(f"           ! {result['warning']}")
    if result.get("refusal"):
        lines.append(f"           BLOCKED: {result['refusal']}")
    if result.get("vix"):
        vix = result["vix"]
        lines.append(f"           India VIX {vix['value']:.2f} ({vix['regime']})")
    lines.append("           Nothing was placed. Set the stop and target BEFORE you buy: /trader-exit")
    return "\n".join(lines)


def main(argv=None):
    ap = cli.parser("Entry Timer - the confirming bar, and the option it implies")
    ap.add_argument("command", choices=["check", "watch", "watchlist"])
    ap.add_argument("symbol", nargs="?", help="NIFTY, BANKNIFTY, SENSEX")
    ap.add_argument("--setup", default="orb", choices=["orb", "level"])
    ap.add_argument("--level", type=float, help="setup=level: the price to beat")
    ap.add_argument("--interval", default="5m", choices=["1m", "5m"])
    ap.add_argument("--strike", default=None, choices=["atm", "delta"], help="how to pick the strike")
    ap.add_argument("--offset", type=int, default=0, help="strike steps out of the money (ATM mode)")
    ap.add_argument("--chain", default="auto", choices=["auto", "dhan", "nse", "bse", "model", "csv"])
    ap.add_argument("--chain-file", help="a chain CSV/JSON you exported")
    ap.add_argument("--until", default=None, help="watch: stop at this IST time (HH:MM)")
    ap.add_argument("--every", type=int, default=20, help="watch: seconds between checks")
    ap.add_argument("--at", help="cut the opening range at this IST time (HH:MM)")
    ap.add_argument("--write", action="store_true", help="save trader/entry.json")
    args = ap.parse_args(argv)

    config, now = cli.context(args)

    if args.command == "watchlist":
        watchlist = store.read_json("watchlist.json", config, default={}) or {}
        symbols = [h["underlying"] for h in watchlist.get("hits", [])]
        if not symbols:
            cli.fail("trader/watchlist.json has no hits; run /trader-scanner first")
        seen, results = set(), []
        for symbol in symbols:
            if symbol in seen:
                continue
            seen.add(symbol)
            results.append(check_one(symbol, args, config, now))
        if args.json:
            cli.emit(args, {"results": results}, "")
        else:
            for result in results:
                print(render(result, config))
        return 0 if any(r["status"] == "triggered" for r in results) else 1

    symbol = (args.symbol or config.get("underlying") or "NIFTY").upper()
    if symbol not in instruments.UNDERLYINGS:
        cli.fail(f"{symbol}: not an index this pack knows ({', '.join(sorted(instruments.UNDERLYINGS))})")

    if args.command == "watch":
        until = _time_from(args.until or config.get("no_entries_after", "14:30"))
        return _watch(symbol, args, config, until)

    result = check_one(symbol, args, config, now)
    if args.write and result["status"] == "triggered" and not args.no_write:
        store.write_json("entry.json", store.stamp(result, now), config)
    if args.json:
        cli.emit(args, result, "")
        return 0 if result["status"] == "triggered" else 1
    print(render(result, config))
    if args.write and result["status"] == "triggered" and not args.no_write:
        print("Wrote trader/entry.json")
    return 0 if result["status"] == "triggered" else 1


def _watch(symbol, args, config, until):
    """Bar by bar. Replays instantly when the clock is pinned, polls otherwise."""
    replay = bool(args.now or args.offline)
    deadline = until or dt.time(15, 0)
    printed = set()
    while True:
        now = clock.now_ist(args.now) if args.now else clock.now_ist()
        if now.time() >= deadline:
            print(f"{symbol}: watch ended at {deadline.strftime('%H:%M')} IST with no trigger.")
            return 1
        result = check_one(symbol, args, config, now)
        key = (result["status"], result.get("signal_time"))
        if key not in printed:
            printed.add(key)
            print(f"[{now.strftime('%H:%M:%S')}] {render(result, config)}")
        if result["status"] == "triggered":
            if not args.no_write:
                store.write_json("entry.json", store.stamp(result, now), config)
                print("Wrote trader/entry.json")
            return 0
        if result["status"] in ("market-closed", "closed"):
            return 1
        if replay:
            args.now = (now + dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
            continue
        _time.sleep(max(5, args.every))


if __name__ == "__main__":
    sys.exit(cli.run(main))
