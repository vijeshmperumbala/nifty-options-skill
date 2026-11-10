#!/usr/bin/env python3
"""Options Analyst - what the chain, the VIX and the calendar are saying.

    python3 analyst.py chain                    # NIFTY chain: PCR, max pain, expected move
    python3 analyst.py chain --underlying SENSEX
    python3 analyst.py chain --chain-file my_chain.csv
    python3 analyst.py vix
    python3 analyst.py calendar

Which contract is expensive, where the open interest sits, how far the market
thinks the index can travel this expiry, and when the next expiry/holiday is.
It reports. It never says buy or sell, and when a number needs real open
interest it says so instead of inventing one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib")))

from indkit import chain as chain_mod, cli, clock, feed, indicators, instruments, rules, store, workflow  # noqa: E402


def chain_report(underlying, args, config, now):
    master = instruments.Master.load(config, now=now)
    expiry = _expiry(args, master, underlying, now, config)
    spot = args.spot or workflow.spot_for(underlying, now)
    chain = chain_mod.load(underlying, spot=spot, expiry=expiry, config=config, now=now,
                           source=args.chain, path=args.chain_file, lot=master.lot(underlying, expiry))
    lot = master.lot(underlying, expiry)
    atm = chain.nearest_strike(spot)
    report = {
        "underlying": underlying,
        "exchange": instruments.UNDERLYINGS[underlying]["exchange"],
        "spot": round(spot, 2),
        "expiry": expiry.isoformat(),
        "series": master.series_kind(underlying, expiry),
        "days_to_expiry": (expiry - clock.now_ist(now).date()).days,
        "lot": lot,
        "source": chain.source,
        "real": chain.real,
        "strikes": len(chain.rows),
        "atm": atm,
        "atm_iv": round(chain.atm_iv(now), 4),
        "expected_move": chain.expected_move(now),
        "warnings": [],
    }
    try:
        report["straddle"] = chain.straddle(now)
    except Exception as exc:                                       # noqa: BLE001
        report["warnings"].append(f"straddle: {exc}")
    for key, call in (("pcr", chain.pcr), ("max_pain", chain.max_pain),
                      ("oi_walls", chain.oi_walls), ("skew", chain.skew)):
        try:
            value = call()
            report[key] = value if not isinstance(value, float) else round(value, 2)
        except Exception as exc:                                   # noqa: BLE001
            report[key] = None
            report["warnings"].append(f"{key}: {exc}")
    ladder = []
    step = chain.step()
    for strike in [atm + i * step for i in range(-4, 5)]:
        if strike not in chain.rows:
            continue
        call = chain.leg(strike, "CE")
        put = chain.leg(strike, "PE")
        ladder.append({
            "strike": strike,
            "call": chain.premium(strike, "CE"),
            "call_oi": call.get("oi"),
            "call_iv": round(float(call["iv"]) * 100, 1) if call.get("iv") else None,
            "put": chain.premium(strike, "PE"),
            "put_oi": put.get("oi"),
            "put_iv": round(float(put["iv"]) * 100, 1) if put.get("iv") else None,
        })
    report["ladder"] = ladder
    report["structure_note"] = rules.structure_note(underlying, master, expiry, now)
    return report


def _expiry(args, master, underlying, now, config):
    if args.expiry:
        return dt.date.fromisoformat(args.expiry)
    return workflow.tradable_expiry(master, underlying, now, config)


def vix_report(now):
    current = feed.india_vix(now)
    history = [b.close for b in feed.daily_bars("INDIAVIX", 90)]
    percentile = indicators.percentile_rank(history, current["value"]) if history else None
    report = dict(current)
    report.update({
        "percentile_60d": percentile,
        "low_60d": round(min(history), 2) if history else None,
        "high_60d": round(max(history), 2) if history else None,
        "one_sigma_daily_points_nifty": None,
    })
    try:
        spot = feed.quote("NIFTY")["price"]
        report["one_sigma_daily_points_nifty"] = round(spot * (current["value"] / 100.0) / (252 ** 0.5), 1)
    except Exception:                                              # noqa: BLE001
        pass
    return report


def calendar_report(config, now, count=4):
    master = instruments.Master.load(config, now=now)
    moment = clock.now_ist(now)
    out = {"as_of": moment.isoformat(timespec="seconds"), "underlyings": [], "holidays": []}
    for underlying in (config.get("underlyings") or ["NIFTY", "BANKNIFTY", "SENSEX"]):
        try:
            expiries = master.expiries(underlying, moment.date())[:count]
            out["underlyings"].append({
                "underlying": underlying,
                "lot": master.lot(underlying),
                "expiries": [{
                    "date": e.isoformat(),
                    "weekday": e.strftime("%A"),
                    "series": master.series_kind(underlying, e),
                    "days": (e - moment.date()).days,
                    "strike_step": master.step(underlying, e),
                } for e in expiries],
            })
        except Exception as exc:                                   # noqa: BLE001
            out["underlyings"].append({"underlying": underlying, "error": str(exc)})
    ahead = []
    for day in clock.trading_days(moment.date(), moment.date() + dt.timedelta(days=30)):
        name = clock.holiday_name(day)
        if name:
            ahead.append({"date": day.isoformat(), "name": name})
    out["holidays"] = ahead[:5]
    out["rules"] = {
        "weekly_expiry": rules.WEEKLY_EXPIRY_INDEX,
        "expiry_settlement": rules.settlement_rule(moment.date()),
        "min_contract_value": rules.MIN_INDEX_CONTRACT_VALUE,
        "short_premium_allowed_by_default": False,
        "stt_options_sell": "0.15% of premium (from 1 Apr 2026)",
    }
    out["notes"] = [rules.NOTES["weekly"], rules.NOTES["upfront_premium"], rules.NOTES["expiry_elm"]]
    return out


def main(argv=None):
    ap = cli.parser("Options Analyst - chain, VIX and calendar")
    ap.add_argument("command", nargs="?", default="chain", choices=["chain", "vix", "calendar"])
    ap.add_argument("--underlying", help="NIFTY, BANKNIFTY, SENSEX")
    ap.add_argument("--expiry", help="YYYY-MM-DD (default: the next tradable expiry)")
    ap.add_argument("--spot", type=float)
    ap.add_argument("--chain", default="auto", choices=["auto", "dhan", "nse", "bse", "model", "csv"])
    ap.add_argument("--chain-file")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    config, now = cli.context(args)

    if args.command == "vix":
        report = vix_report(now)
        if args.json:
            cli.emit(args, report, "")
            return 0
        print(f"India VIX {report['value']:.2f} ({report.get('change_pct', 0):+.2f}% today) - {report['regime']} regime")
        if report.get("percentile_60d") is not None:
            print(f"  60-session range {report['low_60d']:.2f} to {report['high_60d']:.2f}; "
                  f"today sits at the {report['percentile_60d']:.0f}th percentile")
        if report.get("one_sigma_daily_points_nifty"):
            print(f"  That is about a {report['one_sigma_daily_points_nifty']:,.0f}-point one-sigma day on the NIFTY.")
        print("  Higher VIX means richer premiums on both sides; it does not say which way the index goes.")
        return 0

    if args.command == "calendar":
        report = calendar_report(config, now)
        if args.json:
            cli.emit(args, report, "")
            return 0
        print(f"Expiry calendar - {clock.now_ist(now).strftime('%a %d %b %Y %H:%M')} IST")
        for item in report["underlyings"]:
            if "error" in item:
                print(f"  {item['underlying']}: {item['error']}")
                continue
            print(f"  {item['underlying']} (lot {item['lot']}):")
            for exp in item["expiries"]:
                print(f"     {exp['date']} {exp['weekday']:<9} {exp['series']:<8} "
                      f"in {exp['days']:>2}d, strikes every {exp['strike_step']:,.0f}")
        if report["holidays"]:
            print("  exchange holidays in the next 30 days:")
            for holiday in report["holidays"]:
                print(f"     {holiday['date']}  {holiday['name']}")
        print("\n  " + "\n  ".join(report["notes"]))
        return 0

    underlying = (args.underlying or config.get("underlying") or "NIFTY").upper()
    if underlying not in instruments.UNDERLYINGS:
        cli.fail(f"{underlying}: not an index this pack knows")
    report = chain_report(underlying, args, config, now)
    if args.write and not args.no_write:
        store.write_json("analyst.json", store.stamp(report, now), config)

    if args.json:
        cli.emit(args, report, "")
        return 0

    label = {"model": "MODELLED - no open interest, no bid/ask", "nse": "NSE live chain",
             "bse": "BSE live chain", "dhan": "Dhan live chain - OI, IV, greeks, bid/ask",
             "csv": "imported chain"}.get(report["source"], report["source"])
    print(f"{report['underlying']} chain - {report['expiry']} ({report['series']}, "
          f"{report['days_to_expiry']}d), spot {report['spot']:,.2f}, lot {report['lot']}")
    print(f"  source: {label}")
    em = report["expected_move"]
    print(f"  ATM {report['atm']:,.0f}, IV {report['atm_iv']*100:.1f}%, "
          f"one-sigma move to expiry +/- {em['points']:,.0f} points ({em['pct']:.2f}%)")
    if report.get("straddle"):
        straddle = report["straddle"]
        print(f"  ATM straddle Rs {straddle['total']:,.2f} (call {straddle['call']:,.2f} + put "
              f"{straddle['put']:,.2f}), break-even move +/- {straddle['expected_move']:,.0f} points")
    print()
    headers = ["strike", "call", "call OI", "call IV%", "put", "put OI", "put IV%"]
    rows = []
    for row in report["ladder"]:
        rows.append([f"{row['strike']:,.0f}", _m(row["call"]), _oi(row["call_oi"]),
                     _pct(row["call_iv"]), _m(row["put"]), _oi(row["put_oi"]), _pct(row["put_iv"])])
    print(cli.table(headers, rows, aligns=["<", ">", ">", ">", ">", ">", ">"]))
    print()
    if report.get("pcr"):
        pcr = report["pcr"]
        print(f"  PCR (OI) {pcr['ratio']:.2f} - {pcr['bias']} "
              f"(puts {pcr['puts']:,.0f} vs calls {pcr['calls']:,.0f})")
    if report.get("max_pain"):
        print(f"  max pain {report['max_pain']:,.0f} "
              f"({report['max_pain'] - report['spot']:+,.0f} from spot)")
    if report.get("oi_walls"):
        walls = report["oi_walls"]
        print(f"  call OI walls {', '.join(f'{k:,.0f}' for k in walls['call_walls'])}  |  "
              f"put OI walls {', '.join(f'{k:,.0f}' for k in walls['put_walls'])}")
    if report.get("skew"):
        skew = report["skew"]
        print(f"  skew {skew['skew']:+.3f} (put IV {skew['put_iv']*100:.1f}% vs call "
              f"{skew['call_iv']*100:.1f}%) - {skew['read']}")
    for warning in report["warnings"]:
        print(f"  note: {warning}")
    print()
    print(f"  {report['structure_note']}")
    if not report["real"]:
        print("  PCR, max pain and OI walls need a REAL chain. Export the chain from your terminal to a CSV "
              "and pass --chain-file, or run this where the NSE endpoint answers.")
    print("  Nothing here is a recommendation: it describes premiums and positioning, not direction.")
    if args.write and not args.no_write:
        print("  Wrote trader/analyst.json")
    return 0


def _m(value):
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "-"


def _oi(value):
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "-"


def _pct(value):
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "-"


if __name__ == "__main__":
    sys.exit(cli.run(main))
